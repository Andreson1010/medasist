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
    remap_answer,
    validate_citations,
)
from medasist.generation.prompts import PromptRegistry
from medasist.ingestion.schemas import DocType
from medasist.profiles.schemas import UserProfile, get_profile_config
from medasist.retrieval.decompose import decompose_query
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
    unanswered_sub_questions : list[str]
        Sub-perguntas de uma pergunta composta que não foram respondidas
        (vazia quando não há decomposição ou quando todas foram respondidas).
    """

    answer: str
    citations: list[CitationItem] = field(default_factory=list)
    profile: UserProfile = UserProfile.MEDICO
    disclaimer: str = ""
    is_cold_start: bool = False
    unanswered_sub_questions: list[str] = field(default_factory=list)


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


def _run_single(
    question: str,
    stores: dict[Any, Any],
    profile: UserProfile,
    settings: Settings | None = None,
    doc_types: list[DocType] | None = None,
) -> GenerationResult:
    """Executa o pipeline RAG completo para uma única pergunta (sub ou total).

    Fluxo:
    1. Recupera documentos relevantes via ``build_retriever``.
    2. Cold start guard: se não houver documentos, retorna mensagem fixa
       sem chamar o LLM.
    3. Monta contexto numerado e extrai citações.
    4. Chama o LLM via LCEL ``prompt | ChatOpenAI | StrOutputParser``.
    5. Valida e filtra citações órfãs.

    É o corpo do antigo ``run_query`` extraído verbatim; ``run_query`` o
    reusa para a pergunta total quando não há decomposição e para cada
    sub-pergunta quando há.

    Parameters
    ----------
    question : str
        Pergunta (total ou sub-pergunta) do usuário.
    stores : dict
        Mapeamento ``DocType → Chroma`` (de ``get_all_vectorstores``).
    profile : UserProfile
        Perfil do usuário para selecionar temperatura, max_tokens e prompt.
    settings : Settings | None
        Configurações. Se ``None``, usa o singleton ``get_settings()``.
    doc_types : list[DocType] | None
        Filtro opcional de tipos de documento (via ``select_collections``).

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


def _merge_sub_results(
    subs: list[str],
    sub_results: list[GenerationResult],
    settings: Settings,
) -> GenerationResult:
    """Recombina sub-respostas numa resposta única com citações re-numeradas.

    Para cada sub-pergunta com citação válida, remapeia os marcadores ``[N]``
    via ``remap_answer`` por um offset acumulado e re-numera as citações num
    espaço 1-based único; as respostas são concatenadas. Sub-perguntas sem
    citação válida (cold start ou resposta sem fonte) não entram no merged e
    são registradas em ``unanswered_sub_questions``. Se nenhuma sub-pergunta
    tem citação válida, retorna cold start total (regra médica: nunca resposta
    sem fonte).

    Parameters
    ----------
    subs : list[str]
        Textos das sub-perguntas, na mesma ordem de ``sub_results``.
    sub_results : list[GenerationResult]
        Resultados de ``_run_single`` por sub-pergunta.
    settings : Settings
        Configurações com textos de segurança.

    Returns
    -------
    GenerationResult
        Resultado merged com citações re-numeradas e disclaimer, ou cold start
        total quando nenhuma sub-pergunta tem citação válida.
    """
    merged_parts: list[str] = []
    merged_citations: list[CitationItem] = []
    unanswered: list[str] = []
    offset = 0

    for sub, result in zip(subs, sub_results, strict=True):
        if result.is_cold_start or not result.citations:
            unanswered.append(sub)
            continue
        merged_parts.append(remap_answer(result.answer, offset))
        for citation in result.citations:
            merged_citations.append(
                CitationItem(
                    index=citation.index + offset,
                    source=citation.source,
                    section=citation.section,
                    page=citation.page,
                )
            )
        offset += len(result.citations)

    profile = sub_results[0].profile

    if not merged_citations:
        logger.info(
            "run_query: nenhuma sub-pergunta com citação válida — cold start total.",
        )
        return GenerationResult(
            answer=settings.cold_start_message,
            citations=[],
            profile=profile,
            disclaimer=settings.disclaimer,
            is_cold_start=True,
        )

    return GenerationResult(
        answer="\n\n".join(merged_parts),
        citations=merged_citations,
        profile=profile,
        disclaimer=settings.disclaimer,
        is_cold_start=False,
        unanswered_sub_questions=unanswered,
    )


