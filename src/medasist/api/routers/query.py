from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Body, Request

from medasist.api.deps import limiter
from medasist.api.schemas import QueryRequest, QueryResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@limiter.limit("10/minute")
@router.post(
    "/query",
    response_model=QueryResponse,
    summary="Consulta ao pipeline RAG",
    description=(
        "Recebe uma pergunta e um perfil de usuário, executa o pipeline RAG completo "
        "e retorna a resposta com citações e disclaimer médico obrigatório."
    ),
)
async def query(request: Request, body: Annotated[QueryRequest, Body()]) -> QueryResponse:
    """Executa consulta RAG para o perfil e pergunta informados.

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
    result = chain(body.question)

    logger.info(
        "query: profile='%s' cold_start=%s citations=%d",
        body.profile.value,
        result.is_cold_start,
        len(result.citations),
    )

    return QueryResponse.from_result(result)
