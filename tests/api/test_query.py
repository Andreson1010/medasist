from __future__ import annotations

import inspect
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from medasist.api.routers.query import query as query_handler
from medasist.ingestion.schemas import DocType
from medasist.profiles.schemas import UserProfile

VALID_PAYLOAD = {"question": "qual a dose de amoxicilina?", "profile": "medico"}


class TestQueryIsSync:
    def test_query_endpoint_is_sync(self) -> None:
        """O endpoint /query deve ser síncrono (def), não async.

        Endpoint async chamando a chain síncrona bloqueia o event loop durante
        a geração do LLM. FastAPI roda rotas ``def`` numa threadpool.
        """
        assert not hasattr(query_handler, "__await__")
        assert not inspect.iscoroutinefunction(query_handler)


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
        chains = dict.fromkeys(UserProfile, cold_start_chain)

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

    def test_disclaimer_present_on_cold_start(
        self, cold_start_chain: MagicMock
    ) -> None:
        chains = dict.fromkeys(UserProfile, cold_start_chain)

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
        response = client.post(
            "/query", json={"question": "x" * 501, "profile": "medico"}
        )
        assert response.status_code == 422

    def test_invalid_profile_returns_422(self, client: TestClient) -> None:
        response = client.post(
            "/query", json={"question": "qual a dose?", "profile": "invalido"}
        )
        assert response.status_code == 422

    def test_missing_profile_returns_422(self, client: TestClient) -> None:
        response = client.post("/query", json={"question": "qual a dose?"})
        assert response.status_code == 422

    def test_all_profiles_accepted(self, client: TestClient) -> None:
        for profile in ("medico", "enfermeiro", "assistente", "paciente"):
            response = client.post(
                "/query", json={"question": "qual a dose?", "profile": profile}
            )
            assert (
                response.status_code == 200
            ), f"perfil '{profile}' retornou {response.status_code}"


class TestQueryDocTypes:
    def _chain_recording(
        self,
        mock_chain: MagicMock,
        calls: list,
    ) -> MagicMock:
        "MagicMock que registra os argumentos e devolve um GenerationResult."
        mock_chain.side_effect = lambda *args, **kwargs: (
            calls.append((args, kwargs)) or mock_chain.return_value
        )
        return mock_chain

    def test_doc_types_passed_into_chain(self, mock_chain: MagicMock) -> None:
        calls: list = []
        self._chain_recording(mock_chain, calls)
        chains = dict.fromkeys(UserProfile, mock_chain)

        with (
            patch("medasist.api.main.get_all_vectorstores", return_value={}),
            patch(
                "medasist.api.main.build_chain",
                side_effect=lambda stores, profile, settings: chains[profile],
            ),
        ):
            from medasist.api.main import app

            with TestClient(app) as c:
                response = c.post(
                    "/query",
                    json={
                        "question": "qual a dose?",
                        "profile": "medico",
                        "doc_types": ["bula", "protocolo"],
                    },
                )

        assert response.status_code == 200
        assert len(calls) == 1
        args, kwargs = calls[0]
        assert "qual a dose?" in args
        assert DocType.BULA in args[1]
        assert DocType.PROTOCOLO in args[1]

    def test_full_doc_types_set_accepted(self, client: TestClient) -> None:
        response = client.post(
            "/query",
            json={
                "question": "qual a dose?",
                "profile": "medico",
                "doc_types": ["bula", "diretriz", "protocolo", "manual"],
            },
        )
        assert response.status_code == 200

    def test_omitted_doc_types_returns_200(self, client: TestClient) -> None:
        response = client.post("/query", json=VALID_PAYLOAD)
        assert response.status_code == 200

    def test_empty_doc_types_returns_200(self, client: TestClient) -> None:
        response = client.post(
            "/query",
            json={"question": "qual a dose?", "profile": "medico", "doc_types": []},
        )
        assert response.status_code == 200

    def test_lowercase_valid_value_accepted(self, client: TestClient) -> None:
        response = client.post(
            "/query",
            json={
                "question": "qual a dose?",
                "profile": "medico",
                "doc_types": ["bula"],
            },
        )
        assert response.status_code == 200

    def test_invalid_non_doctype_value_returns_422(self, client: TestClient) -> None:
        response = client.post(
            "/query",
            json={
                "question": "qual a dose?",
                "profile": "medico",
                "doc_types": ["PACIENTE"],
            },
        )
        assert response.status_code == 422
