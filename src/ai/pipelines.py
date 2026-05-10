"""Haystack pipelines for code explanation, embedding, and search."""

import logging

from haystack import Document, Pipeline
from haystack.components.builders import ChatPromptBuilder
from haystack.components.embedders import OpenAIDocumentEmbedder, OpenAITextEmbedder
from haystack.components.generators.chat import OpenAIChatGenerator
from haystack.components.writers import DocumentWriter
from haystack.dataclasses import ChatMessage
from haystack.utils import Secret
from haystack_integrations.document_stores.qdrant import QdrantDocumentStore

from .config import AIConfig

logger = logging.getLogger(__name__)


# ── Prompts ──────────────────────────────────────────────────────

EXPLAIN_SYSTEM_PROMPT = """You are a code documentation expert. Analyze the provided PHP code and \
describe what it does in clear, concise human language.

Focus on:
- The purpose and responsibility of this code
- Key behaviors and side effects
- What inputs it accepts and what it returns (for methods)
- Important design patterns or business logic

Provide a description in 2-5 sentences. Do not include code snippets in your response."""

# ── Flow explanation prompts ─────────────────────────────────

FLOW_BUSINESS_SYSTEM = """You are a business analyst documenting software systems. \
Analyze the provided architectural flow and describe the business process it implements."""

FLOW_BUSINESS_TEMPLATE = """Describe the business process this flow represents from a user/customer perspective.

Flow diagram:
{{ diagram }}

{{ code_context }}

Focus on:
- What business action does this flow handle? (e.g., "creates a new order", "retrieves customer data")
- What are the steps from the user's perspective?
- What data flows through the system?
- What side effects occur? (emails sent, events dispatched, data persisted)

Provide 3-6 sentences. Do not include code snippets."""

FLOW_TECHNICAL_SYSTEM = """You are a software architect documenting system architecture. \
Analyze the provided flow and describe its technical implementation."""

FLOW_TECHNICAL_TEMPLATE = """Describe the technical architecture of this flow.

Flow diagram:
{{ diagram }}

{{ code_context }}

Focus on:
- What architectural layers are involved? (controller, service, repository, component)
- What patterns are used? (DI, strategy, template method, async messaging, event-driven)
- What are the async boundaries? (message bus dispatches, event dispatcher)
- How is data transformed between layers? (DTOs, entities, value objects)

Provide 3-6 sentences. Do not include code snippets."""

FLOW_SEARCH_SYSTEM = """You are an AI coding assistant indexer. \
Write a short description optimized for search retrieval by AI coding agents."""

FLOW_SEARCH_TEMPLATE = """Write a search-optimized description for this flow that an AI coding agent would use to find it.

Flow diagram:
{{ diagram }}

{{ code_context }}

Include: key action verbs, domain concepts, HTTP endpoints (if any), entity names, and what problem this flow solves.
Write 2-3 sentences. Use natural language phrases that a developer would search for."""

FLOW_LABELS_SYSTEM = """You are a code classification system. Generate labels/tags for code flows."""

FLOW_LABELS_TEMPLATE = """Generate a JSON array of 5-15 labels/tags for this flow.

Flow diagram:
{{ diagram }}

Include labels for:
- Domain concepts (e.g., "order-management", "customer-data")
- Technical patterns (e.g., "async-messaging", "cqrs", "repository-pattern")
- HTTP verbs if applicable (e.g., "GET", "POST")
- Entity names (e.g., "Order", "Customer")
- Action verbs (e.g., "create", "retrieve", "notify")
- Layer names (e.g., "controller", "service", "handler")

Return ONLY a JSON array of strings, no other text."""

EXPLAIN_METHOD_TEMPLATE = """Analyze this PHP method and describe what it does:

FQN: {{ fqn }}
{% if signature %}Signature: {{ signature }}{% endif %}

Method code:
```php
{{ source_code }}
```
{% if type_context %}
Reference code for argument/return types used by this method:
{% for tc in type_context %}
--- {{ tc.fqn }} ({{ tc.kind }}) ---
```php
{{ tc.code }}
```
{% endfor %}
{% endif %}"""

EXPLAIN_CLASS_TEMPLATE = """Analyze this PHP class and describe what it does:

FQN: {{ fqn }}

Class code:
```php
{{ source_code }}
```
{% if parent_context %}
Parent classes/interfaces this class extends or implements:
{% for pc in parent_context %}
--- {{ pc.fqn }} ({{ pc.kind }}) ---
```php
{{ pc.code }}
```
{% endfor %}
{% endif %}
{% if usage_context %}
Key classes and methods that use this class (first-level dependents):
{% for uc in usage_context %}
--- {{ uc.fqn }} ({{ uc.kind }}) ---
```php
{{ uc.code }}
```
{% endfor %}
{% endif %}"""


