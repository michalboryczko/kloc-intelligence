"""Batch enrichment orchestrator for generating explanations and embeddings."""

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from ..db.query_runner import QueryRunner
from ..db.result_mapper import record_to_node, records_to_nodes
from ..models.node import NodeData
from .chunker import CodeChunker
from .config import AIConfig
from .source_reader import SourceReader

logger = logging.getLogger(__name__)

ENRICHABLE_KINDS = ["Class", "Method"]


@dataclass
class EnrichmentProgress:
    total: int = 0
    processed: int = 0
    skipped: int = 0
    failed: int = 0
    failed_nodes: list[str] = field(default_factory=list)


class Enricher:
    """Orchestrates explanation generation and embedding for Class/Method nodes.

    Methods get context from their argument type hints and return type.
    Classes get context from parent classes/interfaces and first-level usages.
    """

    def __init__(self, runner: QueryRunner, config: AIConfig):
        self._runner = runner
        self._config = config
        self._reader = SourceReader(config.project_root)
        self._chunker = CodeChunker(max_tokens=config.max_tokens_per_chunk)
        # Lazy-init pipelines
        self._method_explain_pipeline = None
        self._class_explain_pipeline = None
        self._code_embed_pipeline = None
        self._explain_embed_pipeline = None

    def _init_pipelines(self):
        """Lazy-initialize Haystack pipelines."""
        if self._method_explain_pipeline is not None:
            return
        from .pipelines import build_embed_pipeline, build_explain_pipeline

        self._method_explain_pipeline = build_explain_pipeline(self._config, kind="Method")
        self._class_explain_pipeline = build_explain_pipeline(self._config, kind="Class")
        self._code_embed_pipeline = build_embed_pipeline(self._config, "code_embeddings")
        self._explain_embed_pipeline = build_embed_pipeline(self._config, "explain_embeddings")

    def enrich_all(
        self,
        force: bool = False,
        kinds: list[str] | None = None,
        batch_size: int = 10,
        callback: Callable[[EnrichmentProgress], None] | None = None,
    ) -> EnrichmentProgress:
        """Batch enrich all Class/Method nodes with explanations and embeddings."""
        self._init_pipelines()
        target_kinds = kinds or ENRICHABLE_KINDS
        progress = EnrichmentProgress()

        nodes = self._get_enrichable_nodes(target_kinds, force)
        progress.total = len(nodes)

        if callback:
            callback(progress)

        for i in range(0, len(nodes), batch_size):
            batch = nodes[i : i + batch_size]
            for node in batch:
                try:
                    result = self._enrich_single(node, force)
                    if result.get("skipped"):
                        progress.skipped += 1
                    else:
                        progress.processed += 1
                except Exception as e:
                    logger.error("Failed to enrich %s: %s", node.fqn, e)
                    progress.failed += 1
                    progress.failed_nodes.append(node.fqn)

                if callback:
                    callback(progress)

        return progress

    def enrich_node(self, node_id: str, force: bool = False) -> dict:
        """Enrich a single node by node_id."""
        self._init_pipelines()
        record = self._runner.execute_single(
            "MATCH (n:Node {node_id: $node_id}) RETURN n", node_id=node_id
        )
        if not record:
            return {"error": f"Node {node_id} not found"}

        node = record_to_node(record)
        if node.kind not in ENRICHABLE_KINDS:
            return {"error": f"Node kind {node.kind} is not enrichable (need Class or Method)"}

        return self._enrich_single(node, force)

    def get_status(self) -> dict:
        """Get enrichment status: total, enriched, pending counts per kind."""
        records = self._runner.execute(
            """
            MATCH (n:Node)
            WHERE n.kind IN $kinds AND n.file IS NOT NULL
            RETURN n.kind AS kind,
                   count(n) AS total,
                   count(n.explanation) AS enriched
            ORDER BY n.kind
            """,
            kinds=ENRICHABLE_KINDS,
        )
        stats = {}
        total_all = 0
        enriched_all = 0
        for r in records:
            kind = r["kind"]
            total = r["total"]
            enriched = r["enriched"]
            stats[kind] = {"total": total, "enriched": enriched, "pending": total - enriched}
            total_all += total
            enriched_all += enriched

        return {
            "kinds": stats,
            "total": total_all,
            "enriched": enriched_all,
            "pending": total_all - enriched_all,
        }

    # ── Core enrichment ──────────────────────────────────────────

    def _enrich_single(self, node: NodeData, force: bool) -> dict:
        """Enrich a single node with context-aware explanation + embeddings."""
        from .pipelines import make_embed_documents

        if not force and self._has_explanation(node.node_id):
            logger.debug("Skipping %s (already enriched)", node.fqn)
            return {"skipped": True, "node_id": node.node_id, "fqn": node.fqn}

        logger.info("Enriching %s %s [%s]", node.kind, node.fqn, node.node_id)

        source = self._reader.read_node_source(node)
        if not source:
            raise ValueError(f"Cannot read source for {node.fqn} (file: {node.file})")

        logger.debug("  Source: %d chars (~%d tokens)", len(source), len(source) // 4)
        start = time.perf_counter()

        # Generate explanation with context
        if node.kind == "Method":
            explanation = self._explain_method(node, source)
        else:
            explanation = self._explain_class(node, source)

        llm_elapsed = time.perf_counter() - start
        if not explanation:
            raise ValueError(f"Empty explanation for {node.fqn}")

        logger.debug("  LLM explanation: %d chars in %.1fs", len(explanation), llm_elapsed)
        self._store_explanation(node.node_id, explanation)
        logger.debug("  Stored explanation in Neo4j")

        # Code embedding: full source (method body or whole class)
        if node.kind in ("Class", "Interface", "Trait", "Enum"):
            method_sources = self._get_method_sources(node)
            chunks = self._chunker.chunk_node(node, source, method_sources)
        else:
            chunks = self._chunker.chunk_node(node, source)

        base_meta = {
            "node_id": node.node_id,
            "kind": node.kind,
            "fqn": node.fqn,
            "name": node.name,
            "file": node.file or "",
            "project": self._config.project_name,
        }

        # Embed source code chunks
        embed_start = time.perf_counter()
        code_docs = make_embed_documents(
            [c.content for c in chunks],
            [{**base_meta, "chunk_index": c.chunk_index} for c in chunks],
        )
        self._code_embed_pipeline.run({"embedder": {"documents": code_docs}})
        logger.debug(
            "  Code embedding: %d chunk(s) in %.1fs", len(chunks), time.perf_counter() - embed_start
        )

        # Embed explanation
        explain_start = time.perf_counter()
        explain_docs = make_embed_documents([explanation], [{**base_meta, "chunk_index": 0}])
        self._explain_embed_pipeline.run({"embedder": {"documents": explain_docs}})
        logger.debug("  Explain embedding: 1 doc in %.1fs", time.perf_counter() - explain_start)

        elapsed = time.perf_counter() - start
        logger.info("  Done %s in %.1fs (llm=%.1fs)", node.fqn, elapsed, llm_elapsed)

        return {
            "node_id": node.node_id,
            "fqn": node.fqn,
            "kind": node.kind,
            "explanation": explanation,
            "chunks": len(chunks),
            "elapsed_ms": int(elapsed * 1000),
        }

    # ── Method explanation with type context ─────────────────────

    def _explain_method(self, node: NodeData, source: str) -> str:
        """Generate explanation for a method with argument/return type context."""
        from .pipelines import run_explain_method

        type_context = self._gather_method_type_context(node)
        logger.debug("  Method type context: %d type(s)", len(type_context))
        for tc in type_context:
            logger.debug("    - %s %s (%d chars)", tc["kind"], tc["fqn"], len(tc["code"]))
        return run_explain_method(
            self._method_explain_pipeline,
            source_code=source,
            fqn=node.fqn,
            signature=node.signature,
            type_context=type_context,
        )

    def _gather_method_type_context(self, method_node: NodeData) -> list[dict]:
        """Gather source code of classes/interfaces referenced by a method.

        Uses METHOD -[:USES]-> CLASS/INTERFACE edges (sot.json v2.0).
        This captures argument types, return types, and any other class references.
        """
        context = []
        seen_fqns = set()

        records = self._runner.execute(
            """
            MATCH (m:Node {node_id: $mid})-[:USES]->(t:Node)
            WHERE t.kind IN ['Class', 'Interface', 'Trait', 'Enum'] AND t.file IS NOT NULL
            RETURN DISTINCT t
            """,
            mid=method_node.node_id,
        )
        for rec in records:
            t = record_to_node(rec, key="t")
            if t.fqn not in seen_fqns:
                code = self._reader.read_node_source(t)
                if code:
                    context.append({"fqn": t.fqn, "kind": t.kind, "code": code})
                    seen_fqns.add(t.fqn)

        return context

    # ── Class explanation with parent + usage context ────────────

    def _explain_class(self, node: NodeData, source: str) -> str:
        """Generate explanation for a class with inheritance and usage context."""
        from .pipelines import run_explain_class

        parent_context = self._gather_class_parent_context(node)
        usage_context = self._gather_class_usage_context(node)
        logger.debug("  Class parent context: %d parent(s)", len(parent_context))
        for pc in parent_context:
            logger.debug("    - %s %s (%d chars)", pc["kind"], pc["fqn"], len(pc["code"]))
        logger.debug("  Class usage context: %d user(s)", len(usage_context))
        for uc in usage_context:
            logger.debug("    - %s %s (%d chars)", uc["kind"], uc["fqn"], len(uc["code"]))
        return run_explain_class(
            self._class_explain_pipeline,
            source_code=source,
            fqn=node.fqn,
            parent_context=parent_context,
            usage_context=usage_context,
        )

    def _gather_class_parent_context(self, class_node: NodeData) -> list[dict]:
        """Gather source code of classes/interfaces this class extends or implements.

        Traverses:
        - CLASS -[:EXTENDS]-> PARENT_CLASS
        - CLASS -[:IMPLEMENTS]-> INTERFACE
        """
        records = self._runner.execute(
            """
            MATCH (c:Node {node_id: $cid})-[:EXTENDS|IMPLEMENTS]->(p:Node)
            WHERE p.file IS NOT NULL
            RETURN DISTINCT p
            """,
            cid=class_node.node_id,
        )
        context = []
        for rec in records:
            p = record_to_node(rec, key="p")
            code = self._reader.read_node_source(p)
            if code:
                context.append({"fqn": p.fqn, "kind": p.kind, "code": code})
        return context

    def _gather_class_usage_context(self, class_node: NodeData, limit: int = 5) -> list[dict]:
        """Gather source code of first-level users of this class.

        Traverses incoming USES edges and finds the owner class/method.
        Limited to `limit` entries to keep prompt size reasonable.
        """
        records = self._runner.execute(
            """
            MATCH (user:Node)-[:USES]->(c:Node {node_id: $cid})
            WHERE user.kind IN ['Class', 'Method'] AND user.file IS NOT NULL
            RETURN DISTINCT user
            ORDER BY user.kind, user.fqn
            LIMIT $limit
            """,
            cid=class_node.node_id,
            limit=limit,
        )
        context = []
        seen_fqns = set()
        for rec in records:
            u = record_to_node(rec, key="user")
            if u.fqn not in seen_fqns:
                code = self._reader.read_node_source(u)
                if code:
                    context.append({"fqn": u.fqn, "kind": u.kind, "code": code})
                    seen_fqns.add(u.fqn)
        return context

    # ── Helpers ──────────────────────────────────────────────────

    def _get_enrichable_nodes(self, kinds: list[str], force: bool) -> list[NodeData]:
        """Query Neo4j for nodes that need enrichment."""
        if force:
            query = """
                MATCH (n:Node)
                WHERE n.kind IN $kinds AND n.file IS NOT NULL
                RETURN n ORDER BY n.kind, n.fqn
            """
        else:
            query = """
                MATCH (n:Node)
                WHERE n.kind IN $kinds AND n.file IS NOT NULL AND n.explanation IS NULL
                RETURN n ORDER BY n.kind, n.fqn
            """
        records = self._runner.execute(query, kinds=kinds)
        return records_to_nodes(records)

    def _has_explanation(self, node_id: str) -> bool:
        result = self._runner.execute_value(
            "MATCH (n:Node {node_id: $node_id}) RETURN n.explanation IS NOT NULL",
            node_id=node_id,
        )
        return bool(result)

    def _store_explanation(self, node_id: str, explanation: str) -> None:
        self._runner.execute(
            """
            MATCH (n:Node {node_id: $node_id})
            SET n.explanation = $explanation,
                n.explain_model = $model,
                n.explain_at = datetime()
            """,
            node_id=node_id,
            explanation=explanation,
            model=self._config.llm.model,
        )

    def _get_method_sources(self, class_node: NodeData) -> list[tuple[NodeData, str]]:
        """Get source code for all methods contained by a class."""
        records = self._runner.execute(
            """
            MATCH (c:Node {node_id: $class_id})-[:CONTAINS]->(m:Node)
            WHERE m.kind = 'Method'
            RETURN m ORDER BY m.start_line
            """,
            class_id=class_node.node_id,
        )
        results = []
        for m in records_to_nodes(records, key="m"):
            source = self._reader.read_node_source(m)
            if source:
                results.append((m, source))
        return results
