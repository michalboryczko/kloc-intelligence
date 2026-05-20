"""Tests for FlowEnricher (graph + context walk + dispatch context + explanation storage).

LLM and embedding calls are mocked so the test runs offline. The new
``_gather_dispatch_context`` method is exercised on the v3 reference fixture
(``kloc-symfony/contract-tests/output/symfony-kloc.json``).
"""

import threading
from unittest.mock import MagicMock, patch

import pytest

from src.ai import pipelines as _pipelines
from src.ai.config import AIConfig, EmbeddingProviderConfig, LLMProviderConfig
from src.ai.flow_enricher import (
    FlowEnricher,
    FlowEnrichmentProgress,
    get_flow_enrichment_status,
)
from src.db.flow_importer import run_import
from src.db.query_runner import QueryRunner
from src.models.node import NodeData

from .conftest import REFERENCE_PROJECT_ROOT, REFERENCE_SYMFONY_KLOC_V3, requires_neo4j

PAYMENT_VERIFY_FLOW_ID = "flow:http:App\\Ui\\Rest\\Controller\\PaymentController::verify"
ORDER_EVENT_SUBSCRIBER_FLOW_ID = (
    "flow:event:App\\Ui\\EventSubscriber\\OrderEventSubscriber::onOrderCreated[OrderCreatedEvent]"
)
ORDER_CREATE_FLOW_ID = "flow:http:App\\Ui\\Rest\\Controller\\OrderController::create"
ORDER_GET_FLOW_ID = "flow:http:App\\Ui\\Rest\\Controller\\OrderController::get"
CUSTOMER_GET_FLOW_ID = "flow:http:App\\Ui\\Rest\\Controller\\CustomerController::get"


def _make_config() -> AIConfig:
    return AIConfig(
        llm=LLMProviderConfig(api_key="test-key", model="test-llm"),
        embedding=EmbeddingProviderConfig(api_key="test-key", model="test-embed", dimension=8),
        project_root=str(REFERENCE_PROJECT_ROOT),
        project_name="test",
    )


@pytest.fixture(scope="module")
def loaded_with_v3_flows(_loaded_database_conn):
    """Module-scoped fixture: reuse loaded test SoT + import the v3 reference flows."""
    conn = _loaded_database_conn
    if not REFERENCE_SYMFONY_KLOC_V3.is_file():
        pytest.skip(f"Reference v3 fixture not found: {REFERENCE_SYMFONY_KLOC_V3}")
    run_import(conn, REFERENCE_SYMFONY_KLOC_V3)
    yield conn
    with conn.session() as s:
        s.run("MATCH (f:Flow) REMOVE f.explanation, f.explain_model, f.explain_at")


# ── Status / smoke tests (Neo4j-backed) ─────────────────────────────


@requires_neo4j
def test_get_flow_enrichment_status_counts(loaded_with_v3_flows):
    status = get_flow_enrichment_status(loaded_with_v3_flows)
    assert status["total"] == 10
    assert status["enriched"] == 0
    assert status["pending"] == 10


@requires_neo4j
def test_enrich_flow_writes_explanation_property(loaded_with_v3_flows):
    """End-to-end with mocked LLM + embedder: explanation lands on the :Flow node."""
    runner = QueryRunner(loaded_with_v3_flows)
    config = _make_config()
    enricher = FlowEnricher(runner, config)

    fake_pipe = MagicMock()
    embed_pipe = MagicMock()
    enricher._explain_pipeline = fake_pipe
    enricher._embed_pipeline = embed_pipe

    with patch(
        "src.ai.pipelines.run_explain_flow", return_value="Creates new orders for customers."
    ):
        result = enricher.enrich_flow(ORDER_CREATE_FLOW_ID, force=True)

    assert "error" not in result, result
    assert result["flow_id"] == ORDER_CREATE_FLOW_ID

    record = runner.execute_single(
        "MATCH (f:Flow {flow_id: $fid}) RETURN f.explanation AS expl, f.explain_model AS m",
        fid=ORDER_CREATE_FLOW_ID,
    )
    assert record["expl"] == "Creates new orders for customers."
    assert record["m"] == "test-llm"

    assert embed_pipe.run.called
    docs = embed_pipe.run.call_args.args[0]["embedder"]["documents"]
    assert len(docs) == 1
    assert docs[0].content == "Creates new orders for customers."
    assert docs[0].meta["flow_id"] == ORDER_CREATE_FLOW_ID
    assert docs[0].meta["kind"] == "Flow"