# ── Qdrant helpers ───────────────────────────────────────────────

def _make_qdrant_store(config: AIConfig, collection: str) -> QdrantDocumentStore:
    """Create a QdrantDocumentStore for a given collection."""
    return QdrantDocumentStore(
        url=config.qdrant_url,
        index=collection,
        embedding_dim=config.embedding_dimension,
        recreate_index=False,
        return_embedding=False,
        hnsw_config=None,
    )


# ── Explain pipeline ────────────────────────────────────────────

def build_explain_pipeline(config: AIConfig, kind: str = "Method") -> Pipeline:
    """Build pipeline that generates human-language explanation.

    Args:
        kind: "Method" or "Class" — selects the appropriate prompt template.
    """
    pipeline = Pipeline()

    template_str = EXPLAIN_METHOD_TEMPLATE if kind == "Method" else EXPLAIN_CLASS_TEMPLATE
    required = ["source_code", "fqn"]

    prompt_builder = ChatPromptBuilder(
        template=[
            ChatMessage.from_system(EXPLAIN_SYSTEM_PROMPT),
            ChatMessage.from_user(template_str),
        ],
        required_variables=required,
    )

    generator = OpenAIChatGenerator(
        api_key=Secret.from_token(config.openrouter_api_key),
        api_base_url=config.openrouter_base_url,
        model=config.llm_model,
        generation_kwargs={"max_tokens": 1024, "temperature": 0.3},
    )

    pipeline.add_component("prompt_builder", prompt_builder)
    pipeline.add_component("generator", generator)
    pipeline.connect("prompt_builder.prompt", "generator.messages")

    return pipeline


def _render_and_log_prompt(template_messages: list[ChatMessage], variables: dict) -> None:
    """Render Jinja2 templates with variables and log the result."""
    from jinja2 import Environment
    env = Environment()
    for msg in template_messages:
        role = msg.role.value if hasattr(msg.role, "value") else msg.role
        try:
            rendered = env.from_string(msg.text).render(**variables)
        except Exception:
            rendered = msg.text
        logger.debug("  LLM prompt [%s]:\n%s", role, rendered)


def _log_response(result: dict) -> str:
    """Log raw LLM response. Returns response text."""
    replies = result.get("generator", {}).get("replies", [])
    if replies:
        text = replies[0].text.strip()
        logger.debug("  LLM response (%d chars):\n%s", len(text), text)
        return text
    logger.debug("  LLM response: (empty)")
    return ""


def run_explain_method(
    pipeline: Pipeline,
    source_code: str,
    fqn: str,
    signature: str | None = None,
    type_context: list[dict] | None = None,
) -> str:
    """Run explain pipeline for a method.

    type_context: list of {"fqn": str, "kind": str, "code": str} for argument/return types.
    """
    variables = {
        "source_code": source_code,
        "fqn": fqn,
        "signature": signature or "",
        "type_context": type_context or [],
    }
    logger.debug("  LLM call: method explain for %s (source=%d chars, type_context=%d)",
                 fqn, len(source_code), len(type_context or []))
    _render_and_log_prompt(
        [ChatMessage.from_system(EXPLAIN_SYSTEM_PROMPT), ChatMessage.from_user(EXPLAIN_METHOD_TEMPLATE)],
        variables,
    )
    result = pipeline.run({"prompt_builder": variables})
    return _log_response(result)


def run_explain_class(
    pipeline: Pipeline,
    source_code: str,
    fqn: str,
    parent_context: list[dict] | None = None,
    usage_context: list[dict] | None = None,
) -> str:
    """Run explain pipeline for a class.

    parent_context: list of {"fqn": str, "kind": str, "code": str} for extends/implements.
    usage_context: list of {"fqn": str, "kind": str, "code": str} for first-level users.
    """
    variables = {
        "source_code": source_code,
        "fqn": fqn,
        "parent_context": parent_context or [],
        "usage_context": usage_context or [],
    }
    logger.debug("  LLM call: class explain for %s (source=%d chars, parents=%d, usages=%d)",
                 fqn, len(source_code), len(parent_context or []), len(usage_context or []))
    _render_and_log_prompt(
        [ChatMessage.from_system(EXPLAIN_SYSTEM_PROMPT), ChatMessage.from_user(EXPLAIN_CLASS_TEMPLATE)],
        variables,
    )
    result = pipeline.run({"prompt_builder": variables})
    return _log_response(result)


