from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import SecretStr, ValidationError

from medasist.config import (
    ADMIN_KEY_MIN_LENGTH,
    Settings,
    admin_key_is_weak,
)


class TestAdminKeyIsWeak:
    def test_placeholder_dev_only_is_weak(self) -> None:
        assert admin_key_is_weak("dev-only") is True

    def test_placeholder_troque_por_chave_segura_is_weak(self) -> None:
        assert admin_key_is_weak("troque-por-chave-segura") is True

    def test_short_key_is_weak(self) -> None:
        assert admin_key_is_weak("x" * 15) is True

    def test_whitespace_stripped_short_is_weak(self) -> None:
        assert admin_key_is_weak(f"{'x' * 15}   ") is True

    def test_exactly_min_length_is_strong(self) -> None:
        assert admin_key_is_weak("x" * ADMIN_KEY_MIN_LENGTH) is False

    def test_long_strong_key_is_not_weak(self) -> None:
        assert admin_key_is_weak("a" * 64) is False


class TestSettingsAdminKeyValidation:
    def test_strong_key_accepted(self) -> None:
        settings = Settings(admin_api_key=SecretStr("very-strong-key-0123456789"))
        assert settings.admin_api_key.get_secret_value() == "very-strong-key-0123456789"

    def test_placeholder_dev_only_raises(self) -> None:
        with pytest.raises(ValidationError):
            Settings(admin_api_key=SecretStr("dev-only"))

    def test_placeholder_troque_por_chave_segura_raises(self) -> None:
        with pytest.raises(ValidationError):
            Settings(admin_api_key=SecretStr("troque-por-chave-segura"))

    def test_fifteen_char_key_raises(self) -> None:
        with pytest.raises(ValidationError):
            Settings(admin_api_key=SecretStr("x" * 15))

    def test_exactly_sixteen_char_key_accepted(self) -> None:
        settings = Settings(admin_api_key=SecretStr("x" * 16))
        assert settings.admin_api_key.get_secret_value() == "x" * 16

    def test_long_strong_key_accepted(self) -> None:
        settings = Settings(admin_api_key=SecretStr("a" * 64))
        assert settings.admin_api_key.get_secret_value() == "a" * 64


class TestSettingsLogLevelValidation:
    def test_default_log_level_is_info(self) -> None:
        settings = Settings(admin_api_key=SecretStr("very-strong-key-0123456789"))
        assert settings.log_level == "INFO"

    def test_log_level_normalized_to_upper(self) -> None:
        settings = Settings(
            admin_api_key=SecretStr("very-strong-key-0123456789"),
            log_level="debug",
        )
        assert settings.log_level == "DEBUG"

    def test_invalid_log_level_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError):
            Settings(
                admin_api_key=SecretStr("very-strong-key-0123456789"),
                log_level="verbose",
            )


class TestSettingsHealthcheckTimeout:
    def test_default_healthcheck_timeout_is_3(self) -> None:
        settings = Settings(admin_api_key=SecretStr("very-strong-key-0123456789"))
        assert settings.healthcheck_timeout == 3.0

    def test_custom_healthcheck_timeout_accepted(self) -> None:
        settings = Settings(
            admin_api_key=SecretStr("very-strong-key-0123456789"),
            healthcheck_timeout=1.5,
        )
        assert settings.healthcheck_timeout == 1.5

    def test_zero_healthcheck_timeout_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError):
            Settings(
                admin_api_key=SecretStr("very-strong-key-0123456789"),
                healthcheck_timeout=0,
            )


