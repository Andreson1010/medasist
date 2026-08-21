from __future__ import annotations

from collections.abc import Callable, Generator
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from medasist.api.schemas import DependencyHealth, DependencyStatus
from medasist.generation.chain import GenerationResult
from medasist.generation.citations import CitationItem
from medasist.ingestion.schemas import DocType
from medasist.profiles.schemas import UserProfile


def _healthy_dependency() -> DependencyHealth:
    """DependencyHealth padrão para probes mockados no /health."""
    return DependencyHealth(
        status=DependencyStatus.OK,
        details="saudável",
        latency_ms=1,
    )


def _make_generation_result(
    answer: str = "A dose recomendada é 500mg [1].",
    is_cold_start: bool = False,
) -> GenerationResult:
    citations = (
        [
            CitationItem(
                index=1, source="bula_amoxicilina.pdf", section="Posologia", page="3"
            )
        ]
        if not is_cold_start
        else []
    )
    return GenerationResult(
        answer=answer,
        citations=citations,
        profile=UserProfile.MEDICO,
        disclaimer=(
            "Este sistema é um auxiliar informativo e não substitui "
            "avaliação médica presencial."
        ),
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
    chains = dict.fromkeys(UserProfile, mock_chain)

    with (
        patch("medasist.api.main.get_all_vectorstores") as mock_stores,
        patch("medasist.api.main.build_chain") as mock_build,
        patch(
            "medasist.api.health.check_chromadb",
            return_value=_healthy_dependency(),
        ),
        patch(
            "medasist.api.health.check_lm_studio",
            return_value=_healthy_dependency(),
        ),
    ):
        mock_stores.return_value = {}
        mock_build.side_effect = lambda stores, profile, settings: chains[profile]

        from medasist.api.main import app

        with TestClient(app) as c:
            yield c


def _make_stream_chain(
    deltas: tuple[str, ...] = ("Olá", " ", "mundo [1]."),
    citations: list[CitationItem] | None = None,
    is_cold_start: bool = False,
) -> MagicMock:
    """Chain streamada mockada que yield os deltas e retorna o estado terminal.

    Retorna um ``MagicMock`` cuja chamada ``chain(question, doc_types)`` produz
    um gerador que yield ``deltas`` e retorna ``(citations, is_cold_start)``
    (estado terminal do gerador).
    """
    chain = MagicMock()
    valid_citations = (
        citations
        if citations is not None
        else [
            CitationItem(
                index=1, source="bula_amoxicilina.pdf", section="Posologia", page="3"
            )
        ]
    )

    def _call(question: str, doc_types: list[DocType] | None = None) -> Generator:
        def gen() -> Generator:
            yield from deltas
            return valid_citations, is_cold_start

        return gen()

    chain.side_effect = _call
    return chain


@pytest.fixture()
def streaming_chain() -> MagicMock:
    """Chain streamada que yield deltas e termina com citações válidas."""
    return _make_stream_chain()


@pytest.fixture()
def cold_start_streaming_chain() -> MagicMock:
    """Chain streamada que decide cold start (sem tokens)."""
    return _make_stream_chain(deltas=(), is_cold_start=True)


@pytest.fixture()
def no_citation_streaming_chain() -> MagicMock:
    """Chain streamada cuja resposta não tem citações válidas."""
    return _make_stream_chain(
        deltas=("Resposta sem marcador de citação.",),
        citations=[],
        is_cold_start=True,
    )


def _streaming_settings() -> MagicMock:
    """Settings mockados com a flag de streaming ativa e textos de segurança."""
    settings = MagicMock()
    settings.generation_streaming_enabled = True
    settings.cold_start_message = "Não encontrei essa informação."
    settings.disclaimer = "Este sistema é um auxiliar informativo."
    return settings


@pytest.fixture()
def streaming_client(
    streaming_chain: MagicMock,
) -> Generator[TestClient, None, None]:
    """TestClient com flag de streaming ativa e chain streamada mockada."""
    streaming_chains = dict.fromkeys(UserProfile, streaming_chain)

    with (
        patch("medasist.api.main.get_all_vectorstores", return_value={}),
        patch("medasist.api.main.build_chain", return_value=MagicMock()),
        patch(
            "medasist.api.main.build_stream_chain",
            side_effect=lambda stores, profile, settings: streaming_chains[profile],
        ),
        patch(
            "medasist.api.routers.query.get_settings",
            return_value=_streaming_settings(),
        ),
        patch(
            "medasist.api.health.check_chromadb",
            return_value=_healthy_dependency(),
        ),
        patch(
            "medasist.api.health.check_lm_studio",
            return_value=_healthy_dependency(),
        ),
    ):
        from medasist.api.main import app

        with TestClient(app) as c:
            yield c


@pytest.fixture()
def streaming_client_factory() -> Callable[..., Generator[TestClient, None, None]]:
    """Fábrica de TestClient de streaming com chains customizadas.

    Uso: ``with streaming_client_factory(chains) as c:`` onde ``chains`` é um
    mapeamento ``UserProfile → chain streamada mockada``.
    """

    @contextmanager
    def _factory(chains: dict) -> Generator[TestClient, None, None]:
        with (
            patch("medasist.api.main.get_all_vectorstores", return_value={}),
            patch("medasist.api.main.build_chain", return_value=MagicMock()),
            patch(
                "medasist.api.main.build_stream_chain",
                side_effect=lambda stores, profile, settings: chains[profile],
            ),
            patch(
                "medasist.api.routers.query.get_settings",
                return_value=_streaming_settings(),
            ),
            patch(
                "medasist.api.health.check_chromadb",
                return_value=_healthy_dependency(),
            ),
            patch(
                "medasist.api.health.check_lm_studio",
                return_value=_healthy_dependency(),
            ),
        ):
            from medasist.api.main import app

            with TestClient(app) as c:
                yield c

    return _factory


_TEST_ADMIN_KEY = "test-admin-key-0123456789"


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
def ingest_client(
    client: TestClient,
) -> Generator[tuple[TestClient, dict[str, str]], None, None]:
    """TestClient + headers prontos para testes de ingestão.

    Já inclui patch de ``get_settings`` com ``admin_api_key`` correspondente
    ao header ``X-Admin-Key: test-admin-key``, evitando leitura do .env real.
    """
    mock_settings = MagicMock()
    mock_settings.admin_api_key.get_secret_value.return_value = _TEST_ADMIN_KEY
    mock_settings.max_upload_mb = 25

    with patch("medasist.api.routers.ingest.get_settings", return_value=mock_settings):
        yield client, {"X-Admin-Key": _TEST_ADMIN_KEY}
