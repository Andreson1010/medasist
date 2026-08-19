from __future__ import annotations

import logging
import threading
from typing import Any

from langchain_core.documents import Document

from medasist.config import Settings

logger = logging.getLogger(__name__)

_reranker: Any = None
_reranker_lock = threading.Lock()


def _get_reranker(settings: Settings) -> Any:
    """Retorna o singleton lazy thread-safe do ``CrossEncoder``.

    O modelo é carregado na primeira chamada (não no startup) e reutilizado
    nas demais. Usa double-checked locking para segurança multi-thread. O
    import de ``sentence_transformers`` é lazy (dentro da função), de modo que
    importar este módulo nunca requer o pacote/modelo instalado.

    Parameters
    ----------
    settings : Settings
        Configurações com ``retrieval_rerank_model``.

    Returns
    -------
    Any
        Instância do ``CrossEncoder`` carregada.

    Raises
    ------
    Exception
        Qualquer falha no carregamento do modelo (re-propaga para ser
        tratada por ``rerank_documents``).
    """
    global _reranker
    if _reranker is None:
        with _reranker_lock:
            if _reranker is None:
                from sentence_transformers import CrossEncoder

                _reranker = CrossEncoder(settings.retrieval_rerank_model)
                logger.info(
                    "CrossEncoder carregado: %s", settings.retrieval_rerank_model
                )
    return _reranker


def _rank(
    docs: list[tuple[Document, float]],
    query: str,
    settings: Settings,
) -> list[float]:
    """Pontua até ``rerank_top_n`` candidatos com o cross-encoder.

    Monta pares ``(query, doc.page_content)`` para os primeiros
    ``rerank_top_n`` documentos e chama ``predict`` em batch único,
    respeitando ``rerank_batch_size``.

    Parameters
    ----------
    docs : list[tuple[Document, float]]
        Candidatos (Document, distância L2) já filtrados pelo guarda lexical.
    query : str
        Pergunta do usuário.
    settings : Settings
        Configurações com ``retrieval_rerank_top_n`` e ``retrieval_rerank_batch_size``.

    Returns
    -------
    list[float]
        Scores do cross-encoder na mesma ordem dos ``docs`` de entrada
        (apenas os ``rerank_top_n`` primeiros).
    """
    reranker = _get_reranker(settings)
    top_n = settings.retrieval_rerank_top_n
    batch_size = settings.retrieval_rerank_batch_size
    pairs = [(query, doc.page_content) for doc, _ in docs[:top_n]]
    return reranker.predict(pairs, batch_size=batch_size)


def rerank_documents(
    docs: list[tuple[Document, float]],
    query: str,
    settings: Settings,
) -> list[tuple[Document, float]]:
    """Reordena candidatos por score do cross-encoder (maior primeiro).

    Quando ``retrieval_rerank_enabled`` é ``False`` (ou a lista está vazia),
    retorna ``docs`` inalterados (ordem L2) sem instanciar o modelo. Em
    falha do reranker (erro, timeout ou modelo ausente), loga a exceção e
    retorna ``docs`` na ordem L2 original — degradação graciosa, nunca
    propaga a falha para ``retrieve``.

    A ordenação é determinística: empates de score preservam a ordem L2
    original (ordenação estável sobre o score).

    Parameters
    ----------
    docs : list[tuple[Document, float]]
        Candidatos (Document, distância L2).
    query : str
        Pergunta do usuário.
    settings : Settings
        Configurações de rerank.

    Returns
    -------
    list[tuple[Document, float]]
        Candidatos reordenados por score do reranker (desc) ou, em falha/
        desabilitado, na ordem L2 original.
    """
    if not settings.retrieval_rerank_enabled or not docs:
        return docs

    try:
        scores = _rank(docs, query, settings)
    except Exception:
        logger.exception(
            "Reranker falhou para query '%s' — retornando ordem L2 original.",
            query[:50],
        )
        return docs

    scored = docs[: len(scores)]
    rest = docs[len(scores) :]
    indexed = list(enumerate(scored))
    ordered = sorted(indexed, key=lambda item: -scores[item[0]])
    return [pair for _, pair in ordered] + rest
