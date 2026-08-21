from __future__ import annotations

import json
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

from medasist.generation.chain import GenerationResult
from medasist.generation.citations import CitationItem
from medasist.ingestion.schemas import DocType
from medasist.profiles.schemas import UserProfile


class QueryRequest(BaseModel):
    """Requisição de consulta ao pipeline RAG.

    Attributes
    ----------
    question : str
        Pergunta do usuário. Mínimo 1, máximo 500 caracteres.
    profile : UserProfile
        Perfil do usuário para selecionar temperatura, max_tokens e prompt.
    doc_types : list[DocType] | None
        Filtro de tipos de documento. Limita a recuperação às coleções
        selecionadas. Quando ``None`` ou ``[]`` (lista vazia), consulta todas
        as coleções.
    """

    question: str = Field(min_length=1, max_length=500)
    profile: UserProfile
    doc_types: list[DocType] | None = Field(
        default=None,
        description=(
            "Filtro de tipos de documento. Limita a recuperação às coleções "
            "selecionadas. Quando null ou lista vazia, consulta todas as coleções."
        ),
    )


class CitationResponse(BaseModel):
    """Citação de fonte referenciada na resposta.

    Attributes
    ----------
    index : int
        Número do marcador ``[N]`` na resposta.
    source : str
        Nome ou caminho do documento de origem.
    section : str
        Seção do documento.
    page : str
        Página do documento.
    """

    index: int
    source: str
    section: str
    page: str

    @classmethod
    def from_item(cls, item: CitationItem) -> CitationResponse:
        """Constrói CitationResponse a partir de CitationItem.

        Parameters
        ----------
        item : CitationItem
            Item de citação do módulo generation.

        Returns
        -------
        CitationResponse
            DTO de resposta correspondente.
        """
        return cls(
            index=item.index,
            source=item.source,
            section=item.section,
            page=item.page,
        )


def _sse_line(payload: dict) -> str:
    """Serializa um payload como linha ``data: {json}\\n\\n`` de um evento SSE.

    Parameters
    ----------
    payload : dict
        Dicionário tipado do evento (ex: ``{"type": "token", "delta": ...}``).

    Returns
    -------
    str
        Linha ``data: {json}\\n\\n`` com UTF-8 preservado.
    """
    return "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"


def sse_token(delta: str) -> str:
    """Monta o evento SSE ``token`` com o delta parcial gerado.

    Parameters
    ----------
    delta : str
        Trecho de texto produzido pelo LLM.

    Returns
    -------
    str
        Linha SSE ``data: {"type":"token","delta":...}\\n\\n``.
    """
    return _sse_line({"type": "token", "delta": delta})


def sse_citations(items: list[CitationResponse]) -> str:
    """Monta o evento SSE ``citations`` com as citações validadas.

    Parameters
    ----------
    items : list[CitationResponse]
        Citações já serializadas via ``CitationResponse.from_item``.

    Returns
    -------
    str
        Linha SSE ``data: {"type":"citations","citations":[...]}\\n\\n``.
    """
    return _sse_line(
        {"type": "citations", "citations": [item.model_dump() for item in items]}
    )


def sse_disclaimer(text: str) -> str:
    """Monta o evento SSE ``disclaimer`` com o aviso médico obrigatório.

    Parameters
    ----------
    text : str
        Texto do disclaimer.

    Returns
    -------
    str
        Linha SSE ``data: {"type":"disclaimer","text":...}\\n\\n``.
    """
    return _sse_line({"type": "disclaimer", "text": text})


def sse_cold_start(message: str) -> str:
    """Monta o evento SSE ``cold_start`` com a mensagem fixa.

    Parameters
    ----------
    message : str
        Mensagem de cold start.

    Returns
    -------
    str
        Linha SSE ``data: {"type":"cold_start","message":...}\\n\\n``.
    """
    return _sse_line({"type": "cold_start", "message": message})


def sse_error(message: str) -> str:
    """Monta o evento SSE ``error`` (terminal, substitui ``done``).

    Parameters
    ----------
    message : str
        Mensagem de erro exibida ao usuário.

    Returns
    -------
    str
        Linha SSE ``data: {"type":"error","message":...}\\n\\n``.
    """
    return _sse_line({"type": "error", "message": message})


def sse_done() -> str:
    """Monta o evento SSE ``done`` (terminal de sucesso).

    Returns
    -------
    str
        Linha SSE ``data: {"type":"done"}\\n\\n``.
    """
    return _sse_line({"type": "done"})


class QueryResponse(BaseModel):
    """Resposta do pipeline RAG.

    Attributes
    ----------
    answer : str
        Texto gerado pelo LLM ou mensagem de cold start.
    citations : list[CitationResponse]
        Fontes referenciadas na resposta.
    profile : UserProfile
        Perfil utilizado na consulta.
    disclaimer : str
        Aviso médico obrigatório.
    is_cold_start : bool
        ``True`` quando nenhum chunk relevante foi encontrado.
    """

    answer: str
    citations: list[CitationResponse]
    profile: UserProfile
    disclaimer: str
    is_cold_start: bool

    @classmethod
    def from_result(cls, result: GenerationResult) -> QueryResponse:
        """Constrói QueryResponse a partir de GenerationResult.

        Parameters
        ----------
        result : GenerationResult
            Resultado do pipeline RAG.

        Returns
        -------
        QueryResponse
            DTO de resposta da API.
        """
        return cls(
            answer=result.answer,
            citations=[CitationResponse.from_item(c) for c in result.citations],
            profile=result.profile,
            disclaimer=result.disclaimer,
            is_cold_start=result.is_cold_start,
        )


class IngestResponse(BaseModel):
    """Resposta da operação de ingestão de documento.

    Attributes
    ----------
    filename : str
        Nome do arquivo ingerido.
    doc_type : DocType
        Tipo do documento.
    sha256 : str
        Hash SHA-256 do arquivo.
    chunks_indexed : int
        Número de chunks indexados.
    skipped : bool
        ``True`` se o documento já estava indexado (idempotente).
    error : str | None
        Mensagem de erro, se houver.
    """

    filename: str
    doc_type: DocType
    sha256: str
    chunks_indexed: int
    skipped: bool
    error: str | None = None


class DependencyStatus(StrEnum):
    """Estado de saúde de uma dependência.

    Attributes
    ----------
    OK : str
        Dependência operacional.
    DEGRADED : str
        Dependência acessível, mas parcialmente funcional (ex: coleções ausentes).
    UNAVAILABLE : str
        Dependência inacessível.
    """

    OK = "ok"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


class DependencyHealth(BaseModel):
    """Saúde de uma dependência do pipeline (ChromaDB ou LM Studio).

    Attributes
    ----------
    status : DependencyStatus
        Estado da dependência.
    details : str
        Detalhe legível do resultado do probe.
    latency_ms : int
        Latência do probe em milissegundos.
    """

    status: DependencyStatus
    details: str
    latency_ms: int


class HealthResponse(BaseModel):
    """Resposta do endpoint ``GET /health``.

    Attributes
    ----------
    status : Literal["ok", "degraded"]
        Estado geral: ``ok`` se todas as dependências estão ``ok``.
    chromadb : DependencyHealth
        Saúde do ChromaDB.
    lm_studio : DependencyHealth
        Saúde do LM Studio.
    """

    status: Literal["ok", "degraded"]
    chromadb: DependencyHealth
    lm_studio: DependencyHealth