class TestSettingsEvaluation:
    def test_defaults_are_set(self) -> None:
        settings = Settings(admin_api_key=SecretStr("very-strong-key-0123456789"))
        assert settings.eval_golden_set_path == Path("evals/dataset/golden_set.json")
        assert settings.eval_llm_model == settings.lm_studio_llm_model
        assert settings.eval_embedding_model == settings.lm_studio_embedding_model
        assert settings.eval_batch_size == 16

    def test_empty_eval_llm_model_resolves_to_lm_studio_model(self) -> None:
        settings = Settings(
            admin_api_key=SecretStr("very-strong-key-0123456789"),
            lm_studio_llm_model="phi-3-mini",
            eval_llm_model="",
        )
        assert settings.eval_llm_model == "phi-3-mini"

    def test_empty_eval_embedding_model_resolves_to_lm_studio_model(self) -> None:
        settings = Settings(
            admin_api_key=SecretStr("very-strong-key-0123456789"),
            lm_studio_embedding_model="nomic-embed-text",
            eval_embedding_model="",
        )
        assert settings.eval_embedding_model == "nomic-embed-text"

    def test_non_empty_eval_models_are_respected(self) -> None:
        settings = Settings(
            admin_api_key=SecretStr("very-strong-key-0123456789"),
            eval_llm_model="judge-mini",
            eval_embedding_model="judge-embed",
        )
        assert settings.eval_llm_model == "judge-mini"
        assert settings.eval_embedding_model == "judge-embed"

    def test_custom_eval_golden_set_path_accepted(self) -> None:
        settings = Settings(
            admin_api_key=SecretStr("very-strong-key-0123456789"),
            eval_golden_set_path=Path("evals/custom/golden.json"),
        )
        assert settings.eval_golden_set_path == Path("evals/custom/golden.json")

    def test_custom_eval_batch_size_accepted(self) -> None:
        settings = Settings(
            admin_api_key=SecretStr("very-strong-key-0123456789"),
            eval_batch_size=4,
        )
        assert settings.eval_batch_size == 4

    def test_zero_eval_batch_size_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError):
            Settings(
                admin_api_key=SecretStr("very-strong-key-0123456789"),
                eval_batch_size=0,
            )

    def test_negative_eval_batch_size_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError):
            Settings(
                admin_api_key=SecretStr("very-strong-key-0123456789"),
                eval_batch_size=-1,
            )


class TestSettingsRerank:
    def test_defaults_are_set(self) -> None:
        settings = Settings(admin_api_key=SecretStr("very-strong-key-0123456789"))
        assert settings.retrieval_rerank_enabled is False
        assert settings.retrieval_rerank_model == "BAAI/bge-reranker-base"
        assert settings.retrieval_rerank_top_n == 20
        assert settings.retrieval_rerank_batch_size == 16

    def test_custom_values_accepted(self) -> None:
        settings = Settings(
            admin_api_key=SecretStr("very-strong-key-0123456789"),
            retrieval_rerank_enabled=True,
            retrieval_rerank_model="custom-reranker",
            retrieval_rerank_top_n=5,
            retrieval_rerank_batch_size=4,
        )
        assert settings.retrieval_rerank_enabled is True
        assert settings.retrieval_rerank_model == "custom-reranker"
        assert settings.retrieval_rerank_top_n == 5
        assert settings.retrieval_rerank_batch_size == 4

    def test_zero_top_n_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError):
            Settings(
                admin_api_key=SecretStr("very-strong-key-0123456789"),
                retrieval_rerank_top_n=0,
            )

    def test_negative_top_n_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError):
            Settings(
                admin_api_key=SecretStr("very-strong-key-0123456789"),
                retrieval_rerank_top_n=-1,
            )

    def test_zero_batch_size_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError):
            Settings(
                admin_api_key=SecretStr("very-strong-key-0123456789"),
                retrieval_rerank_batch_size=0,
            )

    def test_negative_batch_size_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError):
            Settings(
                admin_api_key=SecretStr("very-strong-key-0123456789"),
                retrieval_rerank_batch_size=-1,
            )

    def test_env_override(self, monkeypatch) -> None:
        monkeypatch.setenv("RETRIEVAL_RERANK_ENABLED", "true")
        monkeypatch.setenv("RETRIEVAL_RERANK_MODEL", "env-reranker")
        monkeypatch.setenv("RETRIEVAL_RERANK_TOP_N", "7")
        monkeypatch.setenv("RETRIEVAL_RERANK_BATCH_SIZE", "3")
        settings = Settings(admin_api_key=SecretStr("very-strong-key-0123456789"))
        assert settings.retrieval_rerank_enabled is True
        assert settings.retrieval_rerank_model == "env-reranker"
        assert settings.retrieval_rerank_top_n == 7
        assert settings.retrieval_rerank_batch_size == 3


