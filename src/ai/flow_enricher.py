"""Flow enrichment: 4-type AI explanations + embeddings for architectural flows."""

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from haystack import Pipeline
from haystack.components.builders import ChatPromptBuilder
from haystack.components.generators.chat import OpenAIChatGenerator
from haystack.dataclasses import ChatMessage
from haystack.utils import Secret

from ..db.query_runner import QueryRunner
from .config import AIConfig
from .flow_diagram import FlowDiagramBuilder, FlowInfo
from .pipelines import (
    FLOW_BUSINESS_SYSTEM, FLOW_BUSINESS_TEMPLATE,
    FLOW_TECHNICAL_SYSTEM, FLOW_TECHNICAL_TEMPLATE,
    FLOW_SEARCH_SYSTEM, FLOW_SEARCH_TEMPLATE,
    FLOW_LABELS_SYSTEM, FLOW_LABELS_TEMPLATE,
    build_embed_pipeline, make_embed_documents,
)
from .source_reader import SourceReader

logger = logging.getLogger(__name__)

EXPLANATION_TYPES = [
    ("business", FLOW_BUSINESS_SYSTEM, FLOW_BUSINESS_TEMPLATE),
    ("technical", FLOW_TECHNICAL_SYSTEM, FLOW_TECHNICAL_TEMPLATE),
    ("search", FLOW_SEARCH_SYSTEM, FLOW_SEARCH_TEMPLATE),
    ("labels", FLOW_LABELS_SYSTEM, FLOW_LABELS_TEMPLATE),
]

FLOW_EMBED_COLLECTIONS = {
    "business": "flow_business_embeddings",
    "technical": "flow_technical_embeddings",
    "search": "flow_search_embeddings",
}


@dataclass
class FlowEnrichmentProgress:
    total: int = 0
    processed: int = 0
    skipped: int = 0
    failed: int = 0
    failed_flows: list[str] = field(default_factory=list)