@requires_neo4j
def test_enrich_flow_unknown_id_returns_error(loaded_with_v3_flows):
    runner = QueryRunner(loaded_with_v3_flows)
    enricher = FlowEnricher(runner, _make_config())
    enricher._explain_pipeline = MagicMock()
    enricher._embed_pipeline = MagicMock()
    result = enricher.enrich_flow("flow:http:Nope::nope")
    assert "error" in result


@requires_neo4j
def test_enrich_flow_skips_when_already_enriched_without_force(loaded_with_v3_flows):
    """AC #20 regression — pre-enriched flow returns ``{skipped: True}`` and
    ``run_explain_flow`` is NOT called for it."""
    runner = QueryRunner(loaded_with_v3_flows)
    config = _make_config()
    enricher = FlowEnricher(runner, config)
    enricher._explain_pipeline = MagicMock()
    enricher._embed_pipeline = MagicMock()

    runner.execute(
        "MATCH (f:Flow {flow_id: $fid}) SET f.explanation = 'pre-existing'",
        fid=ORDER_GET_FLOW_ID,
    )
    mock_run = MagicMock(return_value="ok")
    with patch("src.ai.pipelines.run_explain_flow", mock_run):
        result = enricher.enrich_flow(ORDER_GET_FLOW_ID, force=False)
    assert result == {"skipped": True, "flow_id": ORDER_GET_FLOW_ID}
    mock_run.assert_not_called()
    assert not enricher._embed_pipeline.run.called

    runner.execute(
        "MATCH (f:Flow {flow_id: $fid}) REMOVE f.explanation",
        fid=ORDER_GET_FLOW_ID,
    )


@requires_neo4j
def test_get_flow_enrichment_status_after_partial_enrichment(loaded_with_v3_flows):
    """After setting explanation on one flow, status counts move."""
    with loaded_with_v3_flows.session() as s:
        s.run("MATCH (f:Flow) REMOVE f.explanation")
        s.run(
            "MATCH (f:Flow {flow_id: $fid}) SET f.explanation = 'x'",
            fid=CUSTOMER_GET_FLOW_ID,
        )
    status = get_flow_enrichment_status(loaded_with_v3_flows)
    assert status["enriched"] == 1
    assert status["pending"] == status["total"] - 1


def test_progress_dataclass_defaults():
    p = FlowEnrichmentProgress()
    assert p.total == 0
    assert p.processed == 0
    assert p.failed_flows == []


# ── _gather_dispatch_context tests ──────────────────────────────────


@requires_neo4j
def test_gather_dispatch_context_payment_verify(loaded_with_v3_flows):
    """``PaymentController::verify`` dispatches AuditLogMessage and calls paypal.client."""
    runner = QueryRunner(loaded_with_v3_flows)
    enricher = FlowEnricher(runner, _make_config())

    ctx = enricher._gather_dispatch_context(PAYMENT_VERIFY_FLOW_ID)

    assert any(m.get("fqn", "").endswith("AuditLogMessage") for m in ctx["emits_messages"]), ctx[
        "emits_messages"
    ]
    assert ctx["emits_events"] == []
    assert any(h.get("service_id") == "paypal.client" for h in ctx["http_calls"]), ctx["http_calls"]
    assert any(h.get("base_uri") == "https://api.paypal.com" for h in ctx["http_calls"]), ctx[
        "http_calls"
    ]
    assert ctx["triggered_by_messages"] == []
    assert ctx["triggered_by_events"] == []


@requires_neo4j
def test_gather_dispatch_context_order_event_subscriber(loaded_with_v3_flows):
    """``OrderEventSubscriber::onOrderCreated`` is triggered by OrderCreatedEvent."""
    runner = QueryRunner(loaded_with_v3_flows)
    enricher = FlowEnricher(runner, _make_config())

    ctx = enricher._gather_dispatch_context(ORDER_EVENT_SUBSCRIBER_FLOW_ID)

    assert ctx["emits_messages"] == []
    assert ctx["emits_events"] == []
    assert ctx["http_calls"] == []
    assert ctx["triggered_by_messages"] == []
    assert any(
        e.get("fqn", "").endswith("OrderCreatedEvent") and e.get("priority") == 0
        for e in ctx["triggered_by_events"]
    ), ctx["triggered_by_events"]


