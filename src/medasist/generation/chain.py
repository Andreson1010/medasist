from __future__ import annotations

import logging
from collections.abc import Callable, Generator
from dataclasses import dataclass, field
from typing import Any

from langchain_core.documents import Document
from langchain_core.output_parsers import StrOutputParser
from langchain_openai import ChatOpenAI

from medasist.config import Settings, get_settings
from medasist.generation.citations import (
    CitationItem,
    build_citations,
    validate_citations,
)
from medasist.generation.prompts import PromptRegistry
from medasist.ingestion.schemas import DocType
from medasist.profiles.schemas import UserProfile, get_profile_config
from medasist.retrieval.retriever import build_retriever, select_collections

logger = logging.getLogger(__name__)

_registry = PromptRegistry()


@dataclass(frozen=True)
class GenerationResult:
    """Resultado de uma consulta ao pipeline RAG.

    Attributes
    ----------
    answer : str
        Texto gerado pelo LLM ou mensagem de cold start.
    citations : list[CitationItem]
        Fontes referenciadas na resposta (vazia em cold start).
    profile : UserProfile
        Perfil do usuário que originou a consulta.
    disclaimer : str
        Aviso médico obrigatório.
    is_cold_start : bool
        ``True`` quando nenhum chunk relevante foi encontrado e o LLM
        não foi chamado.
    """

    answer: str
    citations: list[CitationItem] = field(default_factory=list)
    profile: UserProfile = UserProfile.MEDICO
    disclaimer: str = ""
    is_cold_start: bool = False


def _format_context(docs: list[Document]) -> str:
    """Formata lista de documentos em string numerada ``[N] conteúdo``.

    Parameters
    ----------
    docs : list[Document]
        Documentos recuperados pelo retriever.

    Returns
    -------
    str
        String com um chunk por linha, prefixado por ``[N]``.
    """
    return "\n".join(f"[{i}] {doc.page_content}" for i, doc in enumerate(docs, start=1))


def run_query(
    question: str,
    stores: dict[Any, Any],
    profile: UserProfile,
    settings: Settings | None = None,
    doc_types: list[DocType] | None = None,
) -> GenerationResult:
    """Executa o pipeline RAG completo para uma pergunta.

    Fluxo:
    1. Recupera documentos relevantes via ``build_retriever``.
    2. Cold start guard: se não houver documentos, retorna mensagem fixa
       sem chamar o LLM.
    3. Monta contexto numerado e extrai citações.
    4. Chama o LLM via LCEL ``prompt | ChatOpenAI | StrOutputParser``.
    5. Valida e filtra citações órfãs.

    Parameters
    ----------
    question : str
        Pergunta do usuário.
    stores : dict
        Mapeamento ``DocType → Chroma`` (de ``get_all_vectorstores``).
    profile : UserProfile
        Perfil do usuário para selecionar temperatura, max_tokens e prompt.
    settings : Settings | None
        Configurações. Se ``None``, usa o singleton ``get_settings()``.
    doc_types : list[DocType] | None
        Filtro opcional de tipos de documento. Quando fornecido (lista não
        vazia), a retrieção é limitada às coleções correspondentes — um novo
        subconjunto é construído sem nunca mutar ``stores``. Se ``None`` ou
        lista vazia, consulta todas as coleções.

    Returns
    -------
    GenerationResult
        Resultado com resposta, citações, perfil, disclaimer e flag de cold start.
    """
    if settings is None:
        settings = get_settings()

    subset = select_collections(stores, doc_types)
    retriever = build_retriever(subset, settings)
    docs: list[Document] = retriever.invoke(question)

    # --- Cold start guard (regra de segurança médica inegociável) ---
    if not docs:
        logger.info(
            "run_query: cold start — nenhum chunk relevante para '%s'.", question[:60]
        )
        return GenerationResult(
            answer=settings.cold_start_message,
            citations=[],
            profile=profile,
            disclaimer=settings.disclaimer,
            is_cold_start=True,
        )

    # --- Caminho normal ---
    citations = build_citations(docs)
    context = _format_context(docs)

    config = get_profile_config(profile, settings)
    prompt = _registry.get_prompt(profile)

    llm = ChatOpenAI(
        base_url=settings.lm_studio_base_url,
        api_key=settings.lm_studio_api_key.get_secret_value(),
        model=settings.lm_studio_llm_model,
        temperature=config.temperature,
        max_tokens=config.max_tokens,
        max_retries=settings.llm_max_retries,
        request_timeout=settings.llm_request_timeout,
    )

    chain = prompt | llm | StrOutputParser()
    raw_answer: str = chain.invoke({"context": context, "question": question})

    answer, valid_citations = validate_citations(raw_answer, citations)

    # --- Regra de segurança médica: resposta sem citações é inaceitável ---
    if not valid_citations:
        logger.warning(
            "run_query: LLM produziu resposta sem citações válidas para '%s'. "
            "Retornando cold start.",
            question[:60],
        )
        return GenerationResult(
            answer=settings.cold_start_message,
            citations=[],
            profile=profile,
            disclaimer=settings.disclaimer,
            is_cold_start=True,
        )

    logger.info(
        "run_query: resposta gerada para profile='%s', citações=%d.",
        profile.value,
        len(valid_citations),
    )

    return GenerationResult(
        answer=answer,
        citations=valid_citations,
        profile=profile,
        disclaimer=settings.disclaimer,
        is_cold_start=False,
    )