def run_query(
    question: str,
    stores: dict[Any, Any],
    profile: UserProfile,
    settings: Settings | None = None,
    doc_types: list[DocType] | None = None,
) -> GenerationResult:
    """Executa o pipeline RAG completo, com decomposição multi-parte opcional.

    Chama ``decompose_query``: quando a pergunta não é decomposta (flag off,
    não-composta, falha/0/1 sub), delega a ``_run_single`` (identidade). Quando
    decomposta em 2+ sub-perguntas, roda cada uma por ``_run_single`` e
    recombina via ``_merge_sub_results`` (citações re-numeradas e ``[N]``
    remapeados). Nunca fabrica conteúdo para sub-perguntas sem hit — misses
    vão para ``unanswered_sub_questions``. Com ``stores`` vazio (ou
    ``doc_types`` que filtram todas as coleções), retorna cold start antes de
    chamar ``decompose_query`` — o LLM de split nunca é chamado (edge case
    RAG-03).

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

    Returns
    -------
    GenerationResult
        Resultado com resposta, citações, perfil, disclaimer, flag de cold start
        e sub-perguntas não respondidas.
    """
    if settings is None:
        settings = get_settings()

    # RAG-03 edge case: `stores` vazio ou `doc_types` que filtram todas as
    # coleções → cold start ANTES de qualquer split — o LLM de split nunca é
    # chamado (mesma semântica do early return de stores vazio em `retrieve`).
    if not select_collections(stores, doc_types):
        logger.info(
            "run_query: stores sem coleções selecionadas — cold start antes "
            "de qualquer split."
        )
        return GenerationResult(
            answer=settings.cold_start_message,
            citations=[],
            profile=profile,
            disclaimer=settings.disclaimer,
            is_cold_start=True,
        )

    subs = decompose_query(question, settings)
    if len(subs) == 1:
        return _run_single(question, stores, profile, settings, doc_types)

    sub_results = [
        _run_single(sub, stores, profile, settings, doc_types) for sub in subs
    ]
    merged = _merge_sub_results(subs, sub_results, settings)

    hits = sum(1 for r in sub_results if not r.is_cold_start and r.citations)
    misses = len(sub_results) - hits
    logger.info(
        "run_query: pergunta composta — %d sub-pergunta(s), hits=%d, misses=%d.",
        len(subs),
        hits,
        misses,
    )
    return merged


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


def _stream_single(
    question: str,
    stores: dict[Any, Any],
    profile: UserProfile,
    settings: Settings,
    doc_types: list[DocType] | None = None,
) -> Generator[str, None, tuple[str, list[CitationItem], bool]]:
    """Gera a resposta de uma única pergunta de forma incremental.

    Espelha ``_run_single`` para o caminho de streaming (retrieval, cold start,
    citações, perfil, doc_types), trocando ``chain.invoke`` por ``chain.stream``.
    Cada chunk do LLM é yieldado enquanto o texto completo é acumulado; ao final,
    ``validate_citations`` roda sobre a resposta completa. O estado terminal é
    comunicado pelo valor de retorno do gerador:
    ``(full_answer, citations, is_cold_start)``.

    Parameters
    ----------
    question : str
        Pergunta (total ou sub-pergunta) do usuário.
    stores : dict
        Mapeamento ``DocType → Chroma`` (de ``get_all_vectorstores``).
    profile : UserProfile
        Perfil do usuário para selecionar temperatura, max_tokens e prompt.
    settings : Settings
        Configurações (já resolvidas pelo chamador).
    doc_types : list[DocType] | None
        Filtro opcional de tipos de documento (via ``select_collections``).

    Yields
    ------
    str
        Cada chunk de texto gerado pelo LLM.

    Returns
    -------
    tuple[str, list[CitationItem], bool]
        ``(texto_completo, citações_válidas, is_cold_start)``. Em cold start
        (retrieval vazio ou resposta sem citações válidas) retorna
        ``("", [], True)`` sem chamar o LLM quando o retrieval é vazio.
    """
    subset = select_collections(stores, doc_types)
    retriever = build_retriever(subset, settings)
    docs: list[Document] = retriever.invoke(question)

    # --- Cold start guard (regra de segurança médica inegociável) ---
    if not docs:
        logger.info(
            "stream_answer: cold start — nenhum chunk relevante para '%s'.",
            question[:60],
        )
        return "", [], True

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
        return full, [], True

    logger.info(
        "stream_answer: resposta gerada para profile='%s', citações=%d.",
        profile.value,
        len(valid_citations),
    )
    return full, valid_citations, False