@requires_neo4j
def test_gather_dispatch_context_flow_with_no_context(loaded_with_v3_flows):
    """Flow with no EMITS / USES_HTTP_CLIENT / HANDLED_BY returns empty lists,
    not ``[{fqn: null}]`` from the Cypher ``collect(DISTINCT ...)`` on a null edge."""
    runner = QueryRunner(loaded_with_v3_flows)
    enricher = FlowEnricher(runner, _make_config())

    ctx = enricher._gather_dispatch_context(CUSTOMER_GET_FLOW_ID)

    assert ctx == {
        "emits_messages": [],
        "emits_events": [],
        "http_calls": [],
        "triggered_by_messages": [],
        "triggered_by_events": [],
    }


@requires_neo4j
def test_gather_dispatch_context_unknown_flow_returns_empty(loaded_with_v3_flows):
    runner = QueryRunner(loaded_with_v3_flows)
    enricher = FlowEnricher(runner, _make_config())
    ctx = enricher._gather_dispatch_context("flow:http:Nope::nope")
    assert ctx == {
        "emits_messages": [],
        "emits_events": [],
        "http_calls": [],
        "triggered_by_messages": [],
        "triggered_by_events": [],
    }


# ── AC #26 / #27 seam tests ─────────────────────────────────────────


def _stub_entry_method() -> NodeData:
    """Fake entry method NodeData — sidesteps the SoT-source dependency for AC #26/#27.

    The v3 fixture references new code (PaymentController::verify,
    OrderEventSubscriber) that the App-only test SoT does not contain. The
    enrichment path needs entry-method source to build the prompt; we stub
    it out so the test stays focused on the dispatch-context kwargs that
    AC #26/#27 actually assert on.
    """
    return NodeData(
        node_id="node:stub",
        kind="Method",
        name="stub",
        fqn="App\\Stub::stub",
        symbol="stub",
        file="src/Stub.php",
        start_line=0,
        end_line=10,
    )


def _patch_enrich_one_source_deps(enricher: FlowEnricher):
    """Context-manager-like helper: returns a tuple of patches to apply via ExitStack.

    Patches the three source-dependent seams in ``_enrich_one`` so the
    dispatch-context plumbing can be exercised without a fully-populated SoT.
    """
    return (
        patch.object(enricher, "_fetch_entry_method", return_value=_stub_entry_method()),
        patch.object(
            enricher._reader,
            "read_node_source",
            return_value="public function stub() {}",
        ),
        patch.object(enricher, "_gather_context_chunks", return_value=[]),
    )


@requires_neo4j
def test_payment_verify_prompt_kwargs_contain_paypal_and_audit(loaded_with_v3_flows):
    """AC #26 — ``run_explain_flow.call_args.kwargs`` for PaymentController::verify
    surfaces ``paypal.client``, ``https://api.paypal.com`` and ``AuditLogMessage``
    via the structured context kwargs, and the rendered template echoes all three."""
    runner = QueryRunner(loaded_with_v3_flows)
    enricher = FlowEnricher(runner, _make_config())
    enricher._explain_pipeline = MagicMock()
    enricher._embed_pipeline = MagicMock()

    mock_run = MagicMock(return_value="ok")
    p_fetch, p_read, p_chunks = _patch_enrich_one_source_deps(enricher)
    with (
        p_fetch,
        p_read,
        p_chunks,
        patch("src.ai.pipelines.run_explain_flow", mock_run),
    ):
        result = enricher.enrich_flow(PAYMENT_VERIFY_FLOW_ID, force=True)

    assert "error" not in result, result
    kwargs = mock_run.call_args.kwargs

    emits_messages = kwargs["emits_messages"]
    http_calls = kwargs["http_calls"]
    assert any("AuditLogMessage" in (m.get("fqn") or "") for m in emits_messages), (
        f"AuditLogMessage not in emits_messages: {emits_messages}"
    )
    assert any(h.get("service_id") == "paypal.client" for h in http_calls), (
        f"paypal.client not in http_calls: {http_calls}"
    )
    assert any(h.get("base_uri") == "https://api.paypal.com" for h in http_calls), (
        f"https://api.paypal.com not in http_calls: {http_calls}"
    )

    from jinja2 import Environment

    from src.ai.pipelines import EXPLAIN_FLOW_TEMPLATE

    rendered = Environment().from_string(EXPLAIN_FLOW_TEMPLATE).render(**kwargs)
    for needle in ("paypal.client", "https://api.paypal.com", "AuditLogMessage"):
        assert needle in rendered, f"prompt body missing {needle!r}\n{rendered}"


