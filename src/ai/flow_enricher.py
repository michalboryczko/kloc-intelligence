"""Flow-level enrichment: produce a business-process summary per :Flow.

For each :Flow node, walks the bidirectional context (callers + callees +
type info + implementations) of its entry method at depth 3, attaches
source snippets from the referenced nodes, and asks the LLM for a 1-3
sentence abstract description of the business process the flow drives.

Stored as :Flow properties (`f.explanation`, `f.explain_model`, `f.explain_at`)
and embedded into the `flow_explain_embeddings` Qdrant collection so semantic
search can return flows alongside other code.
"""

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from ..db.connection import Neo4jConnection
from ..db.query_runner import QueryRunner
from ..db.result_mapper import record_to_node
from ..models.node import NodeData
from ..models.results import ContextEntry
from ._parallel import ThreadLocalPipelines, run_parallel
from .config import AIConfig
from .source_reader import SourceReader

logger = logging.getLogger(__name__)


CONTEXT_DEPTH = 3
CONTEXT_LIMIT = 50
MAX_REFERENCED_NODES = 12
MAX_REF_CHARS_PER_NODE = 2000


@dataclass
class FlowEnrichmentProgress:
    total: int = 0
    processed: int = 0
    skipped: int = 0
    failed: int = 0
    failed_flows: list[str] = field(default_factory=list)


@dataclass
class FlowPipelines:
    """Per-thread set of flow pipelines."""

    explain: object
    embed: object


