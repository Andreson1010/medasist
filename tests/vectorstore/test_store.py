from __future__ import annotations

import chromadb
import pytest
from langchain_core.embeddings import Embeddings

from medasist.config import Settings
from medasist.ingestion.schemas import DocType
from medasist.vectorstore.store import get_all_vectorstores, get_vectorstore


# ---------------------------------------------------------------------------
# Helpers de teste
# ---------------------------------------------------------------------------


class _FakeEmbeddings(Embeddings):
    """Embeddings fake de 4 dimensões — sem chamada ao LM Studio."""

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.2, 0.3, 0.4] for _ in texts]

    def embed_query(self, text: str) -> list[float]:
        return [0.1, 0.2, 0.3, 0.4]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def settings() -> Settings:
    return Settings()


@pytest.fixture
def client(tmp_path) -> chromadb.ClientAPI:
    """PersistentClient em diretório temporário — isolado por teste."""
    return chromadb.PersistentClient(path=str(tmp_path / "chroma"))


@pytest.fixture
def embeddings() -> _FakeEmbeddings:
    return _FakeEmbeddings()


# ---------------------------------------------------------------------------
# get_vectorstore
# ---------------------------------------------------------------------------


def test_get_vectorstore_creates_collection_in_client(client, embeddings, settings):
    """get_vectorstore cria a coleção correta no cliente ChromaDB."""
    get_vectorstore(DocType.BULA, client, embeddings, settings)

    col = client.get_collection(settings.collection_bulas)
    assert col is not None


def test_get_vectorstore_uses_correct_name_per_doc_type(client, embeddings, settings):
    """Cada DocType gera uma coleção com o nome configurado em Settings."""
    expected = {
        DocType.BULA: settings.collection_bulas,
        DocType.DIRETRIZ: settings.collection_diretrizes,
        DocType.PROTOCOLO: settings.collection_protocolos,
        DocType.MANUAL: settings.collection_manuais,
    }
    for doc_type, name in expected.items():
        get_vectorstore(doc_type, client, embeddings, settings)
        col = client.get_collection(name)
        assert col is not None, f"Coleção '{name}' não encontrada para {doc_type}"


def test_get_vectorstore_is_queryable_after_add(client, embeddings, settings):
    """Vectorstore aceita add_texts e retorna resultados via similarity_search."""
    store = get_vectorstore(DocType.PROTOCOLO, client, embeddings, settings)
    store.add_texts(
        texts=["Protocolo sintético de triagem para urgência nível I."],
        metadatas=[{"source": "teste"}],
        ids=["proto_001"],
    )

    results = store.similarity_search("triagem urgência", k=1)

    assert len(results) == 1
    assert "triagem" in results[0].page_content.lower()


def test_get_vectorstore_collections_are_isolated(client, embeddings, settings):
    """Documentos indexados em BULA não aparecem em consultas de DIRETRIZ."""
    bula = get_vectorstore(DocType.BULA, client, embeddings, settings)
    get_vectorstore(DocType.DIRETRIZ, client, embeddings, settings)

    bula.add_texts(["Bula do medicamento Alphazol X."], ids=["bula_001"])

    col_diretriz = client.get_collection(settings.collection_diretrizes)
    assert col_diretriz.count() == 0


def test_get_vectorstore_idempotent_open(client, embeddings, settings):
    """Chamar get_vectorstore duas vezes para o mesmo DocType não duplica a coleção."""
    get_vectorstore(DocType.MANUAL, client, embeddings, settings)
    get_vectorstore(DocType.MANUAL, client, embeddings, settings)

    cols = [c.name for c in client.list_collections()]
    assert cols.count(settings.collection_manuais) == 1


# ---------------------------------------------------------------------------
# get_all_vectorstores
# ---------------------------------------------------------------------------


def test_get_all_vectorstores_returns_all_doc_types(client, embeddings, settings):
    """get_all_vectorstores retorna uma entrada para cada DocType."""
    stores = get_all_vectorstores(client, embeddings, settings)

    assert set(stores.keys()) == set(DocType)


def test_get_all_vectorstores_creates_all_collections(client, embeddings, settings):
    """Todas as 4 coleções ChromaDB são criadas."""
    get_all_vectorstores(client, embeddings, settings)

    col_names = {c.name for c in client.list_collections()}
    assert settings.collection_bulas in col_names
    assert settings.collection_diretrizes in col_names
    assert settings.collection_protocolos in col_names
    assert settings.collection_manuais in col_names


def test_get_all_vectorstores_each_store_is_queryable(client, embeddings, settings):
    """Cada store retornado aceita similarity_search."""
    stores = get_all_vectorstores(client, embeddings, settings)

    for doc_type, store in stores.items():
        store.add_texts(
            texts=[f"Texto sintético para {doc_type.value}."],
            ids=[f"{doc_type.value}_001"],
        )
        results = store.similarity_search("texto sintético", k=1)
        assert len(results) == 1, f"Falha ao consultar store de {doc_type}"
