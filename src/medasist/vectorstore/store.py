from __future__ import annotations

import logging
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


def get_client(settings: Settings) -> chromadb.PersistentClient:
    """Retorna singleton do PersistentClient ChromaDB.

    Parameters
    ----------
    settings : Settings
        Configurações com ``chroma_dir``.

    Returns
    -------
    chromadb.PersistentClient
        Cliente singleton para o processo.
    """
    global _client
    if _client is None:
        path = Path(settings.chroma_dir)
        path.mkdir(parents=True, exist_ok=True)
        _client = chromadb.PersistentClient(path=str(path))
        logger.info("ChromaDB PersistentClient inicializado em %s", path)
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
    )


def _collection_name(doc_type: DocType, settings: Settings) -> str:
    return getattr(settings, _COLLECTION_ATTR[doc_type])


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
    """
    name = _collection_name(doc_type, settings)
    store = Chroma(
        client=client,
        collection_name=name,
        embedding_function=embeddings,
    )
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
