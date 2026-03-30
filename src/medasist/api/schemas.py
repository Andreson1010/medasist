from __future__ import annotations

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
        Filtro de tipos de documento. Reservado para uso futuro — aceito mas
        não aplicado na versão atual.
    """

    question: str = Field(min_length=1, max_length=500)
    profile: UserProfile
    doc_types: list[DocType] | None = Field(
        default=None,
        description="Reservado — filtragem por tipo de documento será suportada em versão futura.",
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