def stream_answer(
    question: str,
    stores: dict[Any, Any],
    profile: UserProfile,
    settings: Settings | None = None,
    doc_types: list[DocType] | None = None,
) -> Generator[str, None, tuple[list[CitationItem], bool]]:
    """Gera a resposta do LLM incrementalmente, com decomposição multi-parte.

    Espelha ``run_query``: chama ``decompose_query`` e, quando a pergunta não é
    decomposta, delega a ``_stream_single`` (identidade byte-identical quando a
    flag está off). Quando decomposta em 2+ sub-perguntas, gera os deltas de
    cada sub pelo mesmo funil, acumula as respostas e recombina via
    ``_merge_sub_results`` no final, retornando ``(citations, is_cold_start)``
    conforme a política parcial (todas-miss → ``([], True)``; ≥1 hit →
    citações re-numeradas, ``False``). Com ``stores`` vazio (ou ``doc_types``
    que filtram todas as coleções), retorna cold start antes de chamar
    ``decompose_query`` — o LLM de split nunca é chamado (edge case RAG-03).

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
        Cada chunk de texto gerado pelo LLM (na ordem das sub-perguntas).

    Returns
    -------
    tuple[list[CitationItem], bool]
        ``(citações_válidas, is_cold_start)``. Em cold start (retrieval vazio
        ou resposta sem citações válidas) retorna ``([], True)`` sem chamar o
        LLM quando o retrieval é vazio.
    """
    if settings is None:
        settings = get_settings()

    # RAG-03 edge case: `stores` vazio ou `doc_types` que filtram todas as
    # coleções → cold start ANTES de qualquer split — o LLM de split nunca é
    # chamado (mesma semântica do early return de stores vazio em `retrieve`).
    if not select_collections(stores, doc_types):
        logger.info(
            "stream_answer: stores sem coleções selecionadas — cold start "
            "antes de qualquer split."
        )
        return [], True

    subs = decompose_query(question, settings)

    if len(subs) == 1:
        gen = _stream_single(question, stores, profile, settings, doc_types)
        try:
            while True:
                try:
                    yield next(gen)
                except StopIteration as stop:
                    _, citations, is_cold_start = stop.value
                    return citations, is_cold_start
        finally:
            gen.close()

    sub_results: list[GenerationResult] = []
    for sub in subs:
        gen = _stream_single(sub, stores, profile, settings, doc_types)
        try:
            full = ""
            while True:
                try:
                    chunk = next(gen)
                except StopIteration as stop:
                    full, citations, is_cold_start = stop.value
                    break
                full += chunk
                yield chunk
        finally:
            gen.close()
        sub_results.append(
            GenerationResult(
                answer=full,
                citations=citations,
                profile=profile,
                disclaimer=settings.disclaimer,
                is_cold_start=is_cold_start,
            )
        )

    merged = _merge_sub_results(subs, sub_results, settings)
    return merged.citations, merged.is_cold_start


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
