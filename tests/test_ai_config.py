"""Tests for AI configuration module."""

import os
from unittest.mock import patch

from src.ai.config import AIConfig


class TestAIConfigDefaults:
    def test_default_values(self):
        config = AIConfig()
        assert config.openrouter_api_key == ""
        assert config.openrouter_base_url == "https://openrouter.ai/api/v1"
        assert config.llm_model == "minimax/minimax-m2.7"
        assert config.embedding_model == "qwen/qwen3-embedding-8b"
        assert config.embedding_dimension == 4096
        assert config.qdrant_url == "http://localhost:6333"
        assert config.project_root == ""
        assert config.project_name == "default"
        assert config.max_tokens_per_chunk == 8000


class TestAIConfigFromEnv:
    def test_from_env_with_all_vars(self):
        env = {
            "OPENROUTER_KEY": "test-key-123",
            "OPENROUTER_BASE_URL": "https://custom.api/v1",
            "KLOC_LLM_MODEL": "custom/model",
            "KLOC_EMBEDDING_MODEL": "custom/embed",
            "KLOC_EMBEDDING_DIMENSION": "768",
            "QDRANT_URL": "http://qdrant:6333",
            "KLOC_PROJECT_ROOT": "/tmp/project",
            "KLOC_PROJECT_NAME": "myapp",
            "KLOC_MAX_TOKENS_PER_CHUNK": "4000",
        }
        with patch.dict(os.environ, env, clear=False):
            config = AIConfig.from_env()
            assert config.openrouter_api_key == "test-key-123"
            assert config.openrouter_base_url == "https://custom.api/v1"
            assert config.llm_model == "custom/model"
            assert config.embedding_model == "custom/embed"
            assert config.embedding_dimension == 768
            assert config.qdrant_url == "http://qdrant:6333"
            assert config.project_root == "/tmp/project"
            assert config.project_name == "myapp"
            assert config.max_tokens_per_chunk == 4000

    def test_from_env_defaults(self):
        env = {}
        with patch.dict(os.environ, env, clear=True):
            config = AIConfig.from_env()
            assert config.llm_model == "minimax/minimax-m2.7"
            assert config.embedding_model == "qwen/qwen3-embedding-8b"


class TestAIConfigValidation:
    def test_validate_missing_key(self):
        config = AIConfig(project_root="/tmp")
        errors = config.validate()
        assert any("OPENROUTER_KEY" in e for e in errors)

    def test_validate_missing_root(self):
        config = AIConfig(openrouter_api_key="key")
        errors = config.validate()
        assert any("PROJECT_ROOT" in e for e in errors)

    def test_validate_all_set(self):
        config = AIConfig(openrouter_api_key="key", project_root="/tmp")
        errors = config.validate()
        assert errors == []
