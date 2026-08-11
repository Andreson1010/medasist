from __future__ import annotations

import logging
import time
from typing import Any

from langchain_chroma import Chroma
from langchain_core.callbacks import CallbackManagerForRetrieverRun
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from pydantic import ConfigDict

from medasist.config import Settings
from medasist.ingestion.schemas import DocType

logger = logging.getLogger(__name__)


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

    k = settings.retrieval_top_k
    threshold = settings.retrieval_score_threshold
    start = time.perf_counter()

    candidates: list[tuple[Document, float]] = []
    failed_stores: list[str] = []

    for doc_type, store in stores.items():
        try:
            results = store.similarity_search_with_score(query, k=k)
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
            query, stores, [], [], elapsed_ms, failed_stores=failed_stores
        )
        return []

    # Remove duplicatas por page_content, mantém o de menor distância
    seen: dict[str, tuple[Document, float]] = {}
    for doc, score in candidates:
        content = doc.page_content
        if content not in seen or score < seen[content][1]:
            seen[content] = (doc, score)

    # Ordena por distância crescente e respeita top_k
    sorted_docs = sorted(seen.values(), key=lambda x: x[1])
    top_docs = sorted_docs[:k]
    scores = [score for _, score in top_docs]

    logger.debug(
        "retrieve retorna %d documento(s) após deduplicação e top_k.", len(top_docs)
    )
    _log_retrieve_metric(
        query, stores, top_docs, scores, elapsed_ms, failed_stores=failed_stores
    )
    return [doc for doc, _ in top_docs]


def _log_retrieve_metric(
    query: str,
    stores: dict[DocType, Chroma],
    docs: list[Document],
    scores: list[float],
    latency_ms: int,
    *,
    failed_stores: list[str],
) -> None:
    """Registra métrica consolidada de retrieval por query.

    Um único registro por query com os campos ``doc_types``, ``chunks``,
    ``scores``, ``latency_ms`` e ``cold_start``. Quando alguma store falhou,
    adiciona ``failed_stores``. A query é truncada a 50 caracteres (padrão
    existente do retriever) e nenhum dado de paciente é registrado.

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
    if failed_stores:
        message += " failed_stores=%s"
        args.append(failed_stores)
    logger.info(message, *args)