@requires_neo4j
def test_order_event_subscriber_prompt_kwargs_triggered_by_order_created_event(
    loaded_with_v3_flows,
):
    """AC #27 — ``triggered_by_events`` kwarg for OrderEventSubscriber surfaces
    OrderCreatedEvent and the rendered template includes a 'Triggered by' section."""
    runner = QueryRunner(loaded_with_v3_flows)
    enricher = FlowEnricher(runner, _make_config())
    enricher._explain_pipeline = MagicMock()
    enricher._embed_pipeline = MagicMock()

    mock_run = MagicMock(return_value="ok")
    p_fetch, p_read, p_chunks = _patch_enrich_one_source_deps(enricher)
    with (
        p_fetch,
        p_read,
        p_chunks,
        patch("src.ai.pipelines.run_explain_flow", mock_run),
    ):
        result = enricher.enrich_flow(ORDER_EVENT_SUBSCRIBER_FLOW_ID, force=True)

    assert "error" not in result, result
    kwargs = mock_run.call_args.kwargs

    triggered_by_events = kwargs["triggered_by_events"]
    assert any("OrderCreatedEvent" in (e.get("fqn") or "") for e in triggered_by_events), (
        f"OrderCreatedEvent not in triggered_by_events: {triggered_by_events}"
    )

    from jinja2 import Environment

    from src.ai.pipelines import EXPLAIN_FLOW_TEMPLATE

    rendered = Environment().from_string(EXPLAIN_FLOW_TEMPLATE).render(**kwargs)
    assert "OrderCreatedEvent" in rendered
    assert "Triggered by events" in rendered, (
        f"rendered prompt missing 'Triggered by events' section heading:\n{rendered}"
    )


@requires_neo4j
def test_empty_dispatch_context_renders_without_errors(loaded_with_v3_flows):
    """Flow with empty context still renders a valid prompt (no Jinja errors)."""
    runner = QueryRunner(loaded_with_v3_flows)
    enricher = FlowEnricher(runner, _make_config())
    enricher._explain_pipeline = MagicMock()
    enricher._embed_pipeline = MagicMock()

    mock_run = MagicMock(return_value="ok")
    empty_ctx = {
        "emits_messages": [],
        "emits_events": [],
        "http_calls": [],
        "triggered_by_messages": [],
        "triggered_by_events": [],
    }
    p_fetch, p_read, p_chunks = _patch_enrich_one_source_deps(enricher)
    with (
        p_fetch,
        p_read,
        p_chunks,
        patch.object(enricher, "_gather_dispatch_context", return_value=empty_ctx),
        patch("src.ai.pipelines.run_explain_flow", mock_run),
    ):
        result = enricher.enrich_flow(CUSTOMER_GET_FLOW_ID, force=True)

    assert "error" not in result, result
    kwargs = mock_run.call_args.kwargs
    assert kwargs["emits_messages"] == []
    assert kwargs["emits_events"] == []
    assert kwargs["http_calls"] == []
    assert kwargs["triggered_by_messages"] == []
    assert kwargs["triggered_by_events"] == []

    from jinja2 import Environment

    from src.ai.pipelines import EXPLAIN_FLOW_TEMPLATE

    rendered = Environment().from_string(EXPLAIN_FLOW_TEMPLATE).render(**kwargs)
    for marker in (
        "Dispatched messages",
        "Dispatched events",
        "External HTTP integrations",
        "Triggered by messages",
        "Triggered by events",
    ):
        assert marker not in rendered, (
            f"empty-context rendering must omit {marker!r}, got:\n{rendered}"
        )


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
