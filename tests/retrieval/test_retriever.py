from __future__ import annotations

import logging
from unittest.mock import MagicMock

import chromadb
import pytest
from langchain_chroma import Chroma
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
        texts=[
            "Diretriz de tratamento da hipertensão: reduzir sal e atividade física."
        ],
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
# Testes — select_collections (subset compartilhado com o run_query)
# ---------------------------------------------------------------------------


def _mock_stores() -> dict[DocType, MagicMock]:
    return {
        dt: MagicMock(name=f"store_{dt.value}")
        for dt in (DocType.BULA, DocType.DIRETRIZ, DocType.PROTOCOLO)
    }


def test_select_collections_doc_types_filters_and_ignores_missing():
    """select_collections com doc_types retorna só os presentes e ignora ausentes."""
    from medasist.retrieval.retriever import select_collections

    stores = _mock_stores()
    subset = select_collections(stores, [DocType.BULA, DocType.MANUAL])

    assert set(subset) == {DocType.BULA}
    assert subset[DocType.BULA] is stores[DocType.BULA]


def test_select_collections_none_returns_all_stores():
    """select_collections com None retorna o próprio dicionário de stores."""
    from medasist.retrieval.retriever import select_collections

    stores = _mock_stores()
    subset = select_collections(stores, None)

    assert subset is stores
    assert set(subset) == set(stores)


def test_select_collections_empty_list_returns_all_stores():
    """select_collections com lista vazia equivale a None (todas as stores)."""
    from medasist.retrieval.retriever import select_collections

    stores = _mock_stores()
    subset = select_collections(stores, [])

    assert subset is stores


def test_select_collections_does_not_mutate_original():
    """select_collections nunca muta o dicionário original de stores."""
    from medasist.retrieval.retriever import select_collections

    stores = _mock_stores()
    original = dict(stores)

    select_collections(stores, [DocType.BULA])
    select_collections(stores, None)

    assert set(stores) == set(original)


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


def test_retrieve_subset_only_returns_subset_doctypes(stores_with_docs, settings):
    """Retrieval com stores subset recupera apenas dos DocTypes do subset."""
    from medasist.retrieval.retriever import retrieve

    settings_loose = Settings(
        retrieval_top_k=10,
        retrieval_score_threshold=10.0,
    )

    subset = {DocType.BULA: stores_with_docs[DocType.BULA]}
    docs = retrieve("hipertensão tratamento", subset, settings_loose)

    assert isinstance(docs, list)
    assert len(docs) > 0
    doc_types_found = {d.metadata.get("doc_type") for d in docs if d.metadata}
    assert doc_types_found == {"bula"}


def test_retrieve_respects_top_k(client, embeddings, settings):
    """retrieve retorna no máximo retrieval_top_k documentos."""
    from medasist.retrieval.retriever import retrieve

    store = get_vectorstore(DocType.MANUAL, client, embeddings, settings)
    texts = [
        f"Manual seção {i}: conteúdo médico sintético número {i}." for i in range(20)
    ]
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


# ---------------------------------------------------------------------------
# Testes — log de métrica consolidada por query
# ---------------------------------------------------------------------------


def _retrieve_record(caplog) -> logging.LogRecord | None:
    """Retorna o registro consolidado de retrieval (prefixo ``retrieve:``)."""
    return next(
        (r for r in caplog.records if r.getMessage().startswith("retrieve:")),
        None,
    )


def test_retrieve_logs_consolidated_metric(caplog, stores_with_docs):
    """retrieve loga record consolidado com chunks, scores, latency_ms e cold_start."""
    from medasist.retrieval.retriever import retrieve

    settings_loose = Settings(
        retrieval_top_k=10,
        retrieval_score_threshold=10.0,
    )

    with caplog.at_level(logging.INFO, logger="medasist.retrieval.retriever"):
        docs = retrieve("hipertensão", stores_with_docs, settings_loose)

    record = _retrieve_record(caplog)
    assert record is not None
    message = record.getMessage()
    assert f"chunks={len(docs)}" in message
    assert "latency_ms=" in message
    assert "cold_start=False" in message
    assert "doc_types=['bula', 'diretriz']" in message


