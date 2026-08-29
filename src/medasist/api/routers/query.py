from __future__ import annotations

import functools
import logging
import time
from collections.abc import Callable, Generator
from typing import Annotated

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import StreamingResponse

from medasist.api.deps import limiter
from medasist.api.schemas import (
    CitationResponse,
    QueryRequest,
    QueryResponse,
    sse_citations,
    sse_cold_start,
    sse_disclaimer,
    sse_done,
    sse_error,
    sse_token,
)
from medasist.config import get_settings
from medasist.generation.citations import CitationItem
from medasist.ingestion.schemas import DocType
from medasist.monitoring.metrics import record_query

logger = logging.getLogger(__name__)

router = APIRouter()

# Limite de requisições por minuto para endpoints não autenticados.
RATE_LIMIT = "10/minute"


def _rate_limited(func: Callable) -> Callable:
    """Aplica o rate limit do slowapi preservando a resolução de anotações.

    O wrapper do slowapi usa ``*args, **kwargs`` e o seu ``__globals__`` aponta
    para o módulo do slowapi. Sob ``from __future__ import annotations`` as
    anotações são strings (``ForwardRef``) e o FastAPI as resolve contra o
    ``__globals__`` do callable registrado na rota — como ``QueryRequest`` e
    ``Body`` não existem no namespace do slowapi, a anotação ``Annotated[
    QueryRequest, Body()]`` ficaria sem resolver e ``body`` viraria query param
    (422). Esta camada extra (definida neste módulo) garante que o callable
    registrado tenha o ``__globals__`` do módulo query, preservando o parsing
    do corpo e mantendo a checagem de rate limit no dispatch (CRIT-01).

    Parameters
    ----------
    func : Callable
        Handler ``(request, body)`` a ser limitado.

    Returns
    -------
    Callable
        Handler envolvido pelo slowapi com o rate limit ativo.
    """
    limited = limiter.limit(RATE_LIMIT)(func)

    @functools.wraps(func)
    def _wrapped(*args, **kwargs):  # type: ignore[no-untyped-def]
        return limited(*args, **kwargs)

    return _wrapped


@router.post(
    "/query",
    response_model=QueryResponse,
    summary="Consulta ao pipeline RAG",
    description=(
        "Recebe uma pergunta e um perfil de usuário, executa o pipeline RAG completo "
        "e retorna a resposta com citações e disclaimer médico obrigatório."
    ),
)
@_rate_limited
def query(request: Request, body: Annotated[QueryRequest, Body()]) -> QueryResponse:
    """Executa consulta RAG para o perfil e pergunta informados.

    Endpoint síncrono de propósito: fastapi executa rotas ``def`` em uma
    threadpool, evitando bloquear o event loop durante a chamada HTTP ao LLM.

    Parameters
    ----------
    request : Request
        Objeto de request do FastAPI (exigido pelo slowapi).
    body : QueryRequest
        Pergunta, perfil e filtros opcionais de tipo de documento.

    Returns
    -------
    QueryResponse
        Resposta com answer, citations, disclaimer e flag de cold start.
    """
    chain = request.app.state.chains[body.profile]
    start = time.perf_counter()
    result = chain(body.question, body.doc_types)
    latency_ms = int((time.perf_counter() - start) * 1000)

    doc_types = [dt.value for dt in body.doc_types] if body.doc_types else None

    logger.info(
        "query: profile='%s' cold_start=%s citations=%d latency_ms=%d doc_types=%s",
        body.profile.value,
        result.is_cold_start,
        len(result.citations),
        latency_ms,
        doc_types,
    )
    record_query(
        body.profile.value,
        result.is_cold_start,
        len(result.citations),
        latency_ms,
    )

    return QueryResponse.from_result(result)


