from __future__ import annotations

import json
import logging
from collections.abc import Iterator
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


class NotFoundError(APIError):
    """HTTP 404 — recurso não encontrado (ex: streaming desabilitado)."""


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


@dataclass(frozen=True)
class StreamEvent:
    """Evento tipado de um stream SSE do ``POST /query/stream``.

    Attributes
    ----------
    type : str
        Tipo do evento: ``token``, ``citations``, ``disclaimer``,
        ``cold_start``, ``error`` ou ``done``.
    delta : str | None
        Texto parcial do LLM (presente em eventos ``token``).
    citations : list[CitationResult] | None
        Citações validadas (presente em eventos ``citations``).
    message : str | None
        Mensagem (presente em eventos ``cold_start`` e ``error``).
    text : str | None
        Texto avulso (presente em eventos ``disclaimer``).
    """

    type: str
    delta: str | None = None
    citations: list[CitationResult] | None = None
    message: str | None = None
    text: str | None = None


# ---------------------------------------------------------------------------
# Funções públicas
# ---------------------------------------------------------------------------


def check_health(base_url: str | None = None) -> bool:
    """Verifica se a API MedAssist responde HTTP 200 em GET /health.

    Semântica de "API no ar": qualquer resposta HTTP 200 conta como ``True``,
    mesmo com ``status`` top-level ``"degraded"`` — degradação de dependência é
    reportada separadamente pela UI via ``get_health``. Nunca levanta exceção.

    Parameters
    ----------
    base_url : str | None
        URL base da API. Usa ``settings.api_base_url`` por padrão.

    Returns
    -------
    bool
        ``True`` quando a API responde HTTP 200 em /health; ``False`` em
        status não-200, timeout ou erro de conexão.
    """
    return get_health(base_url) is not None


def get_health(base_url: str | None = None) -> dict | None:
    """Obtém o corpo de ``GET /health`` da API, ou ``None`` se indisponível.

    Parameters
    ----------
    base_url : str | None
        URL base da API. Usa ``settings.api_base_url`` por padrão.

    Returns
    -------
    dict | None
        Corpo JSON de ``/health`` quando a resposta é HTTP 200; ``None`` em
        timeout, erro de conexão, status não-200 ou corpo inválido. Nunca
        levanta exceção.
    """
    url = (base_url or get_settings().api_base_url).rstrip("/")
    try:
        with httpx.Client(timeout=5.0) as client:
            response = client.get(f"{url}/health")
            if response.status_code != 200:
                return None
            return response.json()
    except Exception as exc:
        logger.debug("Falha no health check: %s", exc)
        return None


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


def _parse_sse_line(line: str) -> StreamEvent | None:
    """Converte uma linha ``data: {json}`` em um ``StreamEvent``.

    Linhas sem o prefixo ``data:`` (ex: vazias, comentários) são ignoradas.

    Parameters
    ----------
    line : str
        Linha do corpo da resposta SSE.

    Returns
    -------
    StreamEvent | None
        Evento tipado correspondente, ou ``None`` se a linha não é ``data:``.
    """
    stripped = line.strip()
    if not stripped.startswith("data:"):
        return None
    payload = json.loads(stripped[len("data:") :].strip())

    citations = None
    if payload.get("citations") is not None:
        citations = [
            CitationResult(
                index=c["index"],
                source=c["source"],
                section=c["section"],
                page=c["page"],
            )
            for c in payload["citations"]
        ]

    return StreamEvent(
        type=payload.get("type", ""),
        delta=payload.get("delta"),
        citations=citations,
        message=payload.get("message"),
        text=payload.get("text"),
    )


def query_stream(
    question: str,
    profile: str,
    doc_types: list[str] | None = None,
    base_url: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> Iterator[StreamEvent]:
    """Consome o stream SSE do ``POST /query/stream``, evento a evento.

    Lê as linhas ``data: {json}`` da resposta e as converte em ``StreamEvent``
    tipados, tratando 429/5xx como as demais chamadas do client. Usada quando
    ``generation_streaming_enabled=True``; quando o backend responde 404 (flag
    divergente), levanta ``NotFoundError`` para a UI degradar para ``/query``.

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

    Yields
    ------
    StreamEvent
        Cada evento tipado do stream (token, citations, disclaimer, etc.).

    Raises
    ------
    RateLimitError
        Quando a API retorna HTTP 429.
    ServerError
        Quando a API retorna HTTP 5xx.
    NotFoundError
        Quando a API retorna HTTP 404 (streaming desabilitado).
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

    logger.debug("POST %s/query/stream profile=%s", url, profile)

    try:
        with (
            httpx.Client(timeout=timeout) as client,
            client.stream("POST", f"{url}/query/stream", json=payload) as response,
        ):
            if response.status_code == 429:
                raise RateLimitError(
                    "Limite de requisições atingido. Aguarde um momento."
                )
            if response.status_code == 404:
                raise NotFoundError("Streaming de resposta desabilitado no servidor.")
            if response.status_code >= 500:
                logger.warning("Erro do servidor: HTTP %d", response.status_code)
                raise ServerError(
                    f"Erro interno do servidor (HTTP {response.status_code})."
                )
            if not response.is_success:
                logger.warning("Resposta inesperada: HTTP %d", response.status_code)
                raise APIError(f"Erro na requisição (HTTP {response.status_code}).")

            for line in response.iter_lines():
                event = _parse_sse_line(line)
                if event is not None:
                    yield event
    except httpx.TimeoutException as exc:
        raise RequestTimeoutError("A API não respondeu a tempo.") from exc
