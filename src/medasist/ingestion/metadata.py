from __future__ import annotations

import logging
from dataclasses import dataclass

from medasist.ingestion.chunker import TextChunk
from medasist.ingestion.schemas import DocType

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ChunkMetadata:
    """Metadados anexados a um chunk para indexação no ChromaDB.

    Attributes
    ----------
    doc_type : str
        Tipo do documento (valor string do DocType).
    source_path : str
        Caminho do arquivo de origem como string.
    sha256 : str
        Hash SHA-256 do documento pai.
    chunk_index : int
        Posição do chunk na lista (0-based).
    char_count : int
        Número de caracteres no chunk.
    """

    doc_type: str
    source_path: str
    sha256: str
    chunk_index: int
    char_count: int


def build_metadata(chunk: TextChunk) -> ChunkMetadata:
    """Constrói o objeto de metadados a partir de um TextChunk.

    Parameters
    ----------
    chunk : TextChunk
        Chunk de texto com informações de origem.

    Returns
    -------
    ChunkMetadata
        Metadados prontos para indexação no ChromaDB.
    """
    return ChunkMetadata(
        doc_type=chunk.doc_type.value,
        source_path=chunk.source_path.name,
        sha256=chunk.sha256,
        chunk_index=chunk.chunk_index,
        char_count=len(chunk.text),
    )


def build_metadata_batch(chunks: list[TextChunk]) -> list[ChunkMetadata]:
    """Constrói metadados para uma lista de chunks.

    Parameters
    ----------
    chunks : list[TextChunk]
        Lista de chunks a processar.

    Returns
    -------
    list[ChunkMetadata]
        Lista de metadados na mesma ordem dos chunks.
    """
    return [build_metadata(chunk) for chunk in chunks]
