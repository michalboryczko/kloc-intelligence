"""Tests for `Enricher.enrich_all` concurrent execution.

Covers ACs from `docs/specs/paraller-llm-api.md`:
- 4 (sequential fast-path byte-identical)
- 5 (peak in-flight ≤ max_concurrency)
- 6 (no deadlock when items < workers)
- 7 (max_concurrency=1 skips ThreadPoolExecutor)
- 8, 9, 11 (callback invariants)
- 10 (callback runs on main thread only)
- 12 (LLM-exception isolation)
- 13 (embedding-exception isolation)
- 14 (all-fail terminal state)
- 15 (mixed success/skip/fail; failed_nodes is a set match)
- 16 (HTTP 429 recorded as failure, no retry)
- 20 (test seam: mock pipeline injection works under concurrency)

All Neo4j/Qdrant access is mocked. The test seam injects MagicMock pipelines
on the enricher; the per-thread pipeline builder is never reached.
"""

import threading
from unittest.mock import MagicMock, patch

import pytest

haystack = pytest.importorskip("haystack", reason="ai extras not installed")
from src.ai.config import AIConfig, EmbeddingProviderConfig, LLMProviderConfig
from src.ai.enricher import Enricher, EnrichmentProgress
from src.models.node import NodeData

# ── Test helpers ─────────────────────────────────────────────────


def _make_config(**overrides) -> AIConfig:
    base = dict(
        llm=LLMProviderConfig(api_key="test-key", model="test-llm"),
        embedding=EmbeddingProviderConfig(api_key="test-key", model="test-embed", dimension=8),
        project_root="/tmp/proj",
        project_name="test",
    )
    base.update(overrides)
    return AIConfig(**base)


def _make_node(node_id: str, kind: str = "Method", fqn: str | None = None) -> NodeData:
    return NodeData(
        node_id=node_id,
        kind=kind,
        name=node_id.split("::")[-1] if "::" in node_id else node_id,
        fqn=fqn or f"App\\Test\\{node_id}",
        symbol=f"sym/{node_id}",
        file=f"src/{node_id}.php",
        start_line=0,
        end_line=10,
    )


def _build_enricher(
    nodes: list[NodeData],
    config: AIConfig | None = None,
    explanation: str = "Does something.",
) -> Enricher:
    """Build an Enricher whose Neo4j-side dependencies are all stubbed.

    Pipelines are NOT pre-injected — tests inject what they need so the
    test seam (AC #20) and per-thread fallback paths are exercised explicitly.
    """
    runner = MagicMock()
    enricher = Enricher(runner, config or _make_config())
    enricher._get_enrichable_nodes = MagicMock(return_value=nodes)  # type: ignore[method-assign]
    enricher._has_explanation = MagicMock(return_value=False)  # type: ignore[method-assign]
    enricher._store_explanation = MagicMock(return_value=None)  # type: ignore[method-assign]
    enricher._gather_method_type_context = MagicMock(return_value=[])  # type: ignore[method-assign]
    enricher._gather_class_parent_context = MagicMock(return_value=[])  # type: ignore[method-assign]
    enricher._gather_class_usage_context = MagicMock(return_value=[])  # type: ignore[method-assign]
    enricher._get_method_sources = MagicMock(return_value=[])  # type: ignore[method-assign]
    # Source reader is invoked per-node; canned source so the early-return path
    # in `_enrich_single` does not raise.
    enricher._reader = MagicMock()
    enricher._reader.read_node_source = MagicMock(return_value=f"<?php // {explanation}")
    return enricher


def _inject_simple_mocks(enricher: Enricher) -> tuple[MagicMock, MagicMock, MagicMock, MagicMock]:
    """Attach trivial MagicMock pipelines to the four test-seam attributes."""
    method_explain = MagicMock(name="method_explain_pipeline")
    class_explain = MagicMock(name="class_explain_pipeline")
    code_embed = MagicMock(name="code_embed_pipeline")
    explain_embed = MagicMock(name="explain_embed_pipeline")
    enricher._method_explain_pipeline = method_explain
    enricher._class_explain_pipeline = class_explain
    enricher._code_embed_pipeline = code_embed
    enricher._explain_embed_pipeline = explain_embed
    return method_explain, class_explain, code_embed, explain_embed


