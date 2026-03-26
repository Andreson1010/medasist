from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import chromadb

from medasist.config import Settings, get_settings
from medasist.ingestion.chunker import chunk_document
from medasist.ingestion.loader import load_pdf
from medasist.ingestion.metadata import build_metadata_batch
from medasist.ingestion.schemas import DocType

logger = logging.getLogger(__name__)

EmbedFn = Callable[[list[str]], list[list[float]]]

_COLLECTION_ATTR: dict[DocType, str] = {
    DocType.BULA: "collection_bulas",
    DocType.DIRETRIZ: "collection_diretrizes",
    DocType.PROTOCOLO: "collection_protocolos",
    DocType.MANUAL: "collection_manuais",
}


@dataclass(frozen=True)
class IngestionResult:
    """Resultado da ingestão de um documento.

    Attributes
    ----------
    path : Path
        Caminho do arquivo processado.
    doc_type : DocType
        Tipo do documento.
    sha256 : str
        Hash SHA-256 do arquivo (vazio em caso de erro de carga).
    chunks_indexed : int
        Número de chunks indexados nesta execução.
    skipped : bool
        True se o documento já estava indexado (idempotência).
    error : str | None
        Mensagem de erro, ou None em caso de sucesso.
    """

    path: Path
    doc_type: DocType
    sha256: str
    chunks_indexed: int
    skipped: bool
    error: str | None = None


def build_embed_fn(settings: Settings) -> EmbedFn:
    """Constrói função de embedding usando LM Studio (OpenAI-compatible).

    Parameters
    ----------
    settings : Settings
        Configurações com URL e modelo de embedding.

    Returns
    -------
    EmbedFn
        Callable que recebe lista de textos e retorna lista de vetores.
    """
    from langchain_openai import OpenAIEmbeddings

    embeddings = OpenAIEmbeddings(
        base_url=settings.lm_studio_base_url,
        api_key=settings.lm_studio_api_key.get_secret_value(),
        model=settings.lm_studio_embedding_model,
    )
    return embeddings.embed_documents


def _collection_name(doc_type: DocType, settings: Settings) -> str:
    return getattr(settings, _COLLECTION_ATTR[doc_type])


def _already_indexed(collection: chromadb.Collection, sha256: str) -> bool:
    result = collection.get(where={"sha256": sha256}, limit=1, include=[])
    return len(result["ids"]) > 0


def ingest_document(
    path: Path,
    doc_type: DocType,
    chroma_client: chromadb.ClientAPI,
    settings: Settings,
    embed_fn: EmbedFn | None = None,
) -> IngestionResult:
    """Ingere um único documento PDF no ChromaDB.

    Carrega o PDF, divide em chunks, gera embeddings e faz upsert na coleção
    correspondente ao ``doc_type``. Documentos com sha256 já presente na
    coleção são pulados (idempotência).

    Parameters
    ----------
    path : Path
        Caminho do arquivo PDF.
    doc_type : DocType
        Tipo do documento para selecionar coleção e estratégia de chunking.
    chroma_client : chromadb.ClientAPI
        Cliente ChromaDB (PersistentClient em produção, EphemeralClient em testes).
    settings : Settings
        Configurações do sistema.
    embed_fn : EmbedFn | None
        Função de embedding. Se None, usa LM Studio via ``build_embed_fn``.

    Returns
    -------
    IngestionResult
        Resultado com contagem de chunks, flag de skip e eventual erro.
    """
    path = Path(path).resolve()

    if embed_fn is None:
        embed_fn = build_embed_fn(settings)

    col_name = _collection_name(doc_type, settings)
    collection = chroma_client.get_or_create_collection(name=col_name)

    try:
        doc = load_pdf(path, doc_type)
    except Exception as exc:
        logger.error("Falha ao carregar %s: %s", path.name, exc)
        return IngestionResult(
            path=path,
            doc_type=doc_type,
            sha256="",
            chunks_indexed=0,
            skipped=False,
            error=str(exc),
        )

    if _already_indexed(collection, doc.sha256):
        logger.info("Pulando %s — sha256 já indexado (%s…)", path.name, doc.sha256[:8])
        return IngestionResult(
            path=path,
            doc_type=doc_type,
            sha256=doc.sha256,
            chunks_indexed=0,
            skipped=True,
        )

    chunks = chunk_document(doc, settings)
    if not chunks:
        logger.warning("Nenhum chunk gerado para %s", path.name)
        return IngestionResult(
            path=path,
            doc_type=doc_type,
            sha256=doc.sha256,
            chunks_indexed=0,
            skipped=False,
        )

    metadata_list = build_metadata_batch(chunks)
    ids = [f"{c.sha256}_{c.chunk_index}" for c in chunks]
    texts = [c.text for c in chunks]
    metadatas = [
        {
            "doc_type": m.doc_type,
            "source_path": m.source_path,
            "sha256": m.sha256,
            "chunk_index": m.chunk_index,
            "char_count": m.char_count,
        }
        for m in metadata_list
    ]

    embeddings = embed_fn(texts)
    collection.upsert(ids=ids, embeddings=embeddings, documents=texts, metadatas=metadatas)

    logger.info("Indexados %d chunks de %s → coleção '%s'", len(chunks), path.name, col_name)
    return IngestionResult(
        path=path,
        doc_type=doc_type,
        sha256=doc.sha256,
        chunks_indexed=len(chunks),
        skipped=False,
    )


def ingest_directory(
    dir_path: Path,
    doc_type: DocType,
    chroma_client: chromadb.ClientAPI,
    settings: Settings,
    embed_fn: EmbedFn | None = None,
) -> list[IngestionResult]:
    """Ingere todos os PDFs de um diretório no ChromaDB.

    Parameters
    ----------
    dir_path : Path
        Diretório com arquivos ``.pdf``.
    doc_type : DocType
        Tipo aplicado a todos os documentos do diretório.
    chroma_client : chromadb.ClientAPI
        Cliente ChromaDB.
    settings : Settings
        Configurações do sistema.
    embed_fn : EmbedFn | None
        Função de embedding. Se None, usa LM Studio via ``build_embed_fn``.

    Returns
    -------
    list[IngestionResult]
        Um resultado por arquivo PDF encontrado.

    Raises
    ------
    NotADirectoryError
        Se ``dir_path`` não existir ou não for um diretório.
    """
    dir_path = Path(dir_path)
    if not dir_path.is_dir():
        raise NotADirectoryError(f"Diretório não encontrado: {dir_path}")

    if embed_fn is None:
        embed_fn = build_embed_fn(settings)

    pdf_files = sorted(dir_path.glob("*.pdf"))
    if not pdf_files:
        logger.warning("Nenhum PDF encontrado em %s", dir_path)
        return []

    results: list[IngestionResult] = []
    for pdf_path in pdf_files:
        result = ingest_document(pdf_path, doc_type, chroma_client, settings, embed_fn)
        results.append(result)

    processed = sum(1 for r in results if not r.skipped and not r.error and r.chunks_indexed > 0)
    skipped = sum(1 for r in results if r.skipped)
    errors = sum(1 for r in results if r.error)
    logger.info(
        "Diretório '%s': %d processados, %d pulados, %d erros",
        dir_path.name,
        processed,
        skipped,
        errors,
    )
    return results
