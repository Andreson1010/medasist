from __future__ import annotations

import chromadb
import pytest
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.retrievers import BaseRetriever

from medasist.config import Settings
from medasist.ingestion.schemas import DocType
from medasist.vectorstore.store import get_vectorstore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeEmbeddings(Embeddings):
    """Embeddings fake com vetores distintos para que a busca funcione."""

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[float(i % 10) * 0.1 + 0.1] * 4 for i, _ in enumerate(texts)]

    def embed_query(self, text: str) -> list[float]:
        return [0.1, 0.1, 0.1, 0.1]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def settings() -> Settings:
    return Settings(retrieval_top_k=10, retrieval_score_threshold=0.4)


@pytest.fixture
def client(tmp_path) -> chromadb.ClientAPI:
    """PersistentClient isolado por teste — sem estado compartilhado."""
    return chromadb.PersistentClient(path=str(tmp_path / "chroma"))


@pytest.fixture
def embeddings() -> _FakeEmbeddings:
    return _FakeEmbeddings()


@pytest.fixture
def stores_with_docs(client, embeddings, settings):
    """Stores com documentos indexados para testar retrieval."""
    store_bula = get_vectorstore(DocType.BULA, client, embeddings, settings)
    store_bula.add_texts(
        texts=["Alphazol X: indicado para hipertensão arterial sistêmica."],
        metadatas=[{"doc_type": "bula", "source": "alphazol.pdf", "page": 1}],
        ids=["bula_001"],
    )
    store_diretriz = get_vectorstore(DocType.DIRETRIZ, client, embeddings, settings)
    store_diretriz.add_texts(
        texts=["Diretriz de tratamento da hipertensão: reduzir sal e atividade física."],
        metadatas=[{"doc_type": "diretriz", "source": "htn_guideline.pdf", "page": 3}],
        ids=["dir_001"],
    )
    return {
        DocType.BULA: store_bula,
        DocType.DIRETRIZ: store_diretriz,
    }


@pytest.fixture
def empty_stores(client, embeddings, settings):
    """Stores sem documentos — simula cold start."""
    store_bula = get_vectorstore(DocType.BULA, client, embeddings, settings)
    store_protocolo = get_vectorstore(DocType.PROTOCOLO, client, embeddings, settings)
    return {
        DocType.BULA: store_bula,
        DocType.PROTOCOLO: store_protocolo,
    }


# ---------------------------------------------------------------------------
# Testes — build_retriever
# ---------------------------------------------------------------------------


def test_build_retriever_returns_base_retriever(stores_with_docs, settings):
    """build_retriever retorna objeto com método invoke (BaseRetriever)."""
    from medasist.retrieval.retriever import build_retriever

    retriever = build_retriever(stores_with_docs, settings)

    assert isinstance(retriever, BaseRetriever)
    assert hasattr(retriever, "invoke")


def test_build_retriever_single_doctype(client, embeddings, settings):
    """build_retriever funciona com um único DocType no dicionário."""
    from medasist.retrieval.retriever import build_retriever

    store = get_vectorstore(DocType.PROTOCOLO, client, embeddings, settings)
    store.add_texts(
        texts=["Protocolo de triagem Manchester nível urgência."],
        ids=["proto_001"],
    )
    retriever = build_retriever({DocType.PROTOCOLO: store}, settings)

    assert isinstance(retriever, BaseRetriever)
    results = retriever.invoke("triagem Manchester")
    assert isinstance(results, list)


# ---------------------------------------------------------------------------
# Testes — retrieve
# ---------------------------------------------------------------------------


def test_retrieve_returns_documents_above_threshold(stores_with_docs, settings):
    """retrieve retorna documentos quando há resultados acima do threshold."""
    from medasist.retrieval.retriever import retrieve

    # threshold alto para aceitar os docs sintéticos
    settings_loose = Settings(
        retrieval_top_k=10,
        retrieval_score_threshold=10.0,
    )

    docs = retrieve("hipertensão", stores_with_docs, settings_loose)

    assert isinstance(docs, list)
    assert len(docs) > 0
    assert all(isinstance(d, Document) for d in docs)


