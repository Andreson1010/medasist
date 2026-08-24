from __future__ import annotations

import io
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from medasist.generation.chain import GenerationResult
from medasist.generation.citations import CitationItem
from medasist.profiles.schemas import UserProfile

"""Acceptance tests for CICD-04 (slowapi rate limiting on /query and /ingest).

Verifies the real 429 behaviour from the outside, through the HTTP contract:
the slowapi limiter counts requests per client IP and rejects the request that
exceeds the configured limit. The Chroma/LLM layer is replaced by mocked
chains/pipeline so the tests exercise only the rate-limit gate. No ``src/``
file is modified.

- POST /query: limit ``10/minute`` → the 11th request answers 429.
- POST /ingest: limit ``5/minute`` → the 6th request answers 429.
"""

logger = logging.getLogger(__name__)

_QUESTION = "Qual a dose recomendada de amoxicilina?"
_ADMIN_KEY = "test-admin-key-0123456789"
_DISCLAIMER = (
    "Este sistema e um auxiliar informativo e nao substitui "
    "avaliacao medica presencial."
)

_QUERY_LIMIT = 10
_INGEST_LIMIT = 5


@dataclass
class _IngestResult:
    sha256: str = "abc123"
    chunks_indexed: int = 3
    skipped: bool = False
    error: str | None = None


def _make_result() -> GenerationResult:
    """Constrói GenerationResult sintético com uma citação válida.

    Returns
    -------
    GenerationResult
        Resultado fixo retornado pela chain mockada em toda chamada.
    """
    return GenerationResult(
        answer="A dose recomendada e 500 mg [1].",
        citations=[
            CitationItem(
                index=1,
                source="bula_amoxicilina.pdf",
                section="Posologia",
                page="3",
            )
        ],
        profile=UserProfile.MEDICO,
        disclaimer=_DISCLAIMER,
        is_cold_start=False,
    )


def _payload() -> dict:
    """Monta payload JSON válido para ``POST /query``.

    Returns
    -------
    dict
        Payload com pergunta e perfil válidos.
    """
    return {"question": _QUESTION, "profile": "medico"}


def _pdf_upload() -> dict:
    """Cria payload de upload simulando um arquivo PDF.

    Returns
    -------
    dict
        Dicionário de arquivo para ``files=`` do TestClient.
    """
    return {"file": ("bula_teste.pdf", io.BytesIO(b"%PDF-1.4 fake"), "application/pdf")}


@contextmanager
def _client() -> Iterator[TestClient]:
    """Constrói TestClient com lifespan mockado e chain mockada.

    Patching espelha ``tests/api/conftest.py`` (nada de ChromaDB nem LLM real).
    ``get_client``/``build_embeddings`` do lifespan também são mockados para
    manter o teste rápido e isolado.

    Yields
    ------
    TestClient
        Cliente de teste com a app já inicializada.
    """
    chain = MagicMock()
    chain.return_value = _make_result()
    chains = dict.fromkeys(UserProfile, chain)
    with (
        patch("medasist.api.main.get_client"),
        patch("medasist.api.main.build_embeddings"),
        patch("medasist.api.main.get_all_vectorstores", return_value={}),
        patch(
            "medasist.api.main.build_chain",
            side_effect=lambda stores, profile, settings: chains[profile],
        ),
    ):
        from medasist.api.main import app

        with TestClient(app) as c:
            yield c


@contextmanager
def _ingest_client() -> Iterator[tuple[TestClient, dict[str, str]]]:
    """Constrói TestClient com pipeline de ingestão mockado.

    A autenticação usa ``get_settings`` real: o ``conftest.py`` raiz define a
    env var ``ADMIN_API_KEY`` antes da coleta, então a chave do header bate com
    a configurada. Apenas o pipeline (``ingest_document``/``get_client``) é
    mockado.

    Yields
    ------
    tuple[TestClient, dict[str, str]]
        Cliente de teste e headers de admin prontos.
    """
    with (
        patch(
            "medasist.api.routers.ingest.ingest_document", return_value=_IngestResult()
        ),
        patch("medasist.api.routers.ingest.get_client"),
        patch("medasist.api.main.get_client"),
        patch("medasist.api.main.build_embeddings"),
        patch("medasist.api.main.get_all_vectorstores", return_value={}),
        patch("medasist.api.main.build_chain", return_value=MagicMock()),
    ):
        from medasist.api.main import app

        with TestClient(app) as c:
            yield c, {"X-Admin-Key": _ADMIN_KEY}


def _assert_rate_limited(response) -> None:
    """Assert response é um 429 do slowapi.

    Parameters
    ----------
    response
        Resposta do TestClient.
    """
    assert response.status_code == 429
    assert "Rate limit exceeded" in response.json()["error"]


def test_query_allows_up_to_limit_then_returns_429() -> None:
    """CICD-04/HP1: /query aceita até 10 requisições por minuto e a 11ª
    responde 429 do slowapi."""
    with _client() as c:
        for _ in range(_QUERY_LIMIT):
            ok = c.post("/query", json=_payload())
            assert ok.status_code == 200

        blocked = c.post("/query", json=_payload())

    _assert_rate_limited(blocked)


def test_query_limit_is_per_minute_window() -> None:
    """CICD-04/HP2: o limite é por janela de 1 minuto — 10 requests passam e o
    11º é o primeiro 429, sem 429 prematuro dentro da janela."""
    with _client() as c:
        statuses = [c.post("/query", json=_payload()).status_code for _ in range(11)]

    assert statuses == [200] * _QUERY_LIMIT + [429]


def test_query_429_message_mentions_rate_limit() -> None:
    """CICD-04/HP3: a resposta 429 de /query carrega a mensagem padrão do
    slowapi indicando o limite excedido."""
    with _client() as c:
        for _ in range(_QUERY_LIMIT + 1):
            resp = c.post("/query", json=_payload())

    _assert_rate_limited(resp)


def test_ingest_allows_up_to_limit_then_returns_429() -> None:
    """CICD-04/HP4: /ingest aceita até 5 requisições por minuto (com admin key
    válida) e a 6ª responde 429 do slowapi."""
    with _ingest_client() as (c, headers):
        for _ in range(_INGEST_LIMIT):
            ok = c.post("/ingest?doc_type=bula", files=_pdf_upload(), headers=headers)
            assert ok.status_code == 200

        blocked = c.post("/ingest?doc_type=bula", files=_pdf_upload(), headers=headers)

    _assert_rate_limited(blocked)


def test_ingest_rate_limit_independent_from_query() -> None:
    """CICD-04/HP5: os limites de /query e /ingest são escopos distintos — o
    consumo de um não afeta o outro (a contagem é por endpoint)."""
    with _client() as c:
        # Esgota o limite de /query (10) e confirma que /ingest segue livre.
        for _ in range(_QUERY_LIMIT):
            assert c.post("/query", json=_payload()).status_code == 200
        assert c.post("/query", json=_payload()).status_code == 429

        with (
            patch(
                "medasist.api.routers.ingest.ingest_document",
                return_value=_IngestResult(),
            ),
            patch("medasist.api.routers.ingest.get_client"),
        ):
            ok = c.post(
                "/ingest?doc_type=bula",
                files=_pdf_upload(),
                headers={"X-Admin-Key": _ADMIN_KEY},
            )
        assert ok.status_code == 200
