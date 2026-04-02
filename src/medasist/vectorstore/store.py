from __future__ import annotations

import logging
import threading
from pathlib import Path

import chromadb
from langchain_chroma import Chroma
from langchain_core.embeddings import Embeddings
from langchain_openai import OpenAIEmbeddings

from medasist.config import Settings
from medasist.ingestion.schemas import DocType

logger = logging.getLogger(__name__)

_COLLECTION_ATTR: dict[DocType, str] = {
    DocType.BULA: "collection_bulas",
    DocType.DIRETRIZ: "collection_diretrizes",
    DocType.PROTOCOLO: "collection_protocolos",
    DocType.MANUAL: "collection_manuais",
}

_client: chromadb.PersistentClient | None = None
_client_path: Path | None = None
_client_lock = threading.Lock()


def get_client(settings: Settings) -> chromadb.PersistentClient:
    """Retorna singleton do PersistentClient ChromaDB.

    Usa double-checked locking para segurança em contextos multi-thread.
    Emite aviso se chamado com ``chroma_dir`` diferente do inicializado.

    Parameters
    ----------
    settings : Settings
        Configurações com ``chroma_dir``.

    Returns
    -------
    chromadb.PersistentClient
        Cliente singleton para o processo.
    """
    global _client, _client_path

    if _client is None:
        with _client_lock:
            if _client is None:
                path = Path(settings.chroma_dir)
                path.mkdir(parents=True, exist_ok=True)
                _client = chromadb.PersistentClient(path=str(path))
                _client_path = path
                logger.info("ChromaDB PersistentClient inicializado em %s", path)
    else:
        requested = Path(settings.chroma_dir)
        if requested != _client_path:
            logger.warning(
                "get_client chamado com chroma_dir='%s', mas singleton já "
                "inicializado em '%s'. Retornando cliente existente.",
                requested,
                _client_path,
            )

    return _client


def build_embeddings(settings: Settings) -> OpenAIEmbeddings:
    """Constrói OpenAIEmbeddings apontando para o LM Studio local.

    Parameters
    ----------
    settings : Settings
        Configurações com URL e modelo de embedding.

    Returns
    -------
    OpenAIEmbeddings
        Instância pronta para uso pelo LangChain.
    """
    return OpenAIEmbeddings(
        base_url=settings.lm_studio_base_url,
        api_key=settings.lm_studio_api_key.get_secret_value(),
        model=settings.lm_studio_embedding_model,
        check_embedding_ctx_length=False,
    )


def _collection_name(doc_type: DocType, settings: Settings) -> str:
    attr = _COLLECTION_ATTR.get(doc_type)
    if attr is None:
        raise ValueError(f"DocType sem coleção mapeada: {doc_type!r}")
    return getattr(settings, attr)


def get_vectorstore(
    doc_type: DocType,
    client: chromadb.ClientAPI,
    embeddings: Embeddings,
    settings: Settings,
) -> Chroma:
    """Abre ou cria o vectorstore LangChain para um DocType.

    Parameters
    ----------
    doc_type : DocType
        Tipo do documento — determina qual coleção usar.
    client : chromadb.ClientAPI
        Cliente ChromaDB (PersistentClient em prod, EphemeralClient em testes).
    embeddings : Embeddings
        Função de embedding compatível com LangChain.
    settings : Settings
        Configurações com nomes de coleções.

    Returns
    -------
    Chroma
        Vectorstore LangChain pronto para add_texts / similarity_search.

    Raises
    ------
    RuntimeError
        Se o ChromaDB não conseguir criar ou abrir a coleção.
    """
    name = _collection_name(doc_type, settings)
    try:
        store = Chroma(
            client=client,
            collection_name=name,
            embedding_function=embeddings,
        )
    except Exception as exc:
        raise RuntimeError(
            f"Falha ao abrir vectorstore '{name}' para {doc_type.value}: {exc}"
        ) from exc
    logger.debug("Vectorstore '%s' pronto (%s)", name, doc_type.value)
    return store


def get_all_vectorstores(
    client: chromadb.ClientAPI,
    embeddings: Embeddings,
    settings: Settings,
) -> dict[DocType, Chroma]:
    """Abre ou cria vectorstores para todos os DocTypes.

    Usado no lifespan do FastAPI para aquecer todas as coleções no startup.

    Parameters
    ----------
    client : chromadb.ClientAPI
        Cliente ChromaDB.
    embeddings : Embeddings
        Função de embedding compatível com LangChain.
    settings : Settings
        Configurações com nomes de coleções.

    Returns
    -------
    dict[DocType, Chroma]
        Mapeamento DocType → vectorstore.
    """
    stores = {dt: get_vectorstore(dt, client, embeddings, settings) for dt in DocType}
    logger.info("Vectorstores inicializados: %s", [dt.value for dt in stores])
    return stores