def _stream_events(
    body: QueryRequest,
    stream: Callable[
        [str, list[DocType] | None],
        Generator[str, None, tuple[list[CitationItem], bool]],
    ],
) -> Generator[str, None, None]:
    """Wrapper SSE: converte os deltas de ``stream_answer`` em eventos tipados.

    Emite ``token`` por delta; ao ler o estado terminal do gerador, emite
    ``citations`` + ``disclaimer`` + ``done`` (sucesso) ou ``cold_start`` +
    ``disclaimer`` + ``done`` (cold start). Em falha do LLM a meio, emite o
    evento terminal ``error`` (sem ``done``) e registra via ``logger.exception``.

    Desconexão do cliente: quando o gerador é fechado (``GeneratorExit``
    disparado pelo Starlette ao detectar a desconexão e cancelar o
    ``StreamingResponse``), o ``finally`` fecha o gerador interno de
    ``stream_answer`` via ``gen.close()``, propagando o ``GeneratorExit`` para
    encerrar o stream do LLM. Nenhum evento terminal é emitido.

    Parameters
    ----------
    body : QueryRequest
        Pergunta, perfil e filtros opcionais.
    stream : Callable
        Closure de streaming de ``build_stream_chain``.

    Yields
    ------
    str
        Linhas SSE ``data: {...}\\n\\n``.
    """
    start = time.perf_counter()
    citations: list[CitationItem] = []
    is_cold_start = False
    gen = stream(body.question, body.doc_types)
    try:
        try:
            while True:
                try:
                    delta = next(gen)
                except StopIteration as stop:
                    citations, is_cold_start = stop.value
                    break
                yield sse_token(delta)
        except Exception:
            logger.exception("query/stream: erro durante a geração da resposta.")
            yield sse_error("Erro ao gerar a resposta.")
            return

        settings = get_settings()
        if is_cold_start:
            yield sse_cold_start(settings.cold_start_message)
        else:
            yield sse_citations([CitationResponse.from_item(c) for c in citations])
        yield sse_disclaimer(settings.disclaimer)
        yield sse_done()
    finally:
        gen.close()
        latency_ms = int((time.perf_counter() - start) * 1000)
        doc_types = [dt.value for dt in body.doc_types] if body.doc_types else None
        logger.info(
            "query/stream: profile='%s' cold_start=%s citations=%d latency_ms=%d "
            "doc_types=%s",
            body.profile.value,
            is_cold_start,
            len(citations),
            latency_ms,
            doc_types,
        )
        record_query(
            body.profile.value,
            is_cold_start,
            len(citations),
            latency_ms,
        )


@router.post(
    "/query/stream",
    summary="Consulta ao pipeline RAG via SSE",
    description=(
        "Entrega a resposta do LLM progressivamente via Server-Sent Events. "
        "Desabilitado por padrão: responde 404 quando "
        "generation_streaming_enabled=False."
    ),
)
@_rate_limited
def query_stream(
    request: Request,
    body: Annotated[QueryRequest, Body()],
) -> StreamingResponse:
    """Exposição do caminho de streaming via SSE.

    Endpoint síncrono de propósito (``def``): FastAPI roda rotas ``def`` numa
    threadpool e itera o gerador síncrono em um thread de trabalho, liberando o
    event loop durante a chamada HTTP ao LLM.

    Parameters
    ----------
    request : Request
        Objeto de request do FastAPI (exigido pelo slowapi).
    body : QueryRequest
        Pergunta, perfil e filtros opcionais (mesma validação do ``/query``).

    Returns
    -------
    StreamingResponse
        Resposta ``text/event-stream`` com os eventos SSE tipados.

    Raises
    ------
    HTTPException
        ``404`` quando ``generation_streaming_enabled`` está desabilitado.
    """
    settings = get_settings()
    if not settings.generation_streaming_enabled:
        raise HTTPException(
            status_code=404,
            detail="Streaming de resposta desabilitado.",
        )
    stream = request.app.state.streaming_chains[body.profile]
    return StreamingResponse(
        _stream_events(body, stream),
        media_type="text/event-stream",
    )