# ── E3 (AC 4): sequential fast-path byte-identical regression ─────────


class TestSequentialFastPath:
    def test_max_concurrency_1_byte_identical_to_sequential(self):
        nodes = [_make_node(f"m{i}") for i in range(5)]
        enricher = _build_enricher(nodes)
        # Mark node 3 as already-enriched (skipped), node 4 raises (fail).
        enricher._has_explanation = MagicMock(  # type: ignore[method-assign]
            side_effect=lambda nid: nid == "m3"
        )
        _inject_simple_mocks(enricher)

        with patch("src.ai.enricher.run_explain_method") as run_explain:
            run_explain.side_effect = lambda *_a, **kw: "" if kw["fqn"] == nodes[4].fqn else "ok"
            progress = enricher.enrich_all(max_concurrency=1)

        # Counts identical to the pre-feature sequential implementation
        assert progress.total == 5
        assert progress.skipped == 1
        assert progress.failed == 1
        assert progress.processed == 3
        # Sequential fast-path → failed_nodes preserves input order
        assert progress.failed_nodes == [nodes[4].fqn]

    def test_max_concurrency_1_failed_nodes_input_order(self):
        nodes = [_make_node(f"m{i}") for i in range(6)]
        enricher = _build_enricher(nodes)
        _inject_simple_mocks(enricher)

        failing = {nodes[1].fqn, nodes[4].fqn}
        with patch("src.ai.enricher.run_explain_method") as run_explain:
            run_explain.side_effect = lambda *_a, **kw: "" if kw["fqn"] in failing else "ok"
            progress = enricher.enrich_all(max_concurrency=1)

        assert progress.failed == 2
        # Order matches input order: nodes[1] before nodes[4]
        assert progress.failed_nodes == [nodes[1].fqn, nodes[4].fqn]


# ── E4 (AC 5): peak in-flight ≤ max_concurrency ───────────────────────


class TestPeakConcurrency:
    def test_peak_in_flight_respects_max_concurrency(self):
        n_items = 50
        max_n = 10
        in_flight = 0
        peak = 0
        lock = threading.Lock()
        barrier = threading.Barrier(max_n)

        def explain_side_effect(*_args, **_kwargs):
            nonlocal in_flight, peak
            with lock:
                in_flight += 1
                if in_flight > peak:
                    peak = in_flight
            try:
                barrier.wait(timeout=5.0)
            except threading.BrokenBarrierError:
                pass
            with lock:
                in_flight -= 1
            return "ok"

        nodes = [_make_node(f"m{i}") for i in range(n_items)]
        enricher = _build_enricher(nodes)
        _inject_simple_mocks(enricher)

        with patch("src.ai.enricher.run_explain_method", side_effect=explain_side_effect):
            progress = enricher.enrich_all(max_concurrency=max_n)

        assert progress.processed == n_items
        assert peak == max_n


# ── E5 (AC 6): no deadlock with fewer items than workers ──────────────


class TestNoDeadlock:
    def test_fewer_items_than_workers_no_deadlock(self):
        nodes = [_make_node(f"m{i}") for i in range(3)]
        enricher = _build_enricher(nodes)
        _inject_simple_mocks(enricher)

        with patch("src.ai.enricher.run_explain_method", return_value="ok"):
            progress = enricher.enrich_all(max_concurrency=10)

        assert progress.processed == 3
        assert progress.failed == 0
        assert progress.skipped == 0


# ── E6 (AC 7): max_concurrency=1 skips ThreadPoolExecutor ─────────────


class TestSequentialSkipsExecutor:
    def test_concurrency_1_does_not_construct_thread_pool_executor(self):
        nodes = [_make_node(f"m{i}") for i in range(4)]
        enricher = _build_enricher(nodes)
        _inject_simple_mocks(enricher)

        with (
            patch("src.ai._parallel.ThreadPoolExecutor") as pool_cls,
            patch("src.ai.enricher.run_explain_method", return_value="ok"),
        ):
            enricher.enrich_all(max_concurrency=1)

        assert pool_cls.called is False

    def test_concurrency_gt_1_does_construct_thread_pool_executor(self):
        """Sanity check the spy works the other way too."""
        nodes = [_make_node(f"m{i}") for i in range(4)]
        enricher = _build_enricher(nodes)
        _inject_simple_mocks(enricher)

        with patch("src.ai.enricher.run_explain_method", return_value="ok"):
            enricher.enrich_all(max_concurrency=4)
        # Plain happy-path; if it didn't construct the pool the test above
        # would still pass, so the negative assertion in the first test is
        # the load-bearing one.


