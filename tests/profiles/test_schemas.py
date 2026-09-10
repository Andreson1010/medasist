from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest
from pydantic import ValidationError

from medasist.config import Settings
from medasist.profiles import schemas as profiles_schemas
from medasist.profiles.schemas import (
    PROMPT_TEMPLATES,
    ProfileConfig,
    UserProfile,
    get_profile_config,
)


@pytest.fixture()
def default_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


class TestUserProfile:
    def test_user_profile_values(self):
        members = {p.value for p in UserProfile}
        assert members == {"medico", "enfermeiro", "assistente", "paciente"}

    def test_user_profile_is_str(self):
        assert isinstance(UserProfile.MEDICO, str)


class TestPromptTemplates:
    def test_all_profiles_have_prompt_template(self):
        for profile in UserProfile:
            assert (
                profile in PROMPT_TEMPLATES
            ), f"Perfil '{profile.value}' sem template em PROMPT_TEMPLATES"


class TestProfileConfigFrozen:
    def test_profile_config_is_frozen(self):
        config = ProfileConfig(
            temperature=0.1,
            max_tokens=1024,
            prompt_template="Contexto: {context}\nPergunta: {question}",
        )
        with pytest.raises(FrozenInstanceError):
            config.temperature = 0.9  # type: ignore[misc]


class TestGetProfileConfig:
    def test_get_profile_config_medico(self, default_settings: Settings):
        config = get_profile_config(UserProfile.MEDICO, settings=default_settings)
        assert config.temperature == 0.1
        assert config.max_tokens == 1024

    def test_get_profile_config_enfermeiro(self, default_settings: Settings):
        config = get_profile_config(UserProfile.ENFERMEIRO, settings=default_settings)
        assert config.temperature == 0.15
        assert config.max_tokens == 1024

    def test_get_profile_config_assistente(self, default_settings: Settings):
        config = get_profile_config(UserProfile.ASSISTENTE, settings=default_settings)
        assert config.temperature == 0.2
        assert config.max_tokens == 512

    def test_get_profile_config_paciente(self, default_settings: Settings):
        config = get_profile_config(UserProfile.PACIENTE, settings=default_settings)
        assert config.temperature == 0.3
        assert config.max_tokens == 512

    def test_profile_config_has_prompt_template(self, default_settings: Settings):
        for profile in UserProfile:
            config = get_profile_config(profile, settings=default_settings)
            assert isinstance(config.prompt_template, str)
            assert len(config.prompt_template) > 0

    def test_prompt_template_contains_context_placeholder(
        self, default_settings: Settings
    ):
        for profile in UserProfile:
            config = get_profile_config(profile, settings=default_settings)
            assert "{context}" in config.prompt_template

    def test_prompt_template_contains_question_placeholder(
        self, default_settings: Settings
    ):
        for profile in UserProfile:
            config = get_profile_config(profile, settings=default_settings)
            assert "{question}" in config.prompt_template

    def test_get_profile_config_reads_from_settings(self):
        custom = Settings(medico_temperature=0.99)  # type: ignore[call-arg]
        config = get_profile_config(UserProfile.MEDICO, settings=custom)
        assert config.temperature == 0.99

    def test_get_profile_config_returns_new_instance(self, default_settings: Settings):
        config_a = get_profile_config(UserProfile.MEDICO, settings=default_settings)
        config_b = get_profile_config(UserProfile.MEDICO, settings=default_settings)
        assert config_a is not config_b

    def test_all_profiles_have_config(self, default_settings: Settings):
        for profile in UserProfile:
            config = get_profile_config(profile, settings=default_settings)
            assert isinstance(config, ProfileConfig)


class TestProfileLlmAccessors:
    def test_all_profiles_have_accessors(self):
        for profile in UserProfile:
            assert (
                profile in profiles_schemas._PROFILE_LLM_ACCESSORS
            ), f"Perfil '{profile.value}' sem acessadores tipados de Settings"

    def test_accessors_read_settings_fields(self, default_settings: Settings):
        read_temp, read_tokens = profiles_schemas._PROFILE_LLM_ACCESSORS[
            UserProfile.PACIENTE
        ]
        assert read_temp(default_settings) == default_settings.paciente_temperature
        assert read_tokens(default_settings) == default_settings.paciente_max_tokens

    def test_unmapped_profile_raises_value_error(
        self, default_settings: Settings, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr(profiles_schemas, "_PROFILE_LLM_ACCESSORS", {})
        with pytest.raises(ValueError, match="sem configuração de LLM"):
            get_profile_config(UserProfile.MEDICO, settings=default_settings)

    def test_missing_template_raises_value_error(
        self, default_settings: Settings, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr(profiles_schemas, "PROMPT_TEMPLATES", {})
        with pytest.raises(ValueError, match="sem template"):
            get_profile_config(UserProfile.MEDICO, settings=default_settings)


class TestMaxUploadMb:
    def test_default_is_25(self):
        settings = Settings()  # type: ignore[call-arg]
        assert settings.max_upload_mb == 25

    def test_accepts_positive_value(self):
        settings = Settings(max_upload_mb=10)  # type: ignore[call-arg]
        assert settings.max_upload_mb == 10

    def test_rejects_non_positive(self):
        with pytest.raises(ValidationError):
            Settings(max_upload_mb=0)  # type: ignore[call-arg]
