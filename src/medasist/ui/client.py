from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from medasist.config import get_settings

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 120.0


# ---------------------------------------------------------------------------
# Exceções
# ---------------------------------------------------------------------------


class APIError(Exception):
    """Erro base para falhas na comunicação com a API MedAssist."""


class RateLimitError(APIError):
    """HTTP 429 — limite de requisições excedido (10 req/min)."""


class ServerError(APIError):
    """HTTP 5xx — erro interno do servidor."""


class RequestTimeoutError(APIError):
    """A API não respondeu dentro do tempo limite configurado."""


# ---------------------------------------------------------------------------
# DTOs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CitationResult:
    """Citação de fonte retornada pela API.

    Attributes
    ----------
    index : int
        Marcador numérico ``[N]`` na resposta.
    source : str
        Nome ou caminho do documento de origem.
    section : str
        Seção do documento referenciada.
    page : str
        Página do documento referenciada.
    """

    index: int
    source: str
    section: str
    page: str


@dataclass(frozen=True)
class QueryResult:
    """Resultado de uma consulta ao pipeline RAG.

    Attributes
    ----------
    answer : str
        Resposta gerada pelo LLM ou mensagem de cold start.
    citations : list[CitationResult]
        Fontes citadas na resposta.
    profile : str
        Perfil de usuário utilizado na consulta.
    disclaimer : str
        Aviso médico obrigatório.
    is_cold_start : bool
        True quando nenhum chunk relevante foi encontrado.
    """

    answer: str
    citations: list[CitationResult]
    profile: str
    disclaimer: str
    is_cold_start: bool


# ---------------------------------------------------------------------------
# Funções públicas
# ---------------------------------------------------------------------------


def check_health(base_url: str | None = None) -> bool:
    """Verifica se a API está disponível via GET /health.

    Nunca levanta exceção — qualquer falha retorna ``False``.

    Parameters
    ----------
    base_url : str | None
        URL base da API. Usa ``settings.api_base_url`` por padrão.

    Returns
    -------
    bool
        ``True`` quando a API responde ``{"status": "ok"}``.
    """
    url = (base_url or get_settings().api_base_url).rstrip("/")
    try:
        with httpx.Client(timeout=5.0) as client:
            response = client.get(f"{url}/health")
            if response.status_code == 200:
                data = response.json()
                return data.get("status") == "ok"
            return False
    except Exception as exc:
        logger.debug("Falha no health check: %s", exc)
        return False


def query(
    question: str,
    profile: str,
    doc_types: list[str] | None = None,
    base_url: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> QueryResult:
    """Envia uma pergunta ao pipeline RAG via POST /query.

    Parameters
    ----------
    question : str
        Pergunta do usuário (máx. 500 caracteres).
    profile : str
        Valor do enum ``UserProfile`` (ex: ``"medico"``).
    doc_types : list[str] | None
        Filtro opcional por tipo de documento.
    base_url : str | None
        URL base da API. Usa ``settings.api_base_url`` por padrão.
    timeout : float
        Tempo limite da requisição em segundos.

    Returns
    -------
    QueryResult
        Resposta tipada do pipeline RAG.

    Raises
    ------
    RateLimitError
        Quando a API retorna HTTP 429.
    ServerError
        Quando a API retorna HTTP 5xx.
    RequestTimeoutError
        Quando a requisição excede ``timeout`` segundos.
    APIError
        Para qualquer outro status não-2xx.
    """
    url = (base_url or get_settings().api_base_url).rstrip("/")
    payload: dict = {
        "question": question,
        "profile": profile,
        "doc_types": doc_types,
    }

    logger.debug("POST %s/query profile=%s", url, profile)

    try:
        with httpx.Client(timeout=timeout) as client:
            response = client.post(f"{url}/query", json=payload)
    except httpx.TimeoutException as exc:
        raise RequestTimeoutError("A API não respondeu a tempo.") from exc

    if response.status_code == 429:
        raise RateLimitError("Limite de requisições atingido. Aguarde um momento.")
    if response.status_code >= 500:
        logger.warning("Erro do servidor: HTTP %d", response.status_code)
        raise ServerError(f"Erro interno do servidor (HTTP {response.status_code}).")
    if not response.is_success:
        logger.warning("Resposta inesperada: HTTP %d", response.status_code)
        raise APIError(f"Erro na requisição (HTTP {response.status_code}).")

    data = response.json()
    citations = [
        CitationResult(
            index=c["index"],
            source=c["source"],
            section=c["section"],
            page=c["page"],
        )
        for c in data.get("citations", [])
    ]

    return QueryResult(
        answer=data["answer"],
        citations=citations,
        profile=data["profile"],
        disclaimer=data["disclaimer"],
        is_cold_start=data["is_cold_start"],
    )
