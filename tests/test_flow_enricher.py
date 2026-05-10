"""Tests for FlowEnricher (graph + context walk + explanation storage).

LLM and embedding calls are mocked so the test runs offline. The real
integration was verified end-to-end during the kloc-intelligence-followups
session: 9/9 reference-project flows enriched against native Gemini.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

haystack = pytest.importorskip("haystack", reason="ai extras not installed")
from src.ai import pipelines  # ensure attribute lookup works for patch()
from src.ai.config import AIConfig, EmbeddingProviderConfig, LLMProviderConfig
from src.ai.flow_enricher import (
    FlowEnricher,
    FlowEnrichmentProgress,
    get_flow_enrichment_status,
)
from src.config import Neo4jConfig
from src.db.connection import Neo4jConnection
from src.db.flow_importer import (
    clear_flows,
    import_flow_edges,
    import_flow_nodes,
    load_symfony_kloc,
    parse_flows,
)
from src.db.query_runner import QueryRunner

from .conftest import requires_neo4j

REFERENCE_FIXTURE = Path(
    "/Users/michal/dev/ai/kloc/kloc-reference-project-php/.kloc/symfony-kloc.json"
)


def _make_config() -> AIConfig:
    return AIConfig(
        llm=LLMProviderConfig(api_key="test-key", model="test-llm"),
        embedding=EmbeddingProviderConfig(api_key="test-key", model="test-embed", dimension=8),
        project_root="/Users/michal/dev/ai/kloc/kloc-reference-project-php",
        project_name="test",
    )


@pytest.fixture(scope="module")
def loaded_with_flows(_loaded_database_conn):
    """Module-scoped fixture: reuse loaded test SoT + import the reference flows."""
    conn = _loaded_database_conn
    if not REFERENCE_FIXTURE.is_file():
        pytest.skip(f"Reference fixture not found: {REFERENCE_FIXTURE}")
    data = load_symfony_kloc(REFERENCE_FIXTURE)
    nodes, edges = parse_flows(data)
    clear_flows(conn)
    import_flow_nodes(conn, nodes)
    import_flow_edges(conn, edges)
    yield conn
    # cleanup explanations after module
    with conn.session() as s:
        s.run("MATCH (f:Flow) REMOVE f.explanation, f.explain_model, f.explain_at")


@requires_neo4j
def test_get_flow_enrichment_status_counts(loaded_with_flows):
    status = get_flow_enrichment_status(loaded_with_flows)
    assert status["total"] >= 5  # at least the http+message flows from reference
    assert status["enriched"] == 0
    assert status["pending"] == status["total"]


@requires_neo4j
def test_enrich_flow_writes_explanation_property(loaded_with_flows):
    """End-to-end with mocked LLM + embedder: explanation lands on the :Flow node."""
    runner = QueryRunner(loaded_with_flows)
    config = _make_config()
    enricher = FlowEnricher(runner, config)

    fake_pipe = MagicMock()
    embed_pipe = MagicMock()
    enricher._explain_pipeline = fake_pipe
    enricher._embed_pipeline = embed_pipe

    with patch(
        "src.ai.pipelines.run_explain_flow", return_value="Creates new orders for customers."
    ):
        flow_id = "flow:http:App\\Ui\\Rest\\Controller\\OrderController::create"
        result = enricher.enrich_flow(flow_id, force=True)

    assert "error" not in result, result
    assert result["flow_id"] == flow_id

    # Property persisted
    record = runner.execute_single(
        "MATCH (f:Flow {flow_id: $fid}) RETURN f.explanation AS expl, f.explain_model AS m",
        fid=flow_id,
    )
    assert record["expl"] == "Creates new orders for customers."
    assert record["m"] == "test-llm"

    # Embedder was invoked exactly once with the explanation as the document content
    assert embed_pipe.run.called
    docs = embed_pipe.run.call_args.args[0]["embedder"]["documents"]
    assert len(docs) == 1
    assert docs[0].content == "Creates new orders for customers."
    assert docs[0].meta["flow_id"] == flow_id
    assert docs[0].meta["kind"] == "Flow"


@requires_neo4j
def test_enrich_flow_unknown_id_returns_error(loaded_with_flows):
    runner = QueryRunner(loaded_with_flows)
    enricher = FlowEnricher(runner, _make_config())
    enricher._explain_pipeline = MagicMock()
    enricher._embed_pipeline = MagicMock()
    result = enricher.enrich_flow("flow:http:Nope::nope")
    assert "error" in result


@requires_neo4j
def test_enrich_flow_skips_when_already_enriched_without_force(loaded_with_flows):
    runner = QueryRunner(loaded_with_flows)
    config = _make_config()
    enricher = FlowEnricher(runner, config)
    enricher._explain_pipeline = MagicMock()
    enricher._embed_pipeline = MagicMock()

    flow_id = "flow:http:App\\Ui\\Rest\\Controller\\OrderController::get"
    runner.execute(
        "MATCH (f:Flow {flow_id: $fid}) SET f.explanation = 'pre-existing'",
        fid=flow_id,
    )
    result = enricher.enrich_flow(flow_id, force=False)
    assert result.get("skipped") is True
    assert not enricher._embed_pipeline.run.called


@requires_neo4j
def test_get_flow_enrichment_status_after_partial_enrichment(loaded_with_flows):
    """After setting explanation on one flow, status counts move."""
    with loaded_with_flows.session() as s:
        s.run("MATCH (f:Flow) REMOVE f.explanation")
        s.run(
            "MATCH (f:Flow {flow_id: $fid}) SET f.explanation = 'x'",
            fid="flow:http:App\\Ui\\Rest\\Controller\\CustomerController::get",
        )
    status = get_flow_enrichment_status(loaded_with_flows)
    assert status["enriched"] == 1
    assert status["pending"] == status["total"] - 1


def test_progress_dataclass_defaults():
    p = FlowEnrichmentProgress()
    assert p.total == 0
    assert p.processed == 0
    assert p.failed_flows == []
