"""AI configuration for kloc-intelligence enrichment and search."""

import os
from dataclasses import dataclass


@dataclass
class AIConfig:
    """Configuration for AI features (LLM, embeddings, Qdrant)."""

    # OpenRouter (unified API for both LLM and embeddings)
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    # LLM for explanations
    llm_model: str = "minimax/minimax-m2.7"

    # Embeddings
    embedding_model: str = "qwen/qwen3-embedding-8b"
    embedding_dimension: int = 4096

    # Qdrant
    qdrant_url: str = "http://localhost:6333"

    # Project
    project_root: str = ""
    project_name: str = "default"

    # Chunking
    max_tokens_per_chunk: int = 8000

    @classmethod
    def from_env(cls) -> "AIConfig":
        return cls(
            openrouter_api_key=os.getenv("OPENROUTER_KEY", ""),
            openrouter_base_url=os.getenv(
                "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"
            ),
            llm_model=os.getenv("KLOC_LLM_MODEL", "minimax/minimax-m2.7"),
            embedding_model=os.getenv("KLOC_EMBEDDING_MODEL", "qwen/qwen3-embedding-8b"),
            embedding_dimension=int(os.getenv("KLOC_EMBEDDING_DIMENSION", "4096")),
            qdrant_url=os.getenv("QDRANT_URL", "http://localhost:6333"),
            project_root=os.getenv("KLOC_PROJECT_ROOT", ""),
            project_name=os.getenv("KLOC_PROJECT_NAME", "default"),
            max_tokens_per_chunk=int(os.getenv("KLOC_MAX_TOKENS_PER_CHUNK", "8000")),
        )

    def validate(self) -> list[str]:
        """Return list of validation errors (empty if valid)."""
        errors = []
        if not self.openrouter_api_key:
            errors.append("OPENROUTER_KEY is required for AI features")
        if not self.project_root:
            errors.append("KLOC_PROJECT_ROOT is required (path to PHP source files)")
        return errors
