from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from langchain_text_splitters import RecursiveCharacterTextSplitter

from medasist.config import Settings
from medasist.ingestion.schemas import DocType, LoadedDocument

logger = logging.getLogger(__name__)

_MIN_CHUNK_LENGTH = 50

_SEPARATORS: dict[DocType, list[str]] = {
    DocType.BULA: ["\n\n", "\n", " "],
    DocType.DIRETRIZ: ["\n\n\n", "\n\n", "\n", " "],
    DocType.PROTOCOLO: ["\n\n", "\n", ". ", " "],
    DocType.MANUAL: ["\n\n", "\n", " "],
}


@dataclass(frozen=True)
class TextChunk:
    """Chunk de texto extraído de um documento médico.

    Attributes
    ----------
    text : str
        Conteúdo textual do chunk.
    doc_type : DocType
        Tipo do documento de origem.
    source_path : Path
        Caminho do arquivo PDF de origem.
    sha256 : str
        Hash SHA-256 do documento pai.
    chunk_index : int
        Posição do chunk na lista (0-based).
    """

    text: str
    doc_type: DocType
    source_path: Path
    sha256: str
    chunk_index: int


def _get_splitter(
    doc_type: DocType, settings: Settings
) -> RecursiveCharacterTextSplitter:
    """Retorna o splitter configurado para o DocType informado.

    Parameters
    ----------
    doc_type : DocType
        Tipo do documento.
    settings : Settings
        Configurações com chunk_size e chunk_overlap por DocType.

    Returns
    -------
    RecursiveCharacterTextSplitter
        Splitter pronto para uso.
    """
    chunk_size = getattr(settings, f"chunk_size_{doc_type.value}")
    chunk_overlap = getattr(settings, f"chunk_overlap_{doc_type.value}")
    return RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=_SEPARATORS[doc_type],
    )


def chunk_document(doc: LoadedDocument, settings: Settings) -> list[TextChunk]:
    """Divide um documento em chunks de texto por estratégia de DocType.

    Parameters
    ----------
    doc : LoadedDocument
        Documento carregado do disco.
    settings : Settings
        Configurações com tamanhos e overlaps por DocType.

    Returns
    -------
    list[TextChunk]
        Lista de chunks com metadados, excluindo textos curtos (< 50 chars).
    """
    text = doc.full_text
    if not text.strip():
        logger.debug("Documento vazio: %s", doc.path)
        return []

    splitter = _get_splitter(doc.doc_type, settings)
    raw_chunks = splitter.split_text(text)

    chunks: list[TextChunk] = []
    index = 0
    for raw in raw_chunks:
        if len(raw) < _MIN_CHUNK_LENGTH:
            logger.debug("Chunk ignorado (muito curto): %d chars", len(raw))
            continue
        chunks.append(
            TextChunk(
                text=raw,
                doc_type=doc.doc_type,
                source_path=doc.path,
                sha256=doc.sha256,
                chunk_index=index,
            )
        )
        index += 1

    logger.info(
        "Documento %s → %d chunks (%s)", doc.path.name, len(chunks), doc.doc_type
    )
    return chunks
