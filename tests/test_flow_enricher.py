"""Tests for FlowEnricher (graph + context walk + explanation storage).

LLM and embedding calls are mocked so the test runs offline. The real
integration was verified end-to-end during the kloc-intelligence-followups
session: 9/9 reference-project flows enriched against native Gemini.
"""

import threading
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

from .conftest import REFERENCE_PROJECT_ROOT, requires_neo4j
from .conftest import REFERENCE_SYMFONY_KLOC as REFERENCE_FIXTURE


def _make_config() -> AIConfig:
    return AIConfig(
        llm=LLMProviderConfig(api_key="test-key", model="test-llm"),
        embedding=EmbeddingProviderConfig(api_key="test-key", model="test-embed", dimension=8),
        project_root=str(REFERENCE_PROJECT_ROOT),
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


# ── Concurrency tests (no Neo4j) ─────────────────────────────────────


def _make_enricher_no_db() -> FlowEnricher:
    """Build a FlowEnricher whose internal `_fetch_flows` / `_enrich_one` are
    patched out so the concurrency surface can be exercised without Neo4j."""
    runner = MagicMock()
    return FlowEnricher(runner, _make_config())


def _flow(flow_id: str, *, explanation: str | None = None) -> dict:
    return {
        "flow_id": flow_id,
        "type": "http",
        "name": flow_id,
        "entry_fqn": "App\\Foo",
        "entry_method": "bar",
        "explanation": explanation,
        "route": None,
        "http_methods": [],
        "message_class": None,
        "event_name": None,
        "command_name": None,
    }


def test_enrich_flows_concurrency_field_from_env(monkeypatch):
    """AC #3 — `ENRICH_FLOWS_CONCURRENCY` env var lands on `AIConfig.enrich_flows_concurrency`.

    Tested here (not just in test_ai_config.py) so the flow-enricher path is
    proven to consume the right field name end-to-end.
    """
    monkeypatch.setenv("ENRICH_FLOWS_CONCURRENCY", "5")
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.setenv("EMBEDDING_API_KEY", "k")
    cfg = AIConfig.from_env()
    assert cfg.enrich_flows_concurrency == 5


def test_enrich_all_flows_concurrency_1_skips_executor():
    """AC #7 (flow parity) — `max_concurrency=1` never constructs a ThreadPoolExecutor."""
    enricher = _make_enricher_no_db()
    flows = [_flow(f"f{i}") for i in range(3)]

    with (
        patch.object(enricher, "_fetch_flows", return_value=flows),
        patch.object(enricher, "_enrich_one", return_value={"flow_id": "_", "explanation": "x"}),
        patch("src.ai._parallel.ThreadPoolExecutor") as pool_spy,
    ):
        enricher.enrich_all_flows(force=True, max_concurrency=1)

    assert pool_spy.called is False


def test_flow_concurrency_1_byte_identical_to_sequential():
    """AC #19 — `max_concurrency=1` produces the same counts and in-order `failed_flows`
    as the pre-feature sequential loop."""
    enricher = _make_enricher_no_db()
    flows = [_flow(f"f{i}") for i in range(5)]
    flows[1]["explanation"] = "already"  # skipped via _enrich_one's own check
    raise_for = {"f3"}

    def fake_enrich_one(flow, force):
        if flow["flow_id"] in raise_for:
            raise RuntimeError("boom")
        if not force and flow.get("explanation"):
            return {"skipped": True, "flow_id": flow["flow_id"]}
        return {"flow_id": flow["flow_id"], "explanation": "x"}

    with (
        patch.object(enricher, "_fetch_flows", return_value=flows),
        patch.object(enricher, "_enrich_one", side_effect=fake_enrich_one),
    ):
        progress = enricher.enrich_all_flows(force=False, max_concurrency=1)

    assert progress.total == 5
    assert progress.processed == 3
    assert progress.skipped == 1
    assert progress.failed == 1
    # Sequential path preserves input order in failed_flows.
    assert progress.failed_flows == ["f3"]


def test_peak_in_flight_respects_max_concurrency_flows():
    """AC #17 — under `max_concurrency=N`, peak concurrent `_enrich_one` calls <= N."""
    enricher = _make_enricher_no_db()
    flows = [_flow(f"f{i}") for i in range(20)]

    in_flight = 0
    peak = 0
    state_lock = threading.Lock()
    barrier = threading.Barrier(10)

    def fake_enrich_one(flow, force):
        nonlocal in_flight, peak
        with state_lock:
            in_flight += 1
            peak = max(peak, in_flight)
        barrier.wait(timeout=5.0)
        with state_lock:
            in_flight -= 1
        return {"flow_id": flow["flow_id"], "explanation": "x"}

    with (
        patch.object(enricher, "_fetch_flows", return_value=flows),
        patch.object(enricher, "_enrich_one", side_effect=fake_enrich_one),
    ):
        progress = enricher.enrich_all_flows(force=True, max_concurrency=10)

    assert peak == 10
    assert progress.processed == 20
    assert progress.failed == 0


def test_embedding_exception_isolated_to_one_flow():
    """AC #18 — one flow raising in `_enrich_one` does not affect the others.

    The error is recorded once in `failed_flows`; remaining flows complete.
    """
    enricher = _make_enricher_no_db()
    flows = [_flow(f"f{i}") for i in range(10)]

    def fake_enrich_one(flow, force):
        if flow["flow_id"] == "f5":
            raise RuntimeError("embedding failed")
        return {"flow_id": flow["flow_id"], "explanation": "x"}

    with (
        patch.object(enricher, "_fetch_flows", return_value=flows),
        patch.object(enricher, "_enrich_one", side_effect=fake_enrich_one),
    ):
        progress = enricher.enrich_all_flows(force=True, max_concurrency=5)

    assert progress.total == 10
    assert progress.processed == 9
    assert progress.failed == 1
    assert progress.failed_flows == ["f5"]


def test_callback_invoked_on_main_thread_only_for_flows():
    """AC #10 (flow parity) — progress callback is invoked from the main thread."""
    enricher = _make_enricher_no_db()
    flows = [_flow(f"f{i}") for i in range(10)]

    main_ident = threading.current_thread().ident
    seen_idents: list[int] = []

    def cb(p):
        seen_idents.append(threading.current_thread().ident)

    with (
        patch.object(enricher, "_fetch_flows", return_value=flows),
        patch.object(enricher, "_enrich_one", return_value={"flow_id": "_", "explanation": "x"}),
    ):
        enricher.enrich_all_flows(force=True, callback=cb, max_concurrency=5)

    assert len(seen_idents) == 11  # initial + 10 items
    assert all(t == main_ident for t in seen_idents)


def test_callback_invariants_hold_under_concurrency_flows():
    """AC #8, #9, #11 (flow parity) — total set before first cb and stable; running sum <= total;
    final invocation has running sum == total."""
    enricher = _make_enricher_no_db()
    flows = [_flow(f"f{i}") for i in range(10)]

    snapshots: list[tuple[int, int, int, int]] = []

    def cb(p):
        snapshots.append((p.total, p.processed, p.skipped, p.failed))

    with (
        patch.object(enricher, "_fetch_flows", return_value=flows),
        patch.object(enricher, "_enrich_one", return_value={"flow_id": "_", "explanation": "x"}),
    ):
        enricher.enrich_all_flows(force=True, callback=cb, max_concurrency=5)

    # First callback fires before workers; total is set, sums are zero.
    assert snapshots[0] == (10, 0, 0, 0)
    # Total never changes.
    assert all(s[0] == 10 for s in snapshots)
    # Running sum is non-decreasing and bounded by total.
    for total, p, s, f in snapshots:
        assert p + s + f <= total
    # Final invocation accounts for every flow.
    final_total, final_p, final_s, final_f = snapshots[-1]
    assert final_p + final_s + final_f == final_total


def test_pipeline_mock_injection_works_under_concurrency_flows():
    """AC #20 (flow parity) — assigning the test seam attributes overrides the thread-local
    set, even when `max_concurrency > 1`. Real pipeline factories are never invoked."""
    enricher = _make_enricher_no_db()
    flows = [_flow(f"f{i}") for i in range(5)]

    fake_explain = MagicMock()
    fake_embed = MagicMock()
    enricher._explain_pipeline = fake_explain
    enricher._embed_pipeline = fake_embed

    # The accessor must surface BOTH injected pipelines on the main thread …
    pipes = enricher._get_pipelines()
    assert pipes.explain is fake_explain
    assert pipes.embed is fake_embed

    # … and from a worker thread.
    seen: list[tuple[object, object]] = []

    def from_worker():
        p = enricher._get_pipelines()
        seen.append((p.explain, p.embed))

    t = threading.Thread(target=from_worker)
    t.start()
    t.join()
    assert seen == [(fake_explain, fake_embed)]

    # Drive enrich_all_flows under concurrency with the mocks in place; the
    # real pipeline factory must NOT be invoked because `_get_pipelines()`
    # short-circuits when both attributes are set.
    with (
        patch.object(enricher, "_fetch_flows", return_value=flows),
        patch.object(enricher, "_enrich_one", return_value={"flow_id": "_", "explanation": "x"}),
        patch(
            "src.ai.pipelines.build_explain_flow_pipeline",
            side_effect=AssertionError("real builder must not be called"),
        ),
        patch(
            "src.ai.pipelines.build_embed_pipeline",
            side_effect=AssertionError("real builder must not be called"),
        ),
    ):
        progress = enricher.enrich_all_flows(force=True, max_concurrency=3)

    assert progress.processed == 5
    assert progress.failed == 0


def test_partial_mock_injection_falls_back_to_thread_local():
    """`_get_pipelines()` mixes injected attrs with thread-local builds when only
    one of `_explain_pipeline` / `_embed_pipeline` is set. Guards the AC #20 trap."""
    enricher = _make_enricher_no_db()

    fake_explain = MagicMock()
    enricher._explain_pipeline = fake_explain
    # _embed_pipeline left at None — builder must run to populate thread-local

    sentinel_explain = MagicMock()
    sentinel_embed = MagicMock()

    with (
        patch("src.ai.pipelines.build_explain_flow_pipeline", return_value=sentinel_explain),
        patch("src.ai.pipelines.build_embed_pipeline", return_value=sentinel_embed),
    ):
        pipes = enricher._get_pipelines()

    # Injected attr wins on explain; thread-local fills embed from the builder.
    assert pipes.explain is fake_explain
    assert pipes.embed is sentinel_embed


def test_zero_flows_callback_fires_once_with_zero_counters():
    """Edge case 1 — empty input still produces one initial callback; no worker
    is invoked and no error is raised."""
    enricher = _make_enricher_no_db()
    snapshots: list[tuple[int, int, int, int]] = []

    def cb(p):
        snapshots.append((p.total, p.processed, p.skipped, p.failed))

    with (
        patch.object(enricher, "_fetch_flows", return_value=[]),
        patch.object(enricher, "_enrich_one", side_effect=AssertionError("must not run")),
    ):
        progress = enricher.enrich_all_flows(force=True, callback=cb, max_concurrency=4)

    assert progress.total == 0
    assert progress.processed == 0
    assert progress.failed == 0
    assert snapshots == [(0, 0, 0, 0)]