class TestSettingsHybridSearch:
    def test_defaults_are_set(self) -> None:
        settings = Settings(admin_api_key=SecretStr("very-strong-key-0123456789"))
        assert settings.retrieval_hybrid_enabled is False
        assert settings.retrieval_hybrid_rrf_k == 60
        assert settings.retrieval_hybrid_sparse_top_k == 20
        assert isinstance(settings.retrieval_sparse_stopwords, tuple)
        assert "mg" not in settings.retrieval_sparse_stopwords
        assert "ml" not in settings.retrieval_sparse_stopwords
        assert "g" not in settings.retrieval_sparse_stopwords
        assert "kg" not in settings.retrieval_sparse_stopwords

    def test_sparse_top_k_default_ge_retrieval_top_k(self) -> None:
        settings = Settings(admin_api_key=SecretStr("very-strong-key-0123456789"))
        assert settings.retrieval_hybrid_sparse_top_k >= settings.retrieval_top_k

    def test_custom_values_accepted(self) -> None:
        settings = Settings(
            admin_api_key=SecretStr("very-strong-key-0123456789"),
            retrieval_hybrid_enabled=True,
            retrieval_hybrid_rrf_k=30,
            retrieval_hybrid_sparse_top_k=5,
            retrieval_sparse_stopwords=("de", "a", "para"),
        )
        assert settings.retrieval_hybrid_enabled is True
        assert settings.retrieval_hybrid_rrf_k == 30
        assert settings.retrieval_hybrid_sparse_top_k == 5
        assert settings.retrieval_sparse_stopwords == ("de", "a", "para")

    def test_zero_rrf_k_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError):
            Settings(
                admin_api_key=SecretStr("very-strong-key-0123456789"),
                retrieval_hybrid_rrf_k=0,
            )

    def test_negative_rrf_k_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError):
            Settings(
                admin_api_key=SecretStr("very-strong-key-0123456789"),
                retrieval_hybrid_rrf_k=-1,
            )

    def test_zero_sparse_top_k_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError):
            Settings(
                admin_api_key=SecretStr("very-strong-key-0123456789"),
                retrieval_hybrid_sparse_top_k=0,
            )

    def test_negative_sparse_top_k_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError):
            Settings(
                admin_api_key=SecretStr("very-strong-key-0123456789"),
                retrieval_hybrid_sparse_top_k=-1,
            )

    def test_env_override(self, monkeypatch) -> None:
        monkeypatch.setenv("RETRIEVAL_HYBRID_ENABLED", "true")
        monkeypatch.setenv("RETRIEVAL_HYBRID_RRF_K", "30")
        monkeypatch.setenv("RETRIEVAL_HYBRID_SPARSE_TOP_K", "5")
        monkeypatch.setenv("RETRIEVAL_SPARSE_STOPWORDS", '["de", "a", "para"]')
        settings = Settings(admin_api_key=SecretStr("very-strong-key-0123456789"))
        assert settings.retrieval_hybrid_enabled is True
        assert settings.retrieval_hybrid_rrf_k == 30
        assert settings.retrieval_hybrid_sparse_top_k == 5
        assert settings.retrieval_sparse_stopwords == ("de", "a", "para")


class TestSettingsQueryRewrite:
    def test_defaults_are_set(self) -> None:
        settings = Settings(admin_api_key=SecretStr("very-strong-key-0123456789"))
        assert settings.retrieval_query_rewrite_enabled is False
        assert settings.retrieval_query_rewrite_min_length == 3
        assert settings.retrieval_query_rewrite_model == settings.lm_studio_llm_model
        assert settings.retrieval_query_rewrite_temperature == 0.0
        assert settings.retrieval_query_rewrite_max_tokens == 128
        assert settings.retrieval_query_rewrite_max_output == 200

    def test_empty_model_resolves_to_lm_studio_model(self) -> None:
        settings = Settings(
            admin_api_key=SecretStr("very-strong-key-0123456789"),
            lm_studio_llm_model="phi-3-mini",
            retrieval_query_rewrite_model="",
        )
        assert settings.retrieval_query_rewrite_model == "phi-3-mini"

    def test_custom_values_accepted(self) -> None:
        settings = Settings(
            admin_api_key=SecretStr("very-strong-key-0123456789"),
            retrieval_query_rewrite_enabled=True,
            retrieval_query_rewrite_min_length=5,
            retrieval_query_rewrite_model="rewrite-mini",
            retrieval_query_rewrite_temperature=0.5,
            retrieval_query_rewrite_max_tokens=64,
            retrieval_query_rewrite_max_output=150,
        )
        assert settings.retrieval_query_rewrite_enabled is True
        assert settings.retrieval_query_rewrite_min_length == 5
        assert settings.retrieval_query_rewrite_model == "rewrite-mini"
        assert settings.retrieval_query_rewrite_temperature == 0.5
        assert settings.retrieval_query_rewrite_max_tokens == 64
        assert settings.retrieval_query_rewrite_max_output == 150

    def test_zero_min_length_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError):
            Settings(
                admin_api_key=SecretStr("very-strong-key-0123456789"),
                retrieval_query_rewrite_min_length=0,
            )

    def test_negative_min_length_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError):
            Settings(
                admin_api_key=SecretStr("very-strong-key-0123456789"),
                retrieval_query_rewrite_min_length=-1,
            )

    def test_temperature_below_zero_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError):
            Settings(
                admin_api_key=SecretStr("very-strong-key-0123456789"),
                retrieval_query_rewrite_temperature=-0.1,
            )

    def test_temperature_above_two_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError):
            Settings(
                admin_api_key=SecretStr("very-strong-key-0123456789"),
                retrieval_query_rewrite_temperature=2.1,
            )

    def test_zero_max_tokens_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError):
            Settings(
                admin_api_key=SecretStr("very-strong-key-0123456789"),
                retrieval_query_rewrite_max_tokens=0,
            )

    def test_negative_max_tokens_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError):
            Settings(
                admin_api_key=SecretStr("very-strong-key-0123456789"),
                retrieval_query_rewrite_max_tokens=-1,
            )

    def test_zero_max_output_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError):
            Settings(
                admin_api_key=SecretStr("very-strong-key-0123456789"),
                retrieval_query_rewrite_max_output=0,
            )

    def test_negative_max_output_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError):
            Settings(
                admin_api_key=SecretStr("very-strong-key-0123456789"),
                retrieval_query_rewrite_max_output=-1,
            )

    def test_env_override(self, monkeypatch) -> None:
        monkeypatch.setenv("RETRIEVAL_QUERY_REWRITE_ENABLED", "true")
        monkeypatch.setenv("RETRIEVAL_QUERY_REWRITE_MIN_LENGTH", "5")
        monkeypatch.setenv("RETRIEVAL_QUERY_REWRITE_MODEL", "env-rewrite")
        monkeypatch.setenv("RETRIEVAL_QUERY_REWRITE_TEMPERATURE", "0.4")
        monkeypatch.setenv("RETRIEVAL_QUERY_REWRITE_MAX_TOKENS", "64")
        monkeypatch.setenv("RETRIEVAL_QUERY_REWRITE_MAX_OUTPUT", "120")
        settings = Settings(admin_api_key=SecretStr("very-strong-key-0123456789"))
        assert settings.retrieval_query_rewrite_enabled is True
        assert settings.retrieval_query_rewrite_min_length == 5
        assert settings.retrieval_query_rewrite_model == "env-rewrite"
        assert settings.retrieval_query_rewrite_temperature == 0.4
        assert settings.retrieval_query_rewrite_max_tokens == 64
        assert settings.retrieval_query_rewrite_max_output == 120