def build_chain(
    stores: dict[Any, Any],
    profile: UserProfile,
    settings: Settings | None = None,
) -> Callable[[str, list[DocType] | None], GenerationResult]:
    """Retorna uma função ``run(question, doc_types=None) -> GenerationResult``.

    Conveniente para uso no FastAPI lifespan, onde as stores e o perfil
    são fixados no startup e a função resultante é chamada por request.

    Parameters
    ----------
    stores : dict
        Mapeamento ``DocType → Chroma``.
    profile : UserProfile
        Perfil fixo para esta chain.
    settings : Settings | None
        Configurações. Se ``None``, usa o singleton ``get_settings()``.

    Returns
    -------
    Callable[[str, list[DocType] | None], GenerationResult]
        Função que recebe uma pergunta e filtros opcionais e retorna
        ``GenerationResult``.
    """
    if settings is None:
        settings = get_settings()

    def run(question: str, doc_types: list[DocType] | None = None) -> GenerationResult:
        return run_query(question, stores, profile, settings, doc_types)

    return run


def stream_answer(
    question: str,
    stores: dict[Any, Any],
    profile: UserProfile,
    settings: Settings | None = None,
    doc_types: list[DocType] | None = None,
) -> Generator[str, None, tuple[list[CitationItem], bool]]:
    """Gera a resposta do LLM incrementalmente, preservando as regras de segurança.

    Espelha ``run_query`` (retrieval, cold start, citações, perfil, doc_types),
    trocando ``chain.invoke`` por ``chain.stream``. Cada chunk do LLM é
    yieldado enquanto o texto completo é acumulado; ao final, ``validate_citations``
    roda sobre a resposta completa. O estado terminal é comunicado pelo valor de
    retorno do gerador: ``(citations, is_cold_start)``.

    Protocolo-agnóstico: nada sabe de SSE.

    Parameters
    ----------
    question : str
        Pergunta do usuário.
    stores : dict
        Mapeamento ``DocType → Chroma`` (de ``get_all_vectorstores``).
    profile : UserProfile
        Perfil do usuário para selecionar temperatura, max_tokens e prompt.
    settings : Settings | None
        Configurações. Se ``None``, usa o singleton ``get_settings()``.
    doc_types : list[DocType] | None
        Filtro opcional de tipos de documento (via ``select_collections``).

    Yields
    ------
    str
        Cada chunk de texto gerado pelo LLM.

    Returns
    -------
    tuple[list[CitationItem], bool]
        ``(citations_válidas, is_cold_start)``. Em cold start (retrieval vazio
        ou resposta sem citações válidas) retorna ``([], True)`` sem chamar o
        LLM quando o retrieval é vazio.

    Raises
    ------
    Exception
        Propaga qualquer exceção do ``chain.stream`` (ex: LM Studio indisponível).
    """
    if settings is None:
        settings = get_settings()

    subset = select_collections(stores, doc_types)
    retriever = build_retriever(subset, settings)
    docs: list[Document] = retriever.invoke(question)

    # --- Cold start guard (regra de segurança médica inegociável) ---
    if not docs:
        logger.info(
            "stream_answer: cold start — nenhum chunk relevante para '%s'.",
            question[:60],
        )
        return [], True

    # --- Caminho normal ---
    citations = build_citations(docs)
    context = _format_context(docs)

    config = get_profile_config(profile, settings)
    prompt = _registry.get_prompt(profile)

    llm = ChatOpenAI(
        base_url=settings.lm_studio_base_url,
        api_key=settings.lm_studio_api_key.get_secret_value(),
        model=settings.lm_studio_llm_model,
        temperature=config.temperature,
        max_tokens=config.max_tokens,
        max_retries=settings.llm_max_retries,
        request_timeout=settings.llm_request_timeout,
    )

    chain = prompt | llm | StrOutputParser()

    full = ""
    for chunk in chain.stream({"context": context, "question": question}):
        full += chunk
        yield chunk

    answer, valid_citations = validate_citations(full, citations)

    # --- Regra de segurança médica: resposta sem citações é inaceitável ---
    if not valid_citations:
        logger.warning(
            "stream_answer: LLM produziu resposta sem citações válidas para '%s'. "
            "Retornando cold start.",
            question[:60],
        )
        return [], True

    logger.info(
        "stream_answer: resposta gerada para profile='%s', citações=%d.",
        profile.value,
        len(valid_citations),
    )
    return valid_citations, False


def build_stream_chain(
    stores: dict[Any, Any],
    profile: UserProfile,
    settings: Settings | None = None,
) -> Callable[
    [str, list[DocType] | None],
    Generator[str, None, tuple[list[CitationItem], bool]],
]:
    """Retorna uma closure ``stream(question, doc_types=None)`` para o lifespan.

    Espelho de ``build_chain`` para o caminho de streaming: fixa ``stores`` e
    ``profile`` no startup e a função resultante é chamada por request.

    Parameters
    ----------
    stores : dict
        Mapeamento ``DocType → Chroma``.
    profile : UserProfile
        Perfil fixo para esta chain.
    settings : Settings | None
        Configurações. Se ``None``, usa o singleton ``get_settings()``.

    Returns
    -------
    Callable[[str, list[DocType] | None], Generator[
        str, None, tuple[list[CitationItem], bool]
    ]]
        Função que recebe uma pergunta e filtros opcionais e retorna o gerador
        de deltas de ``stream_answer``.
    """
    if settings is None:
        settings = get_settings()

    def stream(
        question: str,
        doc_types: list[DocType] | None = None,
    ) -> Generator[str, None, tuple[list[CitationItem], bool]]:
        return stream_answer(question, stores, profile, settings, doc_types)

    return stream
