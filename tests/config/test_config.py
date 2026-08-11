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