class TestSettingsGenerationStreaming:
    def test_default_is_false(self) -> None:
        settings = Settings(admin_api_key=SecretStr("very-strong-key-0123456789"))
        assert settings.generation_streaming_enabled is False

    def test_env_override_true(self, monkeypatch) -> None:
        monkeypatch.setenv("GENERATION_STREAMING_ENABLED", "true")
        settings = Settings(admin_api_key=SecretStr("very-strong-key-0123456789"))
        assert settings.generation_streaming_enabled is True

    def test_env_override_false(self, monkeypatch) -> None:
        monkeypatch.setenv("GENERATION_STREAMING_ENABLED", "false")
        settings = Settings(admin_api_key=SecretStr("very-strong-key-0123456789"))
        assert settings.generation_streaming_enabled is False


class TestSettingsRetryBackoff:
    def test_defaults_are_set(self) -> None:
        settings = Settings(admin_api_key=SecretStr("very-strong-key-0123456789"))
        assert settings.llm_max_retries == 2
        assert settings.llm_request_timeout == 60.0
        assert settings.embedding_max_retries == 2
        assert settings.embedding_request_timeout == 30.0

    def test_custom_values_accepted(self) -> None:
        settings = Settings(
            admin_api_key=SecretStr("very-strong-key-0123456789"),
            llm_max_retries=5,
            llm_request_timeout=120.0,
            embedding_max_retries=3,
            embedding_request_timeout=15.0,
        )
        assert settings.llm_max_retries == 5
        assert settings.llm_request_timeout == 120.0
        assert settings.embedding_max_retries == 3
        assert settings.embedding_request_timeout == 15.0

    def test_zero_retries_accepted(self) -> None:
        settings = Settings(
            admin_api_key=SecretStr("very-strong-key-0123456789"),
            llm_max_retries=0,
            embedding_max_retries=0,
        )
        assert settings.llm_max_retries == 0
        assert settings.embedding_max_retries == 0

    def test_negative_llm_retries_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError):
            Settings(
                admin_api_key=SecretStr("very-strong-key-0123456789"),
                llm_max_retries=-1,
            )

    def test_negative_embedding_retries_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError):
            Settings(
                admin_api_key=SecretStr("very-strong-key-0123456789"),
                embedding_max_retries=-1,
            )

    def test_zero_llm_timeout_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError):
            Settings(
                admin_api_key=SecretStr("very-strong-key-0123456789"),
                llm_request_timeout=0,
            )

    def test_zero_embedding_timeout_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError):
            Settings(
                admin_api_key=SecretStr("very-strong-key-0123456789"),
                embedding_request_timeout=0,
            )