class FlowEnricher:
    """Generates 4-type AI explanations and embeddings for flows."""

    def __init__(self, runner: QueryRunner, config: AIConfig):
        self._runner = runner
        self._config = config
        self._reader = SourceReader(config.project_root)
        self._diagram_builder = FlowDiagramBuilder(runner, self._reader)
        self._explain_pipelines: dict[str, Pipeline] = {}
        self._embed_pipelines: dict[str, Pipeline] = {}

    def _init_pipelines(self):
        if self._explain_pipelines:
            return

        for name, system_prompt, _ in EXPLANATION_TYPES:
            pipeline = Pipeline()
            prompt_builder = ChatPromptBuilder(
                template=[
                    ChatMessage.from_system(system_prompt),
                    ChatMessage.from_user("{{ user_prompt }}"),
                ],
                required_variables=["user_prompt"],
            )
            generator = OpenAIChatGenerator(
                api_key=Secret.from_token(self._config.openrouter_api_key),
                api_base_url=self._config.openrouter_base_url,
                model=self._config.llm_model,
                generation_kwargs={"max_tokens": 1024, "temperature": 0.3},
            )
            pipeline.add_component("prompt_builder", prompt_builder)
            pipeline.add_component("generator", generator)
            pipeline.connect("prompt_builder.prompt", "generator.messages")
            self._explain_pipelines[name] = pipeline

        for collection in FLOW_EMBED_COLLECTIONS.values():
            self._embed_pipelines[collection] = build_embed_pipeline(self._config, collection)

    def enrich_all(
        self,
        force: bool = False,
        callback: Optional[Callable[[FlowEnrichmentProgress], None]] = None,
    ) -> FlowEnrichmentProgress:
        """Enrich all app flows with 4-type explanations + embeddings."""
        self._init_pipelines()
        progress = FlowEnrichmentProgress()

        flow_ids = self._get_enrichable_flows(force)
        progress.total = len(flow_ids)

        if callback:
            callback(progress)

        for flow_id in flow_ids:
            try:
                result = self._enrich_single_flow(flow_id, force)
                if result.get("skipped"):
                    progress.skipped += 1
                else:
                    progress.processed += 1
            except Exception as e:
                logger.error("Failed to enrich flow %s: %s", flow_id, e)
                progress.failed += 1
                progress.failed_flows.append(flow_id)

            if callback:
                callback(progress)

        return progress

    def enrich_flow(self, flow_id: str, force: bool = False) -> dict:
        """Enrich a single flow."""
        self._init_pipelines()
        return self._enrich_single_flow(flow_id, force)

    def _get_enrichable_flows(self, force: bool) -> list[str]:
        """Get flow IDs that need enrichment."""
        if force:
            query = "MATCH (f:Flow) WHERE f.entry_fqn STARTS WITH 'App\\\\' RETURN f.flow_id AS fid ORDER BY f.flow_id"
        else:
            query = """
                MATCH (f:Flow)
                WHERE f.entry_fqn STARTS WITH 'App\\\\'
                  AND f.explanation_business IS NULL
                RETURN f.flow_id AS fid ORDER BY f.flow_id
            """
        records = self._runner.execute(query)
        return [r["fid"] for r in records]

    def _enrich_single_flow(self, flow_id: str, force: bool) -> dict:
        """Enrich one flow: resolve → diagram → 4 LLM calls → embeddings."""
        # Check if already enriched
        if not force:
            has = self._runner.execute_value(
                "MATCH (f:Flow {flow_id: $fid}) RETURN f.explanation_business IS NOT NULL",
                fid=flow_id,
            )
            if has:
                logger.debug("Skipping flow %s (already enriched)", flow_id)
                return {"skipped": True, "flow_id": flow_id}

        logger.info("Enriching flow %s", flow_id)
        start = time.perf_counter()

        # Resolve flow context
        info = self._diagram_builder.resolve_flow(flow_id)
        if not info:
            raise ValueError(f"Flow not found: {flow_id}")

        # Build diagram + code context
        diagram = self._diagram_builder.build_diagram(info)
        code_context = self._diagram_builder.build_code_context(info, max_tokens=self._config.max_tokens_per_chunk * 2)

        logger.debug("  Diagram:\n%s", diagram)
        logger.debug("  Code context: %d chars", len(code_context))

        # Run 4 LLM calls
        explanations = {}
        from jinja2 import Environment
        env = Environment()

        for name, system_prompt, user_template in EXPLANATION_TYPES:
            llm_start = time.perf_counter()

            # Render the user prompt template
            rendered = env.from_string(user_template).render(
                diagram=diagram,
                code_context=code_context,
            )

            logger.debug("  LLM call [%s]: %d chars prompt", name, len(rendered))
            logger.debug("  LLM prompt [system]:\n%s", system_prompt)
            logger.debug("  LLM prompt [user]:\n%s", rendered)

            result = self._explain_pipelines[name].run({
                "prompt_builder": {"user_prompt": rendered}
            })
            replies = result.get("generator", {}).get("replies", [])
            text = replies[0].text.strip() if replies else ""

            logger.debug("  LLM response [%s] (%d chars in %.1fs):\n%s",
                         name, len(text), time.perf_counter() - llm_start, text)
            explanations[name] = text

        # Parse labels (should be JSON array)
        labels = []
        try:
            raw_labels = explanations.get("labels", "[]")
            # Strip markdown code fences if present
            if "```" in raw_labels:
                raw_labels = raw_labels.split("```")[1]
                if raw_labels.startswith("json"):
                    raw_labels = raw_labels[4:]
            labels = json.loads(raw_labels)
            if not isinstance(labels, list):
                labels = []
        except (json.JSONDecodeError, IndexError):
            logger.warning("  Failed to parse labels for %s", flow_id)

        # Store in Neo4j
        self._runner.execute(
            """
            MATCH (f:Flow {flow_id: $fid})
            SET f.explanation_business = $business,
                f.explanation_technical = $technical,
                f.explanation_search = $search,
                f.labels = $labels,
                f.explain_model = $model,
                f.explain_at = datetime()
            """,
            fid=flow_id,
            business=explanations.get("business", ""),
            technical=explanations.get("technical", ""),
            search=explanations.get("search", ""),
            labels=json.dumps(labels),
            model=self._config.llm_model,
        )
        logger.debug("  Stored 4 explanations + labels in Neo4j")

        # Embeddings for business, technical, search (not labels)
        base_meta = {
            "node_id": flow_id,
            "kind": "Flow",
            "fqn": info.entry_fqn,
            "name": info.name,
            "file": "",
            "project": self._config.project_name,
            "chunk_index": 0,
            "flow_type": info.flow_type,
        }

        for explain_type, collection in FLOW_EMBED_COLLECTIONS.items():
            text = explanations.get(explain_type, "")
            if text:
                docs = make_embed_documents([text], [{**base_meta}])
                self._embed_pipelines[collection].run({"embedder": {"documents": docs}})
                logger.debug("  Embedded [%s] -> %s", explain_type, collection)

        elapsed = time.perf_counter() - start
        logger.info("  Done flow %s in %.1fs", flow_id, elapsed)

        return {
            "flow_id": flow_id,
            "flow_type": info.flow_type,
            "name": info.name,
            "explanations": {k: v[:100] + "..." for k, v in explanations.items()},
            "labels": labels,
            "elapsed_ms": int(elapsed * 1000),
        }

    def get_status(self) -> dict:
        """Get flow enrichment status."""
        records = self._runner.execute(
            """
            MATCH (f:Flow)
            WHERE f.entry_fqn STARTS WITH 'App\\\\'
            RETURN f.type AS type,
                   count(f) AS total,
                   count(f.explanation_business) AS enriched
            ORDER BY f.type
            """
        )
        stats = {}
        total_all = 0
        enriched_all = 0
        for r in records:
            t = r["type"]
            total = r["total"]
            enriched = r["enriched"]
            stats[t] = {"total": total, "enriched": enriched, "pending": total - enriched}
            total_all += total
            enriched_all += enriched

        return {
            "types": stats,
            "total": total_all,
            "enriched": enriched_all,
            "pending": total_all - enriched_all,
        }