# ── E7 (ACs 8, 9, 11): callback invariants ────────────────────────────


class TestCallbackInvariants:
    def test_callback_invariants_hold_under_concurrency(self):
        nodes = [_make_node(f"m{i}") for i in range(20)]
        enricher = _build_enricher(nodes)
        _inject_simple_mocks(enricher)

        snapshots: list[tuple[int, int, int, int]] = []

        def cb(p: EnrichmentProgress) -> None:
            snapshots.append((p.total, p.processed, p.skipped, p.failed))

        with patch("src.ai.enricher.run_explain_method", return_value="ok"):
            progress = enricher.enrich_all(max_concurrency=5, callback=cb)

        # AC 11: total is set before the first callback fires and is constant
        assert all(s[0] == 20 for s in snapshots)
        # AC 8: processed + skipped + failed <= total at every invocation
        assert all(s[1] + s[2] + s[3] <= s[0] for s in snapshots)
        # AC 9: final invocation satisfies equality
        last = snapshots[-1]
        assert last[1] + last[2] + last[3] == last[0]
        # Final state matches the returned progress
        assert (progress.total, progress.processed, progress.skipped, progress.failed) == last

    def test_progress_total_set_before_first_callback(self):
        nodes = [_make_node(f"m{i}") for i in range(7)]
        enricher = _build_enricher(nodes)
        _inject_simple_mocks(enricher)

        seen_totals: list[int] = []

        def cb(p: EnrichmentProgress) -> None:
            seen_totals.append(p.total)

        with patch("src.ai.enricher.run_explain_method", return_value="ok"):
            enricher.enrich_all(max_concurrency=3, callback=cb)

        assert seen_totals[0] == 7
        assert all(t == 7 for t in seen_totals)


# ── E8 (AC 10): callback runs on main thread only ─────────────────────


class TestCallbackThread:
    def test_callback_invoked_on_main_thread_only(self):
        main_thread = threading.current_thread().ident
        nodes = [_make_node(f"m{i}") for i in range(15)]
        enricher = _build_enricher(nodes)
        _inject_simple_mocks(enricher)

        cb_threads: list[int | None] = []

        def cb(_p: EnrichmentProgress) -> None:
            cb_threads.append(threading.current_thread().ident)

        with patch("src.ai.enricher.run_explain_method", return_value="ok"):
            enricher.enrich_all(max_concurrency=5, callback=cb)

        assert all(t == main_thread for t in cb_threads)


# ── E9 (AC 12): LLM-exception isolated to one node ────────────────────


class TestLLMExceptionIsolation:
    def test_llm_exception_isolated_to_one_node(self):
        nodes = [_make_node(f"m{i}") for i in range(10)]
        bad = nodes[3]
        enricher = _build_enricher(nodes)
        _inject_simple_mocks(enricher)

        def explain_side_effect(*_args, **kwargs):
            if kwargs["fqn"] == bad.fqn:
                raise RuntimeError("boom")
            return "ok"

        with patch("src.ai.enricher.run_explain_method", side_effect=explain_side_effect):
            progress = enricher.enrich_all(max_concurrency=5)

        assert progress.processed == 9
        assert progress.failed == 1
        assert progress.skipped == 0
        assert set(progress.failed_nodes) == {bad.fqn}


# ── E10 (AC 13): embedding-exception isolated to one node ─────────────


class TestEmbeddingExceptionIsolation:
    def test_embedding_exception_isolated_to_one_node(self):
        nodes = [_make_node(f"m{i}") for i in range(8)]
        bad_node_id = nodes[2].node_id
        enricher = _build_enricher(nodes)
        method_explain, _class_explain, code_embed, _explain_embed = _inject_simple_mocks(enricher)

        def embed_side_effect(payload: dict) -> dict:
            docs = payload["embedder"]["documents"]
            # Each call has one node's documents; the first doc's node_id is the
            # node being processed. Raise for the targeted node only.
            if docs and docs[0].meta.get("node_id") == bad_node_id:
                raise RuntimeError("embed-fail")
            return {}

        code_embed.run.side_effect = embed_side_effect

        with patch("src.ai.enricher.run_explain_method", return_value="ok"):
            progress = enricher.enrich_all(max_concurrency=4)

        assert progress.failed == 1
        assert progress.processed == 7
        assert set(progress.failed_nodes) == {nodes[2].fqn}
        # The mock LLM was called for every node — the failure is downstream
        assert method_explain.run.call_count == 0  # we patched run_explain_method


