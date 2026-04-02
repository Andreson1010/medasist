from __future__ import annotations

import logging
from collections.abc import Callable
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
from medasist.profiles.schemas import UserProfile, get_profile_config
from medasist.retrieval.retriever import build_retriever

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

    Returns
    -------
    GenerationResult
        Resultado com resposta, citações, perfil, disclaimer e flag de cold start.
    """
    if settings is None:
        settings = get_settings()

    retriever = build_retriever(stores, settings)
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
) -> Callable[[str], GenerationResult]:
    """Retorna uma função ``run(question: str) -> GenerationResult``.

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
    Callable[[str], GenerationResult]
        Função que recebe uma pergunta e retorna ``GenerationResult``.
    """
    if settings is None:
        settings = get_settings()

    def run(question: str) -> GenerationResult:
        return run_query(question, stores, profile, settings)

    return run