def test_retrieve_cold_start_returns_empty(empty_stores, settings):
    """Cold start: stores sem documentos → lista vazia (regra de segurança médica)."""
    from medasist.retrieval.retriever import retrieve

    docs = retrieve("qualquer consulta médica", empty_stores, settings)

    assert docs == []


def test_retrieve_multi_doctype_searches_all_collections(stores_with_docs, settings):
    """retrieve com múltiplos DocTypes busca em todas as coleções."""
    from medasist.retrieval.retriever import retrieve

    settings_loose = Settings(
        retrieval_top_k=10,
        retrieval_score_threshold=10.0,
    )

    docs = retrieve("hipertensão tratamento", stores_with_docs, settings_loose)

    doc_types_found = {d.metadata.get("doc_type") for d in docs if d.metadata}
    # Deve ter encontrado docs de ambos os stores
    assert doc_types_found == {"bula", "diretriz"}


def test_retrieve_respects_top_k(client, embeddings, settings):
    """retrieve retorna no máximo retrieval_top_k documentos."""
    from medasist.retrieval.retriever import retrieve

    store = get_vectorstore(DocType.MANUAL, client, embeddings, settings)
    texts = [f"Manual seção {i}: conteúdo médico sintético número {i}." for i in range(20)]
    ids = [f"manual_{i:03d}" for i in range(20)]
    store.add_texts(texts=texts, ids=ids)

    settings_k3 = Settings(
        retrieval_top_k=3,
        retrieval_score_threshold=10.0,
    )

    docs = retrieve("conteúdo médico", {DocType.MANUAL: store}, settings_k3)

    assert len(docs) <= settings_k3.retrieval_top_k


def test_retrieve_returns_no_duplicates(client, embeddings, settings):
    """retrieve não retorna documentos duplicados quando o mesmo ID existe."""
    from medasist.retrieval.retriever import retrieve

    store = get_vectorstore(DocType.BULA, client, embeddings, settings)
    store.add_texts(
        texts=["Bula do Betazol: contraindicado em gestantes."],
        metadatas=[{"source": "betazol.pdf"}],
        ids=["bula_unique_001"],
    )

    settings_loose = Settings(
        retrieval_top_k=10,
        retrieval_score_threshold=10.0,
    )
    docs = retrieve("Betazol gestante", {DocType.BULA: store}, settings_loose)

    page_contents = [d.page_content for d in docs]
    assert len(page_contents) == len(set(page_contents))


def test_retrieve_empty_stores_dict_returns_empty(settings):
    """retrieve com dicionário vazio retorna lista vazia imediatamente."""
    from medasist.retrieval.retriever import retrieve

    docs = retrieve("qualquer query", {}, settings)

    assert docs == []


def test_retrieve_with_strict_threshold_filters_all(client, settings):
    """Threshold abaixo da distância real filtra todos os documentos (cold start).

    Usa embeddings com query vector diferente dos documents para garantir
    distância L2 > 0, permitindo testar o filtro de threshold.
    """
    from medasist.retrieval.retriever import retrieve

    class _DivergentEmbeddings(Embeddings):
        """Query vector bem diferente dos docs — distância L2 alta."""

        def embed_documents(self, texts: list[str]) -> list[list[float]]:
            return [[1.0, 1.0, 1.0, 1.0] for _ in texts]

        def embed_query(self, text: str) -> list[float]:
            # vetor ortogonal aos docs: distância L2 = sqrt(4) = 2.0
            return [0.0, 0.0, 0.0, 0.0]

    divergent = _DivergentEmbeddings()
    store = get_vectorstore(DocType.BULA, client, divergent, settings)
    store.add_texts(
        texts=["Gammacol: uso pediátrico exclusivo."],
        ids=["bula_strict_001"],
    )

    # threshold=1.0 < distância real (~2.0) → todos filtrados
    settings_strict = Settings(
        retrieval_top_k=10,
        retrieval_score_threshold=1.0,
    )

    docs = retrieve("Gammacol pediátrico", {DocType.BULA: store}, settings_strict)

    assert docs == []
