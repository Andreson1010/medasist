from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from medasist.config import Settings
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
