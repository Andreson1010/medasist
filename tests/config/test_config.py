from __future__ import annotations

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