# ── Embed pipeline ──────────────────────────────────────────────

def build_embed_pipeline(config: AIConfig, collection: str) -> Pipeline:
    """Build pipeline that embeds documents and writes to Qdrant."""
    pipeline = Pipeline()

    embedder = OpenAIDocumentEmbedder(
        api_key=Secret.from_token(config.openrouter_api_key),
        api_base_url=config.openrouter_base_url,
        model=config.embedding_model,
    )

    store = _make_qdrant_store(config, collection)
    writer = DocumentWriter(document_store=store)

    pipeline.add_component("embedder", embedder)
    pipeline.add_component("writer", writer)
    pipeline.connect("embedder.documents", "writer.documents")

    return pipeline


def make_embed_documents(
    texts: list[str],
    metadatas: list[dict],
) -> list[Document]:
    """Create Haystack Documents from texts and metadata for embedding."""
    docs = [Document(content=text, meta=meta) for text, meta in zip(texts, metadatas)]
    for doc in docs:
        logger.debug(
            "  Embed doc: node_id=%s chunk=%s (%d chars)\n%s",
            doc.meta.get("node_id", "?"),
            doc.meta.get("chunk_index", 0),
            len(doc.content),
            doc.content[:200] + ("..." if len(doc.content) > 200 else ""),
        )
    return docs


# ── Search pipeline ─────────────────────────────────────────────

def build_search_pipeline(config: AIConfig, collection: str) -> Pipeline:
    """Build pipeline that searches a Qdrant collection."""
    from haystack_integrations.components.retrievers.qdrant import QdrantEmbeddingRetriever

    pipeline = Pipeline()

    embedder = OpenAITextEmbedder(
        api_key=Secret.from_token(config.openrouter_api_key),
        api_base_url=config.openrouter_base_url,
        model=config.embedding_model,
    )

    store = _make_qdrant_store(config, collection)
    retriever = QdrantEmbeddingRetriever(document_store=store, top_k=10)

    pipeline.add_component("embedder", embedder)
    pipeline.add_component("retriever", retriever)
    pipeline.connect("embedder.embedding", "retriever.query_embedding")

    return pipeline


def run_search(
    pipeline: Pipeline,
    query: str,
    top_k: int = 10,
) -> list[dict]:
    """Run search pipeline and return results as dicts."""
    logger.debug("  Search query: '%s' (top_k=%d)", query, top_k)
    result = pipeline.run({
        "embedder": {"text": query},
        "retriever": {"top_k": top_k},
    })
    documents = result.get("retriever", {}).get("documents", [])
    logger.debug("  Search returned %d results", len(documents))
    for doc in documents:
        logger.debug("    %.3f %s %s", doc.score or 0, doc.meta.get("kind", "?"), doc.meta.get("fqn", "?"))
    return [
        {
            "score": doc.score or 0.0,
            "content": doc.content,
            "node_id": doc.meta.get("node_id", ""),
            "kind": doc.meta.get("kind", ""),
            "fqn": doc.meta.get("fqn", ""),
            "name": doc.meta.get("name", ""),
            "file": doc.meta.get("file"),
            "project": doc.meta.get("project", ""),
            "chunk_index": doc.meta.get("chunk_index", 0),
        }
        for doc in documents
    ]


ALL_SEARCH_COLLECTIONS = [
    "code_embeddings",
    "explain_embeddings",
    "flow_business_embeddings",
    "flow_technical_embeddings",
    "flow_search_embeddings",
]


def search_both_collections(
    config: AIConfig,
    query: str,
    limit: int = 10,
) -> list[dict]:
    """Search all embedding collections, merge and deduplicate."""
    all_hits = []
    for collection in ALL_SEARCH_COLLECTIONS:
        try:
            pipeline = build_search_pipeline(config, collection)
            hits = run_search(pipeline, query, top_k=limit)
            for hit in hits:
                hit["collection"] = collection
            all_hits.extend(hits)
        except Exception:
            # Collection may not exist yet
            pass

    # Deduplicate by node_id, keeping highest score
    seen: dict[str, dict] = {}
    for hit in all_hits:
        key = hit["node_id"]
        if key not in seen or hit["score"] > seen[key]["score"]:
            seen[key] = hit
    deduped = sorted(seen.values(), key=lambda h: h["score"], reverse=True)
    return deduped[:limit]
