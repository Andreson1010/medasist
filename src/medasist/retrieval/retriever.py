from __future__ import annotations

import logging
import re
import time
from typing import Any

from langchain_chroma import Chroma
from langchain_core.callbacks import CallbackManagerForRetrieverRun
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from pydantic import ConfigDict

from medasist.config import Settings
from medasist.ingestion.schemas import DocType
from medasist.retrieval.query_rewrite import rewrite_query
from medasist.retrieval.reranker import rerank_documents
from medasist.retrieval.sparse import get_sparse_index

logger = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"\b[a-zà-ú0-9]+\b")


# ---------------------------------------------------------------------------
# Retriever composto (multi-store)
# ---------------------------------------------------------------------------


class _MultiStoreRetriever(BaseRetriever):
    """Retriever que agrega resultados de múltiplos vectorstores ChromaDB.

    Aplica filtro de score (distância L2) para garantir que apenas chunks
    relevantes sejam retornados. Quando nenhum chunk supera o threshold,
    retorna lista vazia (cold start — regra de segurança médica).

    Parameters
    ----------
    stores : dict[DocType, Chroma]
        Mapeamento de DocType para vectorstore.
    settings : Settings
        Configurações com ``retrieval_top_k`` e ``retrieval_score_threshold``.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    stores: dict[DocType, Any]
    settings: Settings

    def _get_relevant_documents(
        self,
        query: str,
        *,
        run_manager: CallbackManagerForRetrieverRun,
    ) -> list[Document]:
        return retrieve(query, self.stores, self.settings)


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------


def select_collections(
    stores: dict[DocType, Any],
    doc_types: list[DocType] | None,
) -> dict[DocType, Any]:
    """Seleciona o subconjunto de stores consultado para os ``doc_types`` dados.

    Quando ``doc_types`` é fornecido (lista não vazia), apenas as coleções
    correspondentes são selecionadas — coleções ausentes em ``stores`` são
    ignoradas; caso contrário, retorna todas as stores. Nunca muta ``stores``.
    Regra compartilhada com ``run_query`` para que retrieval e geração usem
    exatamente as mesmas coleções.

    Parameters
    ----------
    stores : dict[DocType, Any]
        Mapeamento DocType → vectorstore disponíveis.
    doc_types : list[DocType] | None
        Tipos de documento desejados (``None`` ou lista vazia = todos).

    Returns
    -------
    dict[DocType, Any]
        Subconjunto de stores a consultar (o próprio ``stores`` quando todos).
    """
    if doc_types:
        return {dt: stores[dt] for dt in doc_types if dt in stores}
    return stores


def build_retriever(
    stores: dict[DocType, Chroma],
    settings: Settings,
) -> BaseRetriever:
    """Constrói retriever por similaridade sobre um ou mais vectorstores.

    Parameters
    ----------
    stores : dict[DocType, Chroma]
        Mapeamento DocType → vectorstore (de ``get_all_vectorstores`` ou subconjunto).
    settings : Settings
        Configurações com ``retrieval_top_k`` e ``retrieval_score_threshold``.

    Returns
    -------
    BaseRetriever
        Retriever LangChain com método ``invoke``.
    """
    logger.debug(
        "Construindo retriever para %d store(s): %s",
        len(stores),
        [dt.value for dt in stores],
    )
    return _MultiStoreRetriever(stores=stores, settings=settings)


def retrieve(
    query: str,
    stores: dict[DocType, Chroma],
    settings: Settings,
) -> list[Document]:
    """Executa busca por similaridade em todos os stores e filtra pelo threshold.

    Usa ``similarity_search_with_score`` que retorna pares ``(Document, float)``
    onde o float é distância L2 (menor = mais similar). Documentos com distância
    acima de ``settings.retrieval_score_threshold`` são descartados.

    Se nenhum documento supera o threshold, retorna lista vazia (cold start),
    garantindo que o LLM não seja chamado sem contexto relevante.

    Parameters
    ----------
    query : str
        Pergunta do usuário.
    stores : dict[DocType, Chroma]
        Mapeamento DocType → vectorstore para pesquisar.
    settings : Settings
        Configurações com ``retrieval_top_k`` e ``retrieval_score_threshold``.

    Returns
    -------
    list[Document]
        Documentos relevantes, sem duplicatas, ordenados por distância crescente.
        Lista vazia se nenhum documento superar o threshold (cold start).
    """
    if not stores:
        logger.warning("retrieve chamado com stores vazio — cold start.")
        return []

    # Reescrita de consultas curtas (RAG-03): a consulta efetiva (possivelmente
    # expandida) é usada na busca densa/esparsa, na guarda lexical e no rerank.
    # A pergunta ORIGINAL (``query``) permanece para o log e para a geração.
    #
    # AC7: sem dados para buscar (cold start), a reescrita nunca é chamada e o
    # retrieve retorna ``[]`` sem consultar o LLM de reescrita.
    if settings.retrieval_query_rewrite_enabled and not _stores_have_data(stores):
        logger.info("Cold start: stores sem dados — reescrita de consulta ignorada.")
        _log_retrieve_metric(query, stores, [], [], 0, failed_stores=[])
        return []

    effective_query = rewrite_query(query, settings)
    rewritten = effective_query != query

    k = settings.retrieval_top_k
    threshold = settings.retrieval_score_threshold
    start = time.perf_counter()

    candidates: list[tuple[Document, float]] = []
    failed_stores: list[str] = []

    for doc_type, store in stores.items():
        try:
            results = store.similarity_search_with_score(effective_query, k=k)
            logger.debug(
                "Store '%s': %d resultado(s) para query '%s'",
                doc_type.value,
                len(results),
                query[:50],
            )
            for doc, score in results:
                # score é distância L2: menor = mais similar
                # filtra docs com distância acima do threshold (muito distantes)
                if score <= threshold:
                    candidates.append((doc, score))
        except Exception:
            logger.exception("Erro ao consultar store '%s'", doc_type.value)
            failed_stores.append(doc_type.value)

    elapsed_ms = int((time.perf_counter() - start) * 1000)

    # Busca híbrida (RAG-02): combina denso + esparso (BM25) via RRF quando a
    # flag está ativa. Cold start é decidido pós-fusão e pós-guarda.
    if settings.retrieval_hybrid_enabled:
        return _retrieve_hybrid(
            effective_query,
            stores,
            settings,
            k,
            threshold,
            candidates,
            failed_stores,
            start,
            rewritten=rewritten,
        )

    if not candidates:
        if failed_stores:
            logger.error(
                "Nenhum resultado: falha de infra em store(s) %s para query '%s'",
                failed_stores,
                query[:50],
            )
        else:
            logger.info(
                "Cold start: nenhum chunk com distância L2 <= %.3f para query '%s'",
                threshold,
                query[:50],
            )
        _log_retrieve_metric(
            query,
            stores,
            [],
            [],
            elapsed_ms,
            failed_stores=failed_stores,
            rewritten=rewritten,
        )
        return []

    # Remove duplicatas por page_content, mantém o de menor distância
    seen: dict[str, tuple[Document, float]] = {}
    for doc, score in candidates:
        content = doc.page_content
        if content not in seen or score < seen[content][1]:
            seen[content] = (doc, score)

    # Ordena por distância crescente
    sorted_docs = sorted(seen.values(), key=lambda x: x[1])

    # Guarda lexical: impede contaminação cruzada entre documentos.
    # Se a consulta menciona um medicamento (termo com sufixo de droga) que
    # não aparece em nenhum chunk recuperado, trata como cold start em vez de
    # permitir que o LLM alucine a partir de um documento sobre outro fármaco.
    guarded = _lexical_relevance_guard(effective_query, sorted_docs, settings)

    # Rerank (RAG-01): reordena os candidatos guarda-aprovados por score do
    # cross-encoder, sempre DEPOIS do guarda lexical e ANTES do corte final.
    # Cold start é decidido pré-rerank no L2 — o rerank nunca esvazia um
    # contexto já válido, apenas o reordena.
    if settings.retrieval_rerank_enabled and guarded:
        guarded = rerank_documents(guarded, effective_query, settings)

    top_docs = guarded[:k]
    scores = [score for _, score in top_docs]

    if not top_docs and sorted_docs:
        elapsed_ms = int((time.perf_counter() - start) * 1000)
        _log_retrieve_metric(
            query,
            stores,
            [],
            [],
            elapsed_ms,
            failed_stores=failed_stores,
            rewritten=rewritten,
        )
        return []

    logger.debug(
        "retrieve retorna %d documento(s) após deduplicação e top_k.", len(top_docs)
    )
    _log_retrieve_metric(
        query,
        stores,
        [doc for doc, _ in top_docs],
        scores,
        elapsed_ms,
        failed_stores=failed_stores,
        rewritten=rewritten,
    )
    return [doc for doc, _ in top_docs]


def _rrf_fuse(
    dense: list[tuple[Document, float]],
    sparse: list[tuple[Document, float]],
    k: int,
) -> list[tuple[Document, float]]:
    """Funde listas densa e esparsa por Reciprocal Rank Fusion (RRF).

    Cada documento recebe score ``sum(1/(k + rank))`` por lista em que
    aparece. O mesmo ``page_content`` presente nos dois caminhos é retornado
    uma única vez, com as contribuições somadas (dedup cross-path). A lista
    final é ordenada por score RRF decrescente, com empate resolvido de forma
    determinística: candidato denso precede esparso (ordenação estável).

    Parameters
    ----------
    dense : list[tuple[Document, float]]
        Candidatos densos (filtrados por L2), na ordem de rank.
    sparse : list[tuple[Document, float]]
        Candidatos esparsos (BM25), na ordem de rank.
    k : int
        Constante ``retrieval_hybrid_rrf_k`` da fusão.

    Returns
    -------
    list[tuple[Document, float]]
        Documentos deduplicados com score RRF, ordenados por score desc.
    """
    fused: dict[str, dict[str, object]] = {}

    for rank, (doc, _score) in enumerate(dense, start=1):
        content = doc.page_content
        entry = fused.setdefault(
            content,
            {
                "doc": doc,
                "rrf": 0.0,
                "dense_rank": None,
                "from_dense": False,
            },
        )
        entry["rrf"] = entry["rrf"] + 1.0 / (k + rank)
        if entry["dense_rank"] is None:
            entry["dense_rank"] = rank
        entry["from_dense"] = True

    for rank, (doc, _score) in enumerate(sparse, start=1):
        content = doc.page_content
        entry = fused.setdefault(
            content,
            {
                "doc": doc,
                "rrf": 0.0,
                "dense_rank": None,
                "from_dense": False,
            },
        )
        entry["rrf"] = entry["rrf"] + 1.0 / (k + rank)

    entries = list(fused.values())

    def sort_key(entry: dict[str, object]) -> tuple[float, int, float]:
        rrf = entry["rrf"]
        dense_bias = 0 if entry["from_dense"] else 1
        dense_rank = entry["dense_rank"]
        rank = dense_rank if dense_rank is not None else float("inf")
        return (-rrf, dense_bias, rank)

    ordered = sorted(entries, key=sort_key)
    return [(entry["doc"], entry["rrf"]) for entry in ordered]


def _retrieve_hybrid(
    query: str,
    stores: dict[DocType, Chroma],
    settings: Settings,
    k: int,
    threshold: float,
    dense_candidates: list[tuple[Document, float]],
    failed_stores: list[str],
    start: float,
    *,
    rewritten: bool = False,
) -> list[Document]:
    """Executa o funil híbrido (denso + esparso + RRF) dentro de ``retrieve``.

    Combina os candidatos densos (já filtrados por L2) com os esparsos (BM25
    por DocType, limitados por ``retrieval_hybrid_sparse_top_k``) via
    ``_rrf_fuse``, aplica a guarda lexical sobre a lista fundida e, se
    habilitado, o rerank RAG-01, antes do corte ``[:k]``. Cold start é
    decidido pós-fusão e pós-guarda: hit apenas esparso aprovado pela guarda
    NÃO é cold start; ambos vazios é cold start.

    O caminho esparso nunca propaga exceção: em falha loga e segue dense-only,
    preservando o contexto denso válido.

    Parameters
    ----------
    query : str
        Pergunta do usuário.
    stores : dict[DocType, Chroma]
        Subconjunto de stores consultado (já respeita ``select_collections``).
    settings : Settings
        Configurações híbridas, de rerank e de top-k.
    k : int
        ``retrieval_top_k``.
    threshold : float
        ``retrieval_score_threshold`` (usado apenas em logs de cold start).
    dense_candidates : list[tuple[Document, float]]
        Candidatos densos filtrados por L2.
    failed_stores : list[str]
        Stores que falharam no caminho denso.
    start : float
        Timestamp de início para cálculo da latência.

    Returns
    -------
    list[Document]
        Documentos finais (após fusão, guarda, rerank e corte) ou ``[]`` em
        cold start.
    """
    sparse_top_k = settings.retrieval_hybrid_sparse_top_k
    rrf_k = settings.retrieval_hybrid_rrf_k

    sparse_candidates: list[tuple[Document, float]] = []
    n_sparse = 0

    for doc_type, store in stores.items():
        try:
            index = get_sparse_index(store, settings)
            if index is None:
                continue
            hits = index.search(query, sparse_top_k)
            n_sparse += len(hits)
            sparse_candidates.extend(hits)
        except Exception:
            logger.exception("Erro na busca esparsa para store '%s'", doc_type.value)

    n_dense = len(dense_candidates)

    fused = _rrf_fuse(dense_candidates, sparse_candidates, rrf_k)
    guarded = _lexical_relevance_guard(query, fused, settings)

    if settings.retrieval_rerank_enabled and guarded:
        guarded = rerank_documents(guarded, query, settings)

    top_docs = guarded[:k]
    scores = [score for _, score in top_docs]
    elapsed_ms = int((time.perf_counter() - start) * 1000)

    if not top_docs:
        if failed_stores:
            logger.error(
                "Nenhum resultado híbrido: falha de infra em store(s) %s "
                "para query '%s'",
                failed_stores,
                query[:50],
            )
        else:
            logger.info(
                "Cold start híbrido: nenhum chunk (denso+esparso) aprovado "
                "pela guarda para query '%s'",
                query[:50],
            )
        _log_retrieve_metric(
            query,
            stores,
            [],
            [],
            elapsed_ms,
            failed_stores=failed_stores,
            n_dense_candidates=n_dense,
            n_sparse_candidates=n_sparse,
            hybrid=True,
            rewritten=rewritten,
        )
        return []

    logger.debug(
        "retrieve híbrido retorna %d documento(s) após fusão RRF e top_k.",
        len(top_docs),
    )
    _log_retrieve_metric(
        query,
        stores,
        [doc for doc, _ in top_docs],
        scores,
        elapsed_ms,
        failed_stores=failed_stores,
        n_dense_candidates=n_dense,
        n_sparse_candidates=n_sparse,
        hybrid=True,
        rewritten=rewritten,
    )
    return [doc for doc, _ in top_docs]


def _stores_have_data(stores: dict[DocType, Any]) -> bool:
    """Verifica se pelo menos um store tem documentos para buscar.

    Consulta a contagem da coleção de cada store. Retorna ``True`` na primeira
    coleção com documentos e ``False`` quando todas estão vazias (cold start).
    Em falha da checagem, assume ``True`` (não-vazio) para nunca pular busca
    legítima. Usada para pular a reescrita de consulta em cold start (AC7).

    Parameters
    ----------
    stores : dict[DocType, Any]
        Stores consultadas.

    Returns
    -------
    bool
        ``True`` quando há dados em pelo menos um store.
    """
    for store in stores.values():
        try:
            if store._collection.count() > 0:
                return True
        except Exception:
            logger.exception("Falha ao checar dados do store — assumindo não-vazio.")
            return True
    return False


def _lexical_relevance_guard(
    query: str,
    docs: list[tuple[Document, float]],
    settings: Settings,
) -> list[tuple[Document, float]]:
    """Bloqueia chunks que não têm relação lexical com o medicamento perguntado.

    Extrai da consulta os termos com cara de medicamento (sufixo de droga).
    Quando existe pelo menos um desses termos, exige que ele apareça em ao
    menos um chunk recuperado. Se nenhum chunk menciona o medicamento, retorna
    lista vazia (cold start) — evita que o LLM responda sobre um fármaco usando
    apenas documentos de outro fármaco.

    Parameters
    ----------
    query : str
        Pergunta do usuário.
    docs : list[tuple[Document, float]]
        Candidatos já deduplicados e ordenados (Document, distância).
    settings : Settings
        Configurações com stopwords e sufixos de droga.

    Returns
    -------
    list[tuple[Document, float]]
        Candidatos originais quando pelo menos um menciona o medicamento da
        consulta, ou lista vazia quando nenhum supera a guarda lexical.
    """
    drug_terms = _drug_terms_in(query, settings)
    if not drug_terms:
        return docs

    texts: list[str] = []
    for doc, _ in docs:
        source = doc.metadata.get("source", "") or doc.metadata.get("source_path", "")
        texts.append(f"{doc.page_content} {source}".lower())
    corpus_text = " ".join(texts)
    if any(term in corpus_text for term in drug_terms):
        return docs

    logger.warning(
        "Guarda lexical: consulta menciona medicamento(s) %s, mas nenhum chunk "
        "recuperado os contém. Tratando como cold start.",
        sorted(drug_terms),
    )
    return []


def _drug_terms_in(query: str, settings: Settings) -> set[str]:
    """Retorna termos com sufixo de droga presentes na consulta.

    Considera apenas termos com comprimento >= ``retrieval_drug_term_min_len``
    para evitar falsos positivos de palavras comuns curtas.

    Parameters
    ----------
    query : str
        Pergunta do usuário.
    settings : Settings
        Configurações com stopwords, sufixos de droga e comprimento mínimo.

    Returns
    -------
    set[str]
        Termos da consulta (minúsculos, sem stopwords) que terminam com um
        sufixo típico de medicamento.
    """
    tokens = {m.group(0) for m in _TOKEN_RE.finditer(query.lower())}
    stopwords = set(settings.retrieval_stopwords)
    content_tokens = tokens - stopwords
    min_len = settings.retrieval_drug_term_min_len
    return {
        t
        for t in content_tokens
        if len(t) >= min_len and t.endswith(settings.retrieval_drug_suffixes)
    }


def _log_retrieve_metric(
    query: str,
    stores: dict[DocType, Chroma],
    docs: list[Document],
    scores: list[float],
    latency_ms: int,
    *,
    failed_stores: list[str],
    n_dense_candidates: int = 0,
    n_sparse_candidates: int = 0,
    hybrid: bool = False,
    rewritten: bool = False,
) -> None:
    """Registra métrica consolidada de retrieval por query.

    Um único registro por query com os campos ``doc_types``, ``chunks``,
    ``scores``, ``latency_ms`` e ``cold_start``. Quando alguma store falhou,
    adiciona ``failed_stores``. Quando a busca híbrida está ativa, adiciona os
    campos ``hybrid``, ``n_dense_candidates`` e ``n_sparse_candidates``
    (contagens de candidatos por caminho), preservando os campos existentes —
    mudança aditiva que não quebra os testes de log atuais. Quando a consulta
    foi reescrita (RAG-03), adiciona ``rewritten=True`` — também aditivo. A
    query é truncada a 50 caracteres (padrão existente do retriever) e nenhum
    dado de paciente é registrado.

    Parameters
    ----------
    query : str
        Pergunta do usuário.
    stores : dict[DocType, Chroma]
        Stores consultadas.
    docs : list[Document]
        Documentos retornados.
    scores : list[float]
        Distâncias L2 dos documentos retornados (paralelo a ``docs``).
    latency_ms : int
        Latência total do loop de stores em milissegundos.
    failed_stores : list[str]
        Stores que falharam durante a consulta.
    n_dense_candidates : int
        Número de candidatos do caminho denso (apenas híbrido).
    n_sparse_candidates : int
        Número de candidatos do caminho esparso (apenas híbrido).
    hybrid : bool
        Indica se a métrica é de uma execução híbrida.
    rewritten : bool
        Indica se a consulta efetiva usada na busca difere da original.
    """
    message = (
        "retrieve: query='%s' doc_types=%s chunks=%d scores=%s latency_ms=%d "
        "cold_start=%s"
    )
    args: list[Any] = [
        query[:50],
        [dt.value for dt in stores],
        len(docs),
        scores,
        latency_ms,
        not docs,
    ]
    if hybrid:
        message += " hybrid=%s n_dense_candidates=%d n_sparse_candidates=%d"
        args.extend([True, n_dense_candidates, n_sparse_candidates])
    if failed_stores:
        message += " failed_stores=%s"
        args.append(failed_stores)
    if rewritten:
        message += " rewritten=%s"
        args.append(True)
    logger.info(message, *args)
