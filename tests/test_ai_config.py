"""Tests for AI configuration module (per-operation provider config)."""

import os
from unittest.mock import patch

from src.ai.config import AIConfig, EmbeddingProviderConfig, LLMProviderConfig


class TestProviderDefaults:
    def test_llm_provider_defaults(self):
        cfg = LLMProviderConfig()
        assert cfg.api_url == "https://openrouter.ai/api/v1"
        assert cfg.api_key == ""
        assert cfg.model == "minimax/minimax-m2.7"

    def test_embedding_provider_defaults(self):
        cfg = EmbeddingProviderConfig()
        assert cfg.api_url == "https://openrouter.ai/api/v1"
        assert cfg.api_key == ""
        assert cfg.model == "qwen/qwen3-embedding-8b"
        assert cfg.dimension == 4096


class TestAIConfigDefaults:
    def test_default_values(self):
        config = AIConfig()
        assert config.llm.api_url == "https://openrouter.ai/api/v1"
        assert config.llm.api_key == ""
        assert config.llm.model == "minimax/minimax-m2.7"
        assert config.embedding.api_url == "https://openrouter.ai/api/v1"
        assert config.embedding.api_key == ""
        assert config.embedding.model == "qwen/qwen3-embedding-8b"
        assert config.embedding.dimension == 4096
        assert config.qdrant_url == "http://localhost:6333"
        assert config.project_root == ""
        assert config.project_name == "default"
        assert config.max_tokens_per_chunk == 8000


class TestAIConfigFromEnv:
    def test_from_env_with_all_vars(self):
        env = {
            "LLM_API_URL": "https://generativelanguage.googleapis.com/v1beta/openai/",
            "LLM_API_KEY": "gemini-key-123",
            "LLM_MODEL": "gemini-3-flash-preview",
            "EMBEDDING_API_URL": "https://embeddings.example.com/v1",
            "EMBEDDING_API_KEY": "embed-key-456",
            "EMBEDDING_MODEL": "gemini-embedding-001",
            "EMBEDDING_DIMENSION": "3072",
            "QDRANT_URL": "http://qdrant:6333",
            "KLOC_PROJECT_ROOT": "/tmp/project",
            "KLOC_PROJECT_NAME": "myapp",
            "KLOC_MAX_TOKENS_PER_CHUNK": "4000",
        }
        with patch.dict(os.environ, env, clear=False):
            config = AIConfig.from_env()
            assert config.llm.api_url == "https://generativelanguage.googleapis.com/v1beta/openai/"
            assert config.llm.api_key == "gemini-key-123"
            assert config.llm.model == "gemini-3-flash-preview"
            assert config.embedding.api_url == "https://embeddings.example.com/v1"
            assert config.embedding.api_key == "embed-key-456"
            assert config.embedding.model == "gemini-embedding-001"
            assert config.embedding.dimension == 3072
            assert config.qdrant_url == "http://qdrant:6333"
            assert config.project_root == "/tmp/project"
            assert config.project_name == "myapp"
            assert config.max_tokens_per_chunk == 4000

    def test_from_env_defaults(self):
        env = {}
        with patch.dict(os.environ, env, clear=True):
            config = AIConfig.from_env()
            assert config.llm.model == "minimax/minimax-m2.7"
            assert config.embedding.model == "qwen/qwen3-embedding-8b"
            assert config.embedding.dimension == 4096

    def test_independent_providers(self):
        """Different URL/key per operation must be supported."""
        env = {
            "LLM_API_URL": "https://provider-a/v1",
            "LLM_API_KEY": "key-a",
            "EMBEDDING_API_URL": "https://provider-b/v1",
            "EMBEDDING_API_KEY": "key-b",
        }
        with patch.dict(os.environ, env, clear=True):
            config = AIConfig.from_env()
        assert config.llm.api_url != config.embedding.api_url
        assert config.llm.api_key != config.embedding.api_key


class TestAIConfigValidation:
    def test_validate_missing_llm_key_when_required(self):
        config = AIConfig(
            embedding=EmbeddingProviderConfig(api_key="set"),
            project_root="/tmp",
        )
        errors = config.validate()
        assert any("LLM_API_KEY" in e for e in errors)
        assert not any("EMBEDDING_API_KEY" in e for e in errors)

    def test_validate_missing_embedding_key_when_required(self):
        config = AIConfig(
            llm=LLMProviderConfig(api_key="set"),
            project_root="/tmp",
        )
        errors = config.validate()
        assert any("EMBEDDING_API_KEY" in e for e in errors)
        assert not any("LLM_API_KEY" in e for e in errors)

    def test_validate_missing_root(self):
        config = AIConfig(
            llm=LLMProviderConfig(api_key="set"),
            embedding=EmbeddingProviderConfig(api_key="set"),
        )
        errors = config.validate()
        assert any("PROJECT_ROOT" in e for e in errors)

    def test_validate_search_only_skips_llm(self):
        """search needs only embedding; missing LLM key must not error when narrowed."""
        config = AIConfig(
            embedding=EmbeddingProviderConfig(api_key="set"),
            project_root="/tmp",
        )
        errors = config.validate(require_llm=False)
        assert errors == []

    def test_validate_explain_only_skips_embedding(self):
        config = AIConfig(
            llm=LLMProviderConfig(api_key="set"),
            project_root="/tmp",
        )
        errors = config.validate(require_embedding=False)
        assert errors == []

    def test_validate_all_set(self):
        config = AIConfig(
            llm=LLMProviderConfig(api_key="k"),
            embedding=EmbeddingProviderConfig(api_key="k"),
            project_root="/tmp",
        )
        errors = config.validate()
        assert errors == []