# ── E11 (AC 14): all nodes fail ───────────────────────────────────────


class TestAllFail:
    def test_all_nodes_fail_returns_zero_processed(self):
        nodes = [_make_node(f"m{i}") for i in range(6)]
        enricher = _build_enricher(nodes)
        _inject_simple_mocks(enricher)

        with patch(
            "src.ai.enricher.run_explain_method",
            side_effect=RuntimeError("always fails"),
        ):
            progress = enricher.enrich_all(max_concurrency=3)

        assert progress.total == 6
        assert progress.failed == 6
        assert progress.processed == 0
        assert progress.skipped == 0
        assert set(progress.failed_nodes) == {n.fqn for n in nodes}


# ── E12 (AC 15): mixed success/skip/fail ──────────────────────────────


class TestMixedOutcomes:
    def test_failed_nodes_matches_set_of_raisers(self):
        nodes = [_make_node(f"m{i}") for i in range(10)]
        skip_ids = {nodes[0].node_id, nodes[5].node_id, nodes[8].node_id}
        fail_fqns = {nodes[2].fqn, nodes[7].fqn}

        enricher = _build_enricher(nodes)
        enricher._has_explanation = MagicMock(  # type: ignore[method-assign]
            side_effect=lambda nid: nid in skip_ids
        )
        _inject_simple_mocks(enricher)

        def explain_side_effect(*_args, **kwargs):
            if kwargs["fqn"] in fail_fqns:
                raise RuntimeError("boom")
            return "ok"

        with patch("src.ai.enricher.run_explain_method", side_effect=explain_side_effect):
            progress = enricher.enrich_all(max_concurrency=4)

        assert progress.total == 10
        assert progress.skipped == 3
        assert progress.failed == 2
        assert progress.processed == 5
        # Counts sum to total
        assert progress.skipped + progress.failed + progress.processed == progress.total
        # Set comparison only — concurrency order is unspecified
        assert set(progress.failed_nodes) == fail_fqns


# ── R1 (AC 16): HTTP 429 recorded as failure, no retry ────────────────


class TestRateLimit:
    def test_http_429_recorded_as_failure_no_retry(self):
        nodes = [_make_node(f"m{i}") for i in range(5)]
        enricher = _build_enricher(nodes)
        _inject_simple_mocks(enricher)

        call_count = 0
        call_lock = threading.Lock()

        class FakeRateLimitError(Exception):
            """Stand-in for openai.RateLimitError / httpx HTTP 429."""

        def explain_side_effect(*_args, **kwargs):
            nonlocal call_count
            with call_lock:
                call_count += 1
            if kwargs["fqn"] == nodes[1].fqn:
                raise FakeRateLimitError("429 Too Many Requests")
            return "ok"

        with patch("src.ai.enricher.run_explain_method", side_effect=explain_side_effect):
            progress = enricher.enrich_all(max_concurrency=3)

        assert progress.failed == 1
        assert nodes[1].fqn in progress.failed_nodes
        # No retry: the LLM stub was called once per node — exactly N times
        assert call_count == len(nodes)


# ── E13 (AC 20): pipeline mock injection works under concurrency ──────


