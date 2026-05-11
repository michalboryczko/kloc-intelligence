"""AI configuration for kloc-intelligence enrichment and search.

LLM and embedding providers are configured independently — each operation
(enrich/explain uses LLM; enrich/search uses embeddings) can point at a
different OpenAI-compatible endpoint.
"""

import os
from dataclasses import dataclass, field

DEFAULT_LLM_API_URL = "https://openrouter.ai/api/v1"
DEFAULT_LLM_MODEL = "minimax/minimax-m2.7"
DEFAULT_EMBEDDING_API_URL = "https://openrouter.ai/api/v1"
DEFAULT_EMBEDDING_MODEL = "qwen/qwen3-embedding-8b"
DEFAULT_EMBEDDING_DIMENSION = 4096


@dataclass
class LLMProviderConfig:
    """Provider config for the chat/explanation LLM."""

    api_url: str = DEFAULT_LLM_API_URL
    api_key: str = ""
    model: str = DEFAULT_LLM_MODEL


@dataclass
class EmbeddingProviderConfig:
    """Provider config for the embedding model used by enrich + search."""

    api_url: str = DEFAULT_EMBEDDING_API_URL
    api_key: str = ""
    model: str = DEFAULT_EMBEDDING_MODEL
    dimension: int = DEFAULT_EMBEDDING_DIMENSION


@dataclass
class AIConfig:
    """Configuration for AI features (LLM, embeddings, Qdrant)."""

    llm: LLMProviderConfig = field(default_factory=LLMProviderConfig)
    embedding: EmbeddingProviderConfig = field(default_factory=EmbeddingProviderConfig)

    qdrant_url: str = "http://localhost:6333"
    project_root: str = ""
    project_name: str = "default"
    max_tokens_per_chunk: int = 8000
    enrich_concurrency: int = 10
    enrich_flows_concurrency: int = 10

    @classmethod
    def from_env(cls) -> "AIConfig":
        return cls(
            llm=LLMProviderConfig(
                api_url=os.getenv("LLM_API_URL", DEFAULT_LLM_API_URL),
                api_key=os.getenv("LLM_API_KEY", ""),
                model=os.getenv("LLM_MODEL", DEFAULT_LLM_MODEL),
            ),
            embedding=EmbeddingProviderConfig(
                api_url=os.getenv("EMBEDDING_API_URL", DEFAULT_EMBEDDING_API_URL),
                api_key=os.getenv("EMBEDDING_API_KEY", ""),
                model=os.getenv("EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL),
                dimension=int(os.getenv("EMBEDDING_DIMENSION", str(DEFAULT_EMBEDDING_DIMENSION))),
            ),
            qdrant_url=os.getenv("QDRANT_URL", "http://localhost:6333"),
            project_root=os.getenv("KLOC_PROJECT_ROOT", ""),
            project_name=os.getenv("KLOC_PROJECT_NAME", "default"),
            max_tokens_per_chunk=int(os.getenv("KLOC_MAX_TOKENS_PER_CHUNK", "8000")),
            enrich_concurrency=int(os.getenv("ENRICH_CONCURRENCY", "10")),
            enrich_flows_concurrency=int(os.getenv("ENRICH_FLOWS_CONCURRENCY", "10")),
        )

    def validate(self, *, require_llm: bool = True, require_embedding: bool = True) -> list[str]:
        """Return list of validation errors per requested operation.

        Callers narrow validation to only the providers they actually use,
        e.g. `search` calls validate(require_llm=False).
        """
        errors: list[str] = []
        if require_llm and not self.llm.api_key:
            errors.append("LLM_API_KEY is required for explain/enrich")
        if require_embedding and not self.embedding.api_key:
            errors.append("EMBEDDING_API_KEY is required for embedding/search")
        if not self.project_root:
            errors.append("KLOC_PROJECT_ROOT is required (path to PHP source files)")
        return errors
