from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from medasist.generation.chain import GenerationResult
from medasist.generation.citations import CitationItem
from medasist.profiles.schemas import UserProfile

VALID_PAYLOAD = {"question": "qual a dose de amoxicilina?", "profile": "medico"}


class TestQueryHappyPath:
    def test_returns_200(self, client: TestClient) -> None:
        response = client.post("/query", json=VALID_PAYLOAD)
        assert response.status_code == 200

    def test_answer_present(self, client: TestClient) -> None:
        response = client.post("/query", json=VALID_PAYLOAD)
        assert response.json()["answer"]

    def test_disclaimer_always_present(self, client: TestClient) -> None:
        response = client.post("/query", json=VALID_PAYLOAD)
        assert response.json()["disclaimer"]

    def test_profile_echoed_in_response(self, client: TestClient) -> None:
        response = client.post("/query", json=VALID_PAYLOAD)
        assert response.json()["profile"] == "medico"

    def test_citations_list_returned(self, client: TestClient) -> None:
        response = client.post("/query", json=VALID_PAYLOAD)
        citations = response.json()["citations"]
        assert isinstance(citations, list)
        assert len(citations) == 1
        assert citations[0]["source"] == "bula_amoxicilina.pdf"

    def test_is_cold_start_false_on_normal_response(self, client: TestClient) -> None:
        response = client.post("/query", json=VALID_PAYLOAD)
        assert response.json()["is_cold_start"] is False


class TestQueryColdStart:
    def test_cold_start_flag_propagated(self, cold_start_chain: MagicMock) -> None:
        chains = {profile: cold_start_chain for profile in UserProfile}

        with (
            patch("medasist.api.main.get_all_vectorstores", return_value={}),
            patch(
                "medasist.api.main.build_chain",
                side_effect=lambda stores, profile, settings: chains[profile],
            ),
        ):
            from medasist.api.main import app

            with TestClient(app) as c:
                response = c.post("/query", json=VALID_PAYLOAD)

        assert response.status_code == 200
        assert response.json()["is_cold_start"] is True
        assert response.json()["citations"] == []

    def test_disclaimer_present_on_cold_start(self, cold_start_chain: MagicMock) -> None:
        chains = {profile: cold_start_chain for profile in UserProfile}

        with (
            patch("medasist.api.main.get_all_vectorstores", return_value={}),
            patch(
                "medasist.api.main.build_chain",
                side_effect=lambda stores, profile, settings: chains[profile],
            ),
        ):
            from medasist.api.main import app

            with TestClient(app) as c:
                response = c.post("/query", json=VALID_PAYLOAD)

        assert response.json()["disclaimer"]


class TestQueryValidation:
    def test_empty_question_returns_422(self, client: TestClient) -> None:
        response = client.post("/query", json={"question": "", "profile": "medico"})
        assert response.status_code == 422

    def test_question_too_long_returns_422(self, client: TestClient) -> None:
        response = client.post("/query", json={"question": "x" * 501, "profile": "medico"})
        assert response.status_code == 422

    def test_invalid_profile_returns_422(self, client: TestClient) -> None:
        response = client.post("/query", json={"question": "qual a dose?", "profile": "invalido"})
        assert response.status_code == 422

    def test_missing_profile_returns_422(self, client: TestClient) -> None:
        response = client.post("/query", json={"question": "qual a dose?"})
        assert response.status_code == 422

    def test_all_profiles_accepted(self, client: TestClient) -> None:
        for profile in ("medico", "enfermeiro", "assistente", "paciente"):
            response = client.post("/query", json={"question": "qual a dose?", "profile": profile})
            assert response.status_code == 200, f"perfil '{profile}' retornou {response.status_code}"
