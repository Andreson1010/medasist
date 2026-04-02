from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from langchain_core.documents import Document

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CitationItem:
    """Referência a um chunk recuperado, associada a um marcador ``[N]`` na resposta.

    Attributes
    ----------
    index : int
        Número do marcador ``[N]`` na resposta gerada.
    source : str
        Nome ou caminho do documento de origem (``metadata["source"]``).
    section : str
        Seção do documento (``metadata.get("section", "")``).
    page : str
        Página do documento (``metadata.get("page", "")``).
    """

    index: int
    source: str
    section: str
    page: str


def build_citations(docs: list[Document]) -> list[CitationItem]:
    """Constrói lista de CitationItem a partir dos documentos recuperados.

    Os índices começam em 1 e correspondem à numeração ``[N]`` que o LLM
    deve usar na resposta ao referenciar o contexto fornecido.

    Parameters
    ----------
    docs : list[Document]
        Documentos retornados pelo retriever.

    Returns
    -------
    list[CitationItem]
        Uma CitationItem por documento, com índice 1-based.
    """
    citations = []
    for i, doc in enumerate(docs, start=1):
        meta = doc.metadata
        citations.append(
            CitationItem(
                index=i,
                source=meta.get("source_path", meta.get("source", "")),
                section=meta.get("section", ""),
                page=str(meta.get("page", "")),
            )
        )
    logger.debug("build_citations: %d citações construídas.", len(citations))
    return citations


def validate_citations(
    answer: str,
    citations: list[CitationItem],
) -> tuple[str, list[CitationItem]]:
    """Filtra citações órfãs e remove marcadores ``[N]`` sem correspondência.

    Varre a resposta em busca de padrões ``[N]`` e retém somente os
    ``CitationItem`` cujo ``index`` aparece no texto. Marcadores que
    referenciam índices inexistentes na lista de citações (alucinações do LLM)
    são removidos do texto para evitar referências pendentes.

    Parameters
    ----------
    answer : str
        Texto gerado pelo LLM, possivelmente contendo ``[1]``, ``[2]``, etc.
    citations : list[CitationItem]
        Lista completa de citações construída por ``build_citations``.

    Returns
    -------
    tuple[str, list[CitationItem]]
        ``(cleaned_answer, valid_citations)`` onde ``cleaned_answer`` não
        contém marcadores sem ``CitationItem`` correspondente e
        ``valid_citations`` contém apenas as citações referenciadas.
    """
    valid_indices: set[int] = {c.index for c in citations}
    used_indices: set[int] = {int(m) for m in re.findall(r"\[(\d+)\]", answer)}

    hallucinated = used_indices - valid_indices
    if hallucinated:
        logger.warning(
            "validate_citations: %d marcador(es) sem citação correspondente "
            "removido(s) da resposta: %s.",
            len(hallucinated),
            sorted(hallucinated),
        )
        for idx in hallucinated:
            answer = re.sub(rf"\[{idx}\]", "", answer)

    valid = [
        c for c in citations if c.index in used_indices and c.index in valid_indices
    ]

    logger.debug(
        "validate_citations: %d/%d citações válidas (usadas na resposta).",
        len(valid),
        len(citations),
    )
    return answer, valid
