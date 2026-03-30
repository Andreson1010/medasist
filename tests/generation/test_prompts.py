from __future__ import annotations

import pytest
from langchain_core.prompts import ChatPromptTemplate

from medasist.generation.prompts import PromptRegistry
from medasist.profiles.schemas import UserProfile


@pytest.fixture()
def registry() -> PromptRegistry:
    return PromptRegistry()


class TestPromptRegistry:
    def test_get_prompt_returns_chat_prompt_template(self, registry: PromptRegistry) -> None:
        prompt = registry.get_prompt(UserProfile.MEDICO)
        assert isinstance(prompt, ChatPromptTemplate)

    @pytest.mark.parametrize("profile", list(UserProfile))
    def test_all_profiles_have_prompt(
        self, registry: PromptRegistry, profile: UserProfile
    ) -> None:
        prompt = registry.get_prompt(profile)
        assert prompt is not None

    @pytest.mark.parametrize("profile", list(UserProfile))
    def test_prompt_has_context_and_question_variables(
        self, registry: PromptRegistry, profile: UserProfile
    ) -> None:
        prompt = registry.get_prompt(profile)
        variables = prompt.input_variables
        assert "context" in variables
        assert "question" in variables

    def test_templates_are_unique_across_profiles(self, registry: PromptRegistry) -> None:
        templates = [
            registry.get_prompt(profile).messages[0].prompt.template  # type: ignore[union-attr]
            for profile in UserProfile
        ]
        assert len(set(templates)) == len(templates), "Perfis têm templates duplicados"

    def test_get_prompt_same_profile_returns_same_instance(
        self, registry: PromptRegistry
    ) -> None:
        p1 = registry.get_prompt(UserProfile.PACIENTE)
        p2 = registry.get_prompt(UserProfile.PACIENTE)
        assert p1 is p2

    def test_prompt_formats_correctly(self, registry: PromptRegistry) -> None:
        prompt = registry.get_prompt(UserProfile.MEDICO)
        messages = prompt.format_messages(context="ctx", question="q?")
        assert len(messages) == 1
        content = messages[0].content
        assert "ctx" in content
        assert "q?" in content