def test_retrieve_logs_scores_parallel_to_returned_docs(caplog, stores_with_docs):
    """scores no log é paralelo aos documentos retornados (mesma contagem)."""
    from medasist.retrieval.retriever import retrieve

    settings_loose = Settings(
        retrieval_top_k=10,
        retrieval_score_threshold=10.0,
    )

    with caplog.at_level(logging.INFO, logger="medasist.retrieval.retriever"):
        docs = retrieve("hipertensão", stores_with_docs, settings_loose)

    record = _retrieve_record(caplog)
    assert record is not None
    scores = list(record.args[3])
    assert len(scores) == len(docs)


def test_retrieve_cold_start_logs_metric(caplog, empty_stores, settings):
    """Cold start: record consolidado com cold_start=true, chunks=0 e scores vazio."""
    from medasist.retrieval.retriever import retrieve

    with caplog.at_level(logging.INFO, logger="medasist.retrieval.retriever"):
        docs = retrieve("qualquer consulta médica", empty_stores, settings)

    assert docs == []
    record = _retrieve_record(caplog)
    assert record is not None
    message = record.getMessage()
    assert "cold_start=True" in message
    assert "chunks=0" in message
    assert "scores=[]" in message


def test_retrieve_continues_when_one_store_fails_and_logs_failed_store(
    caplog, client, embeddings, settings
):
    """Store que falha não interrompe o fluxo e é listada em failed_stores.

    Monta um store saudável (BULA com docs) e um store mock que levanta
    ``RuntimeError`` em ``similarity_search_with_score``. ``retrieve`` deve
    retornar os docs do store saudável e o record consolidado deve conter
    ``failed_stores`` com o nome do store que falhou, além de ``latency_ms``
    e ``chunks`` correspondente aos docs retornados.
    """
    from medasist.retrieval.retriever import retrieve

    settings_loose = Settings(
        retrieval_top_k=10,
        retrieval_score_threshold=10.0,
    )

    store_bula = get_vectorstore(DocType.BULA, client, embeddings, settings_loose)
    store_bula.add_texts(
        texts=["Alphazol X: indicado para hipertensão arterial sistêmica."],
        metadatas=[{"doc_type": "bula", "source": "alphazol.pdf", "page": 1}],
        ids=["bula_001"],
    )

    failing_store = MagicMock(spec=Chroma)
    failing_store.similarity_search_with_score.side_effect = RuntimeError("falha")

    stores = {
        DocType.BULA: store_bula,
        DocType.PROTOCOLO: failing_store,
    }

    with caplog.at_level(logging.INFO, logger="medasist.retrieval.retriever"):
        docs = retrieve("hipertensão", stores, settings_loose)

    assert len(docs) > 0
    assert all(isinstance(d, Document) for d in docs)
    assert all(d.metadata.get("doc_type") == "bula" for d in docs)

    record = _retrieve_record(caplog)
    assert record is not None
    message = record.getMessage()
    assert "failed_stores=['protocolo']" in message
    assert f"chunks={len(docs)}" in message
    assert "latency_ms=" in message


def test_retrieve_all_stores_fail_logs_error_and_returns_empty(caplog, settings):
    """Todos os stores falhando: retorna vazio e loga erro com failed_stores.

    Quando nenhum store responde, ``retrieve`` não pode devolver contexto e
    trata como cold start: retorna lista vazia, loga ``logger.error`` com o
    nome dos stores que falharam e o record consolidado com ``failed_stores``,
    ``latency_ms`` e ``chunks=0``.
    """
    from medasist.retrieval.retriever import retrieve

    failing_store = MagicMock(spec=Chroma)
    failing_store.similarity_search_with_score.side_effect = RuntimeError("falha")

    stores = {DocType.MANUAL: failing_store}

    with caplog.at_level(logging.INFO, logger="medasist.retrieval.retriever"):
        docs = retrieve("qualquer consulta", stores, settings)

    assert docs == []
    assert any(
        r.levelno == logging.ERROR
        and "Nenhum resultado: falha de infra" in r.getMessage()
        for r in caplog.records
    )
    record = _retrieve_record(caplog)
    assert record is not None
    message = record.getMessage()
    assert "failed_stores=['manual']" in message
    assert "chunks=0" in message
    assert "latency_ms=" in message