class FlowEnricher:
    """Generate business-process summaries for :Flow nodes."""

    def __init__(self, runner: QueryRunner, config: AIConfig):
        self._runner = runner
        self._config = config
        self._reader = SourceReader(config.project_root)
        # Test-seam attributes — assigning to either overrides the thread-local
        # set for that pipeline. See AC #20.
        self._explain_pipeline = None
        self._embed_pipeline = None
        self._flow_pipelines: ThreadLocalPipelines[FlowPipelines] = ThreadLocalPipelines(
            self._build_pipelines
        )

    def _build_pipelines(self) -> FlowPipelines:
        from .pipelines import build_embed_pipeline, build_explain_flow_pipeline

        return FlowPipelines(
            explain=build_explain_flow_pipeline(self._config),
            embed=build_embed_pipeline(self._config, "flow_explain_embeddings"),
        )

    def _get_pipelines(self) -> FlowPipelines:
        """Return the active flow pipelines for the current thread.

        Instance attributes (set by tests via the documented seam) override the
        thread-local set; missing attributes fall back to the thread-local
        builder. This preserves the existing test pattern of
        `enricher._explain_pipeline = MagicMock()`.
        """
        if self._explain_pipeline is None and self._embed_pipeline is None:
            return self._flow_pipelines.get()
        tl = (
            self._flow_pipelines.get()
            if self._explain_pipeline is None or self._embed_pipeline is None
            else None
        )
        return FlowPipelines(
            explain=self._explain_pipeline if self._explain_pipeline is not None else tl.explain,  # type: ignore[union-attr]
            embed=self._embed_pipeline if self._embed_pipeline is not None else tl.embed,  # type: ignore[union-attr]
        )

    def enrich_all_flows(
        self,
        force: bool = False,
        callback: Callable[[FlowEnrichmentProgress], None] | None = None,
        *,
        max_concurrency: int | None = None,
    ) -> FlowEnrichmentProgress:
        concurrency = (
            max_concurrency
            if max_concurrency is not None
            else self._config.enrich_flows_concurrency
        )
        flows = self._fetch_flows(force)
        progress = FlowEnrichmentProgress(total=len(flows))
        if callback:
            callback(progress)

        progress_lock = threading.Lock()

        def _worker(flow: dict) -> dict:
            return self._enrich_one(flow, force)

        def _on_completed(flow: dict, result: dict | None, exc: BaseException | None) -> None:
            with progress_lock:
                if exc is not None:
                    logger.error("Failed to enrich flow %s: %s", flow["flow_id"], exc)
                    progress.failed += 1
                    progress.failed_flows.append(flow["flow_id"])
                elif result is not None and result.get("skipped"):
                    progress.skipped += 1
                else:
                    progress.processed += 1
                if callback:
                    callback(progress)

        run_parallel(
            flows,
            _worker,
            max_concurrency=concurrency,
            on_completed=_on_completed,
        )

        return progress

    def enrich_flow(self, flow_id: str, force: bool = False) -> dict:
        record = self._runner.execute_single("MATCH (f:Flow {flow_id: $fid}) RETURN f", fid=flow_id)
        if not record or not record["f"]:
            return {"error": f"Flow not found: {flow_id}"}
        flow = self._flow_record_to_dict(record["f"])
        return self._enrich_one(flow, force)

    # ── Core ─────────────────────────────────────────────────────

    def _enrich_one(self, flow: dict, force: bool) -> dict:
        """Enrich a single flow. flow is a dict from _fetch_flows / _flow_record_to_dict."""
        from .pipelines import make_embed_documents, run_explain_flow

        flow_id = flow["flow_id"]

        if not force and flow.get("explanation"):
            logger.debug("Skipping flow %s (already enriched)", flow_id)
            return {"skipped": True, "flow_id": flow_id}

        logger.info("Enriching flow %s", flow_id)
        start = time.perf_counter()

        pipes = self._get_pipelines()

        method = self._fetch_entry_method(flow_id)
        if method is None:
            method = self._resolve_entry_method_fallback(flow)
        if method is None:
            raise ValueError(
                f"Flow {flow_id} has no FLOW_ENTRY edge and entry FQN "
                f"'{flow.get('entry_fqn', '')}::{flow.get('entry_method', '')}' "
                f"could not be resolved against the :Node graph"
            )

        entry_source = self._reader.read_node_source(method)
        if not entry_source:
            raise ValueError(f"Cannot read entry source for flow {flow_id} (file: {method.file})")

        referenced = self._gather_context_chunks(method)
        ctx = self._gather_dispatch_context(flow_id)

        http_methods = flow.get("http_methods") or []
        http_methods_str = ", ".join(http_methods) if http_methods else ""

        explanation = run_explain_flow(
            pipes.explain,
            flow_type=flow["type"],
            flow_name=flow["name"],
            entry_fqn=f"{flow.get('entry_fqn', '')}::{flow.get('entry_method', '')}".rstrip(":"),
            entry_source=entry_source[: MAX_REF_CHARS_PER_NODE * 4],
            route=flow.get("route", "") or "",
            http_methods=http_methods_str,
            message_class=flow.get("message_class", "") or "",
            event_name=flow.get("event_name", "") or "",
            command_name=flow.get("command_name", "") or "",
            entry_file=method.file or "",
            referenced_chunks=referenced,
            emits_messages=ctx["emits_messages"],
            emits_events=ctx["emits_events"],
            http_calls=ctx["http_calls"],
            triggered_by_messages=ctx["triggered_by_messages"],
            triggered_by_events=ctx["triggered_by_events"],
        )
        if not explanation:
            raise ValueError(f"Empty explanation for flow {flow_id}")

        self._store_explanation(flow_id, explanation)

        # Embed
        meta = {
            "flow_id": flow_id,
            "kind": "Flow",
            "type": flow["type"],
            "fqn": flow.get("entry_fqn", ""),
            "name": flow["name"],
            "file": method.file or "",
            "project": self._config.project_name,
            "node_id": flow_id,  # used for dedupe in search merge
            "chunk_index": 0,
        }
        docs = make_embed_documents([explanation], [meta])
        pipes.embed.run({"embedder": {"documents": docs}})

        elapsed = time.perf_counter() - start
        logger.info("  Done flow %s in %.1fs (refs=%d)", flow_id, elapsed, len(referenced))
        return {
            "flow_id": flow_id,
            "explanation": explanation,
            "referenced_count": len(referenced),
            "elapsed_ms": int(elapsed * 1000),
        }

    # ── Helpers ──────────────────────────────────────────────────

    def _fetch_flows(self, force: bool) -> list[dict]:
        cypher = (
            "MATCH (f:Flow) "
            + ("" if force else "WHERE f.explanation IS NULL ")
            + "RETURN f ORDER BY f.type, f.flow_id"
        )
        rows = self._runner.execute(cypher)
        return [self._flow_record_to_dict(row["f"]) for row in rows]

    @staticmethod
    def _flow_record_to_dict(record) -> dict:
        return {
            "flow_id": record["flow_id"],
            "type": record.get("type", ""),
            "name": record.get("name", ""),
            "entry_fqn": record.get("entry_fqn", ""),
            "entry_method": record.get("entry_method", ""),
            "explanation": record.get("explanation"),
            "route": record.get("route"),
            "http_methods": list(record.get("http_methods") or []),
            "message_class": record.get("message_class"),
            "event_name": record.get("event_name"),
            "command_name": record.get("command_name"),
        }

    def _gather_dispatch_context(self, flow_id: str) -> dict:
        """Gather both directions of the dispatch graph for ``flow_id``.

        Returns a dict with five lists shaped per the v3 plan's interface contract.
        Empty ``collect(DISTINCT ...)`` results in Cypher come back as a single-item
        list with all-null fields; those are stripped here so the prompt template
        receives clean empty lists.
        """
        cypher = """
        MATCH (f:Flow {flow_id: $fid})
        OPTIONAL MATCH (f)-[em:EMITS]->(m:Message)
        OPTIONAL MATCH (f)-[ee:EMITS]->(e:Event)
        OPTIONAL MATCH (f)-[uh:USES_HTTP_CLIENT]->(h:HttpClient)
        OPTIONAL MATCH (mIn:Message)-[:HANDLED_BY]->(f)
        OPTIONAL MATCH (eIn:Event)-[r:HANDLED_BY]->(f)
        RETURN
          collect(DISTINCT {fqn: m.fqn, transports: m.transports, caller: em.caller_method_fqn}) AS emits_messages,
          collect(DISTINCT {fqn: e.fqn, caller: ee.caller_method_fqn})                            AS emits_events,
          collect(DISTINCT {service_id: h.service_id, base_uri: h.base_uri, class: h.class_fqn,
                            caller: uh.caller_method_fqn})                                        AS http_calls,
          collect(DISTINCT {fqn: mIn.fqn})                                                        AS triggered_by_messages,
          collect(DISTINCT {fqn: eIn.fqn, priority: r.priority})                                  AS triggered_by_events
        """
        record = self._runner.execute_single(cypher, fid=flow_id)
        if record is None:
            return {
                "emits_messages": [],
                "emits_events": [],
                "http_calls": [],
                "triggered_by_messages": [],
                "triggered_by_events": [],
            }

        def _filter(items: list[dict], key: str) -> list[dict]:
            return [it for it in (items or []) if it.get(key) is not None]

        return {
            "emits_messages": _filter(record["emits_messages"], "fqn"),
            "emits_events": _filter(record["emits_events"], "fqn"),
            "http_calls": _filter(record["http_calls"], "service_id"),
            "triggered_by_messages": _filter(record["triggered_by_messages"], "fqn"),
            "triggered_by_events": _filter(record["triggered_by_events"], "fqn"),
        }

    def _fetch_entry_method(self, flow_id: str) -> NodeData | None:
        record = self._runner.execute_single(
            "MATCH (:Flow {flow_id: $fid})-[:FLOW_ENTRY]->(m:Node) RETURN m",
            fid=flow_id,
        )
        if not record or not record["m"]:
            return None
        return record_to_node(record, key="m")

    def _resolve_entry_method_fallback(self, flow: dict) -> NodeData | None:
        """Resolve via symbol cascade when FLOW_ENTRY is missing.

        Some flow entries (e.g. CLI commands inheriting `execute()` from a
        base class) won't have a FLOW_ENTRY edge because the SoT didn't
        materialize the method node. Try the class FQN as a last resort.
        """
        from ..db.queries.resolve import resolve_symbol

        class_fqn = flow.get("entry_fqn") or ""
        method_name = flow.get("entry_method") or ""
        candidates: list[str] = []
        if class_fqn and method_name:
            candidates.append(f"{class_fqn}::{method_name}")
        if class_fqn:
            candidates.append(class_fqn)
        for q in candidates:
            results = resolve_symbol(self._runner, q)
            if results:
                logger.warning(
                    "Flow entry resolved via fallback (%s -> %s) — FLOW_ENTRY edge missing",
                    q,
                    results[0].fqn,
                )
                return results[0]
        return None

    def _gather_context_chunks(self, entry_method: NodeData) -> list[dict]:
        """Walk depth-3 context with implementations and return source chunks for each referenced node.

        Returns a list of {"fqn", "kind", "code"} dicts, capped at MAX_REFERENCED_NODES,
        ordered by depth ASC (closest first).
        """
        from ..orchestration.context import execute_context

        try:
            result = execute_context(
                self._runner,
                entry_method.fqn,
                depth=CONTEXT_DEPTH,
                limit=CONTEXT_LIMIT,
                include_impl=True,
            )
        except Exception as exc:
            logger.warning(
                "Context walk failed for %s: %s — proceeding with entry source only",
                entry_method.fqn,
                exc,
            )
            return []

        seen_ids: set[str] = {entry_method.node_id}
        candidates: list[tuple[int, ContextEntry]] = []
        for entry in result.used_by or []:
            self._collect_context_entries(entry, seen_ids, candidates)
        for entry in result.uses or []:
            self._collect_context_entries(entry, seen_ids, candidates)

        candidates.sort(key=lambda pair: pair[0])

        chunks: list[dict] = []
        for _, entry in candidates:
            if len(chunks) >= MAX_REFERENCED_NODES:
                break
            node = self._fetch_node(entry.node_id)
            if node is None or not node.file:
                continue
            src = self._reader.read_node_source(node)
            if not src:
                continue
            if len(src) > MAX_REF_CHARS_PER_NODE:
                src = src[:MAX_REF_CHARS_PER_NODE] + "\n... (truncated)"
            chunks.append({"fqn": entry.fqn, "kind": entry.kind or "", "code": src})

        return chunks

    def _collect_context_entries(
        self, entry: ContextEntry, seen_ids: set[str], out: list[tuple[int, ContextEntry]]
    ) -> None:
        if entry.node_id and entry.node_id not in seen_ids:
            seen_ids.add(entry.node_id)
            out.append((entry.depth, entry))
        for child in entry.children or []:
            self._collect_context_entries(child, seen_ids, out)
        for impl in entry.implementations or []:
            self._collect_context_entries(impl, seen_ids, out)

    def _fetch_node(self, node_id: str) -> NodeData | None:
        record = self._runner.execute_single("MATCH (n:Node {node_id: $nid}) RETURN n", nid=node_id)
        if not record or not record["n"]:
            return None
        return record_to_node(record)

    def _store_explanation(self, flow_id: str, explanation: str) -> None:
        self._runner.execute(
            """
            MATCH (f:Flow {flow_id: $fid})
            SET f.explanation = $explanation,
                f.explain_model = $model,
                f.explain_at = datetime()
            """,
            fid=flow_id,
            explanation=explanation,
            model=self._config.llm.model,
        )


def get_flow_enrichment_status(connection: Neo4jConnection) -> dict:
    """Return counts of total / enriched / pending flows."""
    with connection.session() as session:
        total = session.run("MATCH (f:Flow) RETURN count(f) AS c").single()["c"]
        enriched = session.run(
            "MATCH (f:Flow) WHERE f.explanation IS NOT NULL RETURN count(f) AS c"
        ).single()["c"]
    return {
        "total": total,
        "enriched": enriched,
        "pending": total - enriched,
    }