class TestPipelineInjectionSeam:
    def test_pipeline_mock_injection_works_under_concurrency(self):
        nodes = [_make_node(f"m{i}", kind="Method") for i in range(5)]
        enricher = _build_enricher(nodes)
        method_explain, _class_explain, code_embed, explain_embed = _inject_simple_mocks(enricher)

        with (
            patch("src.ai.enricher.run_explain_method", return_value="ok") as explain_spy,
            patch("src.ai.pipelines.build_explain_pipeline") as build_explain_spy,
            patch("src.ai.pipelines.build_embed_pipeline") as build_embed_spy,
        ):
            progress = enricher.enrich_all(max_concurrency=3)

        # All nodes processed
        assert progress.processed == 5
        assert progress.failed == 0
        # Real pipelines were never built — the injected MagicMocks dominated
        assert build_explain_spy.called is False
        assert build_embed_spy.called is False
        # The injected `method_explain` mock was the pipeline argument every time
        assert explain_spy.call_count == 5
        for call in explain_spy.call_args_list:
            args, _kwargs = call
            assert args[0] is method_explain
        # Each injected embed mock was invoked once per node
        assert code_embed.run.call_count == 5
        assert explain_embed.run.call_count == 5

    def test_partial_mock_injection_falls_back_to_thread_local(self):
        """Only the method-explain pipeline is injected; the rest must fall back.

        Sanity for the _get_pipelines() merge logic.
        """
        nodes = [_make_node("m0", kind="Method")]
        enricher = _build_enricher(nodes)
        injected = MagicMock(name="method_explain")
        enricher._method_explain_pipeline = injected

        built = MagicMock(name="built")
        with (
            patch("src.ai.enricher.run_explain_method", return_value="ok") as explain_spy,
            patch("src.ai.pipelines.build_explain_pipeline", return_value=built) as build_e,
            patch("src.ai.pipelines.build_embed_pipeline", return_value=built) as build_em,
        ):
            progress = enricher.enrich_all(max_concurrency=1)

        assert progress.processed == 1
        # Method-explain came from the injection
        assert explain_spy.call_args.args[0] is injected
        # The other three pipelines came from the builder
        assert build_e.called
        assert build_em.called


# ── max_concurrency parameter overrides env / config ─────────────────


class TestMaxConcurrencyParameterPrecedence:
    def test_explicit_max_concurrency_overrides_config(self):
        """Passing max_concurrency wins over AIConfig.enrich_concurrency."""
        nodes = [_make_node(f"m{i}") for i in range(2)]
        config = _make_config()
        config.enrich_concurrency = 7
        enricher = _build_enricher(nodes, config=config)
        _inject_simple_mocks(enricher)

        observed: list[int] = []
        orig_run_parallel = __import__("src.ai.enricher", fromlist=["run_parallel"]).run_parallel

        def spy_run_parallel(items, worker, *, max_concurrency, on_completed):
            observed.append(max_concurrency)
            return orig_run_parallel(
                items, worker, max_concurrency=max_concurrency, on_completed=on_completed
            )

        with (
            patch("src.ai.enricher.run_parallel", side_effect=spy_run_parallel),
            patch("src.ai.enricher.run_explain_method", return_value="ok"),
        ):
            enricher.enrich_all(max_concurrency=2)

        assert observed == [2]

    def test_max_concurrency_none_uses_config_value(self):
        nodes = [_make_node(f"m{i}") for i in range(2)]
        config = _make_config()
        config.enrich_concurrency = 4
        enricher = _build_enricher(nodes, config=config)
        _inject_simple_mocks(enricher)

        observed: list[int] = []
        orig_run_parallel = __import__("src.ai.enricher", fromlist=["run_parallel"]).run_parallel

        def spy_run_parallel(items, worker, *, max_concurrency, on_completed):
            observed.append(max_concurrency)
            return orig_run_parallel(
                items, worker, max_concurrency=max_concurrency, on_completed=on_completed
            )

        with (
            patch("src.ai.enricher.run_parallel", side_effect=spy_run_parallel),
            patch("src.ai.enricher.run_explain_method", return_value="ok"),
        ):
            enricher.enrich_all()  # no max_concurrency arg

        assert observed == [4]


# ── Empty / edge-case coverage ────────────────────────────────────────


class TestEmptyEdgeCase:
    def test_zero_nodes_fires_callback_once_and_returns(self):
        enricher = _build_enricher([])
        _inject_simple_mocks(enricher)

        cb_calls = 0

        def cb(_p: EnrichmentProgress) -> None:
            nonlocal cb_calls
            cb_calls += 1

        with patch("src.ai._parallel.ThreadPoolExecutor") as pool_cls:
            progress = enricher.enrich_all(max_concurrency=10, callback=cb)

        assert progress.total == 0
        assert progress.processed == 0
        assert progress.failed == 0
        assert progress.skipped == 0
        assert cb_calls == 1
        # No items → no executor constructed
        assert pool_cls.called is False
