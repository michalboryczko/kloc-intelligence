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
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from ..db.connection import Neo4jConnection
from ..db.query_runner import QueryRunner
from ..db.result_mapper import record_to_node
from ..models.node import NodeData
from ..models.results import ContextEntry
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


class FlowEnricher:
    """Generate business-process summaries for :Flow nodes."""

    def __init__(self, runner: QueryRunner, config: AIConfig):
        self._runner = runner
        self._config = config
        self._reader = SourceReader(config.project_root)
        self._explain_pipeline = None
        self._embed_pipeline = None

    def _init_pipelines(self):
        if self._explain_pipeline is not None:
            return
        from .pipelines import build_embed_pipeline, build_explain_flow_pipeline
        self._explain_pipeline = build_explain_flow_pipeline(self._config)
        self._embed_pipeline = build_embed_pipeline(self._config, "flow_explain_embeddings")

    def enrich_all_flows(
        self,
        force: bool = False,
        callback: Optional[Callable[[FlowEnrichmentProgress], None]] = None,
    ) -> FlowEnrichmentProgress:
        self._init_pipelines()
        flows = self._fetch_flows(force)
        progress = FlowEnrichmentProgress(total=len(flows))
        if callback:
            callback(progress)

        for flow in flows:
            try:
                result = self._enrich_one(flow, force)
                if result.get("skipped"):
                    progress.skipped += 1
                else:
                    progress.processed += 1
            except Exception as e:
                logger.error("Failed to enrich flow %s: %s", flow["flow_id"], e)
                progress.failed += 1
                progress.failed_flows.append(flow["flow_id"])
            if callback:
                callback(progress)

        return progress

    def enrich_flow(self, flow_id: str, force: bool = False) -> dict:
        self._init_pipelines()
        record = self._runner.execute_single(
            "MATCH (f:Flow {flow_id: $fid}) RETURN f", fid=flow_id
        )
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

        method = self._fetch_entry_method(flow_id)
        if method is None:
            method = self._resolve_entry_method_fallback(flow)
        if method is None:
            raise ValueError(
                f"Flow {flow_id} has no FLOW_ENTRY edge and entry FQN "
                f"'{flow.get('entry_fqn','')}::{flow.get('entry_method','')}' "
                f"could not be resolved against the :Node graph"
            )

        entry_source = self._reader.read_node_source(method)
        if not entry_source:
            raise ValueError(
                f"Cannot read entry source for flow {flow_id} (file: {method.file})"
            )

        referenced = self._gather_context_chunks(method)

        explanation = run_explain_flow(
            self._explain_pipeline,
            flow_type=flow["type"],
            flow_name=flow["name"],
            entry_fqn=f"{flow.get('entry_fqn','')}::{flow.get('entry_method','')}".rstrip(":"),
            entry_source=entry_source[:MAX_REF_CHARS_PER_NODE * 4],
            route=flow.get("route", "") or "",
            message_class=flow.get("message_class", "") or "",
            event_name=flow.get("event_name", "") or "",
            command_name=flow.get("command_name", "") or "",
            referenced_chunks=referenced,
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
        self._embed_pipeline.run({"embedder": {"documents": docs}})

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
            "message_class": record.get("message_class"),
            "event_name": record.get("event_name"),
            "command_name": record.get("command_name"),
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
                    q, results[0].fqn,
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
                entry_method.fqn, exc,
            )
            return []

        seen_ids: set[str] = {entry_method.node_id}
        candidates: list[tuple[int, ContextEntry]] = []
        for entry in (result.used_by or []):
            self._collect_context_entries(entry, seen_ids, candidates)
        for entry in (result.uses or []):
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
        for child in (entry.children or []):
            self._collect_context_entries(child, seen_ids, out)
        for impl in (entry.implementations or []):
            self._collect_context_entries(impl, seen_ids, out)

    def _fetch_node(self, node_id: str) -> NodeData | None:
        record = self._runner.execute_single(
            "MATCH (n:Node {node_id: $nid}) RETURN n", nid=node_id
        )
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


def clear_flow_explain_collection(qdrant_url: str, qdrant_api_key: str | None = None) -> None:
    """Drop the flow_explain_embeddings collection. Idempotent."""
    try:
        from qdrant_client import QdrantClient
        client = QdrantClient(url=qdrant_url, api_key=qdrant_api_key)
        try:
            client.delete_collection("flow_explain_embeddings")
        except Exception:
            pass
        client.close()
    except Exception as exc:
        logger.warning("Could not drop flow_explain_embeddings collection: %s", exc)


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
