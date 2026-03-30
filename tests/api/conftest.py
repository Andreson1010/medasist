from __future__ import annotations

from collections.abc import Generator
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from medasist.generation.chain import GenerationResult
from medasist.generation.citations import CitationItem
from medasist.profiles.schemas import UserProfile


def _make_generation_result(
    answer: str = "A dose recomendada é 500mg [1].",
    is_cold_start: bool = False,
) -> GenerationResult:
    citations = (
        [CitationItem(index=1, source="bula_amoxicilina.pdf", section="Posologia", page="3")]
        if not is_cold_start
        else []
    )
    return GenerationResult(
        answer=answer,
        citations=citations,
        profile=UserProfile.MEDICO,
        disclaimer="Este sistema é um auxiliar informativo e não substitui avaliação médica presencial.",
        is_cold_start=is_cold_start,
    )


@pytest.fixture()
def mock_chain() -> MagicMock:
    """Chain mockada que retorna um GenerationResult padrão."""
    chain = MagicMock()
    chain.return_value = _make_generation_result()
    return chain


@pytest.fixture()
def cold_start_chain() -> MagicMock:
    """Chain mockada que retorna resultado de cold start."""
    chain = MagicMock()
    chain.return_value = _make_generation_result(
        answer="Não encontrei informações relevantes para sua pergunta.",
        is_cold_start=True,
    )
    return chain


@pytest.fixture()
def client(mock_chain: MagicMock) -> Generator[TestClient, None, None]:
    """TestClient com lifespan mockado (sem ChromaDB nem LLM real)."""
    chains = {profile: mock_chain for profile in UserProfile}

    with (
        patch("medasist.api.main.get_all_vectorstores") as mock_stores,
        patch("medasist.api.main.build_chain") as mock_build,
    ):
        mock_stores.return_value = {}
        mock_build.side_effect = lambda stores, profile, settings: chains[profile]

        from medasist.api.main import app

        with TestClient(app) as c:
            yield c


_TEST_ADMIN_KEY = "test-admin-key"


@pytest.fixture()
def admin_headers() -> dict[str, str]:
    """Headers com X-Admin-Key para endpoints protegidos.

    Sempre usar em conjunto com patch de ``get_settings`` que retorne
    ``admin_api_key.get_secret_value() == _TEST_ADMIN_KEY``.
    Use a fixture ``ingest_client`` para testes de ingestão, que já inclui
    esse patch automaticamente.
    """
    return {"X-Admin-Key": _TEST_ADMIN_KEY}


@pytest.fixture()
def ingest_client(client: TestClient) -> Generator[tuple[TestClient, dict[str, str]], None, None]:
    """TestClient + headers prontos para testes de ingestão.

    Já inclui patch de ``get_settings`` com ``admin_api_key`` correspondente
    ao header ``X-Admin-Key: test-admin-key``, evitando leitura do .env real.
    """
    mock_settings = MagicMock()
    mock_settings.admin_api_key.get_secret_value.return_value = _TEST_ADMIN_KEY

    with patch("medasist.api.routers.ingest.get_settings", return_value=mock_settings):
        yield client, {"X-Admin-Key": _TEST_ADMIN_KEY}
