from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import chromadb
import pytest
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from pydantic import SecretStr

from medasist.config import Settings
from medasist.generation.citations import build_citations
from medasist.ingestion.schemas import DocType
from medasist.retrieval.retriever import _lexical_relevance_guard, _rrf_fuse
from medasist.retrieval.sparse import (
    get_sparse_index,
    reset_sparse_indexes,
    tokenize,
)
from medasist.vectorstore.store import get_vectorstore


def _settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = {
        "retrieval_top_k": 10,
        "retrieval_score_threshold": 0.4,
        "retrieval_hybrid_enabled": True,
        "retrieval_hybrid_rrf_k": 60,
        "retrieval_hybrid_sparse_top_k": 20,
    }
    defaults.update(overrides)
    return Settings(
        admin_api_key=SecretStr("very-strong-key-0123456789"),
        **defaults,
    )


class _FakeEmbeddings(Embeddings):
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[float(i % 10) * 0.1 + 0.1] * 4 for i, _ in enumerate(texts)]

    def embed_query(self, text: str) -> list[float]:
        return [0.1, 0.1, 0.1, 0.1]


@pytest.fixture(autouse=True)
def _reset_sparse_indexes():
    """Zera o cache global do índice esparso antes e depois de cada teste."""
    reset_sparse_indexes()
    yield
    reset_sparse_indexes()


@pytest.fixture
def client(tmp_path) -> chromadb.ClientAPI:
    return chromadb.PersistentClient(path=str(tmp_path / "chroma"))


# ---------------------------------------------------------------------------
# T3 — tokenizador esparso
# ---------------------------------------------------------------------------


def test_tokenize_normalizes_accents_both_directions():
    """Acentos normalizados em ambos os sentidos: Dipironá == dipirona."""
    settings = _settings()
    assert tokenize("Dipironá", settings) == ["dipirona"]
    assert tokenize("dipirona", settings) == ["dipirona"]


def test_tokenize_keeps_500mg_intact():
    """'500mg' permanece token íntegro e casa com 'Amoxicilina 500mg'."""
    settings = _settings()
    assert tokenize("amoxicilina 500mg", settings) == ["amoxicilina", "500mg"]


def test_tokenize_normalizes_digit_unit_space():
    """'10 mg' e '10mg' normalizam para o mesmo token '10mg'."""
    settings = _settings()
    assert tokenize("10 mg", settings) == ["10mg"]
    assert tokenize("10mg", settings) == ["10mg"]
    assert tokenize("10 g", settings) == ["10g"]


def test_tokenize_uses_sparse_stopwords_not_dosage_units():
    """mg/ml/g/kg NÃO são removidos (lista esparsa não usa retrieval_stopwords)."""
    settings = _settings()
    tokens = tokenize("500mg ml g kg para", settings)
    assert "500mg" in tokens
    assert "ml" in tokens
    assert "g" in tokens
    assert "kg" in tokens
    assert "para" not in tokens  # stopword esparsa removida


def test_tokenize_stopword_only_query_returns_empty():
    """Query composta só de stopwords esparsas → zero tokens."""
    settings = _settings()
    assert tokenize("qual a dose de", settings) == []


def test_tokenize_accents_in_stopwords_are_stripped():
    """Stopword acentuada é normalizada ao comparar com token sem acento."""
    settings = _settings(retrieval_sparse_stopwords=("máxima", "dose"))
    assert tokenize("dose maxima", settings) == []


# ---------------------------------------------------------------------------
# T6 — fusão RRF (helper puro)
# ---------------------------------------------------------------------------


def test_rrf_math_rank1_dense_rank3_sparse():
    """Chunk no rank 1 denso + rank 3 esparso com k=60 pontua 1/61 + 1/63."""
    dense = [(Document(page_content="X"), 0.1)]
    sparse = [
        (Document(page_content="A"), 1.0),
        (Document(page_content="B"), 1.0),
        (Document(page_content="X"), 1.0),
    ]
    result = _rrf_fuse(dense, sparse, 60)
    score_x = next(score for doc, score in result if doc.page_content == "X")
    assert score_x == pytest.approx(1.0 / 61 + 1.0 / 63)


def test_rrf_dedup_same_page_content_appears_once():
    """Mesmo page_content nos dois caminhos aparece uma única vez."""
    dense = [(Document(page_content="X"), 0.1)]
    sparse = [(Document(page_content="X"), 1.0)]
    result = _rrf_fuse(dense, sparse, 60)
    contents = [doc.page_content for doc, _ in result]
    assert contents.count("X") == 1


def test_rrf_deterministic_tie_break_dense_before_sparse():
    """Empate de score RRF → candidato denso precede esparso (estável)."""
    dense = [(Document(page_content="A"), 0.1)]
    sparse = [(Document(page_content="B"), 1.0)]
    result = _rrf_fuse(dense, sparse, 60)
    # Ambos com score 1/61; A veio do denso → primeiro
    assert [doc.page_content for doc, _ in result] == ["A", "B"]


def test_rrf_empty_lists_returns_empty():
    """Listas vazias de entrada → resultado vazio, sem erro."""
    assert _rrf_fuse([], [], 60) == []


def test_rrf_sorts_by_score_desc():
    """Lista final ordenada por score RRF decrescente."""
    dense = [(Document(page_content="A"), 0.1), (Document(page_content="B"), 0.2)]
    # A rank1 = 1/61; B rank2 = 1/62 → A > B
    result = _rrf_fuse(dense, [], 60)
    assert [doc.page_content for doc, _ in result] == ["A", "B"]


# ---------------------------------------------------------------------------
# T4 — índice BM25 lazy por DocType (ChromaDB real)
# ---------------------------------------------------------------------------


def _bula_store(client, settings, texts, metadatas=None, ids=None):
    """Cria store BULA populada com textos e metadados dados."""
    store = get_vectorstore(DocType.BULA, client, _FakeEmbeddings(), settings)
    store.add_texts(
        texts=texts,
        metadatas=metadatas or [{}] * len(texts),
        ids=ids or [f"id_{i}" for i in range(len(texts))],
    )
    return store


def test_get_sparse_index_built_once_and_reused(client):
    """Índice construído uma única vez por coleção e reutilizado."""
    settings = _settings()
    store = _bula_store(
        client,
        settings,
        ["Bula de dipirona para dor."],
        metadatas=[{"doc_type": "bula", "source_path": "bula_dipirona.pdf", "page": 1}],
    )

    first = get_sparse_index(store, settings)
    second = get_sparse_index(store, settings)

    assert first is second
    assert first is not None


def test_get_sparse_index_thread_safe_single_build(client, mocker):
    """Construção concorrente (threads) produz um único índice — sem race."""
    settings = _settings()
    store = _bula_store(
        client,
        settings,
        ["Bula de dipirona para dor."],
        metadatas=[{"doc_type": "bula", "source_path": "bula_dipirona.pdf", "page": 1}],
    )

    original_build = get_sparse_index.__globals__["SparseIndex"].build
    calls = 0
    lock = threading.Lock()

    def counting_build(store_arg, settings_arg):
        nonlocal calls
        with lock:
            calls += 1
        return original_build(store_arg, settings_arg)

    mocker.patch(
        "medasist.retrieval.sparse.SparseIndex.build",
        side_effect=counting_build,
    )

    n = 8
    barrier = threading.Barrier(n)

    def worker():
        barrier.wait()
        return get_sparse_index(store, settings)

    with ThreadPoolExecutor(max_workers=n) as ex:
        results = list(ex.map(lambda _: worker(), range(n)))

    assert calls == 1
    assert all(r is results[0] for r in results)


def test_get_sparse_index_stale_refresh_after_ingest(client):
    """Chunks ingeridos após a construção ficam visíveis na query seguinte."""
    settings = _settings()
    store = _bula_store(
        client,
        settings,
        ["Bula de dipirona para dor."],
        metadatas=[{"doc_type": "bula", "source_path": "bula_dipirona.pdf", "page": 1}],
        ids=["id_0"],
    )

    index = get_sparse_index(store, settings)
    assert index is not None
    assert [d.page_content for d, _ in index.search("ibuprofeno", 5)] == []

    # ingest de novo chunk na mesma coleção
    store.add_texts(
        texts=["Bula de ibuprofeno para febre."],
        metadatas=[
            {"doc_type": "bula", "source_path": "bula_ibuprofeno.pdf", "page": 2}
        ],
        ids=["id_1"],
    )

    refreshed = get_sparse_index(store, settings)
    hits = [d.page_content for d, _ in refreshed.search("ibuprofeno", 5)]
    assert "Bula de ibuprofeno para febre." in hits


def test_get_sparse_index_empty_collection_returns_empty(client):
    """Coleção vazia → índice vazio, sem erro; busca retorna []."""
    settings = _settings()
    store = get_vectorstore(DocType.BULA, client, _FakeEmbeddings(), settings)

    index = get_sparse_index(store, settings)

    assert index is not None
    assert index.search("dipirona", 5) == []


def test_get_sparse_index_build_failure_returns_none_and_logs(client, mocker, caplog):
    """Falha na construção (collection.get) → None + log, sem propagar."""
    import logging

    settings = _settings()
    store = _bula_store(
        client,
        settings,
        ["Bula de dipirona para dor."],
        metadatas=[{"doc_type": "bula", "source_path": "bula_dipirona.pdf", "page": 1}],
    )

    mocker.patch.object(
        store._collection, "get", side_effect=RuntimeError("falha de infra")
    )

    with caplog.at_level(logging.ERROR, logger="medasist.retrieval.sparse"):
        result = get_sparse_index(store, settings)

    assert result is None
    assert any("índice esparso" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# T5 — busca esparsa com reconstrução de metadados
# ---------------------------------------------------------------------------


def test_search_reconstructs_document_with_faithful_metadata(client):
    """Document reconstruído tem page_content e metadados idênticos ao armazenado."""
    settings = _settings()
    store = _bula_store(
        client,
        settings,
        ["Amoxicilina 500mg: dose habitual de 1 cápsula a cada 8 horas."],
        metadatas=[
            {
                "doc_type": "bula",
                "source_path": "bula_amoxicilina.pdf",
                "sha256": "abc123",
                "chunk_index": 2,
                "char_count": 50,
                "page": 3,
                "section": "Posologia",
            }
        ],
        ids=["id_0"],
    )

    index = get_sparse_index(store, settings)
    assert index is not None
    hits = index.search("amoxicilina 500mg", 5)

    assert len(hits) == 1
    doc, _score = hits[0]
    assert doc.page_content == (
        "Amoxicilina 500mg: dose habitual de 1 cápsula a cada 8 horas."
    )
    assert doc.metadata["doc_type"] == "bula"
    assert doc.metadata["source_path"] == "bula_amoxicilina.pdf"
    assert doc.metadata["sha256"] == "abc123"
    assert doc.metadata["chunk_index"] == 2
    assert doc.metadata["char_count"] == 50
    assert doc.metadata["page"] == 3
    assert doc.metadata["section"] == "Posologia"


def test_search_orders_by_score_desc(client):
    """Busca devolve pares (Document, score) ordenados por score desc."""
    settings = _settings()
    store = _bula_store(
        client,
        settings,
        ["Bula de dipirona para dor intensa.", "Bula de dipirona e dor em adultos."],
        metadatas=[{"doc_type": "bula"} for _ in range(2)],
        ids=["id_0", "id_1"],
    )

    index = get_sparse_index(store, settings)
    assert index is not None
    hits = index.search("dipirona dor", 5)

    assert len(hits) == 2
    assert all(d.page_content.startswith("Bula de dipirona") for d, _ in hits)
    assert hits[0][1] >= hits[1][1]


def test_search_top_k_limits_results(client):
    """top_k limita o número de candidatos esparsos retornados."""
    settings = _settings()
    texts = [f"dipirona seção {i}: dor e febre." for i in range(10)]
    store = _bula_store(client, settings, texts, ids=[f"id_{i}" for i in range(10)])

    index = get_sparse_index(store, settings)
    assert index is not None
    assert len(index.search("dipirona dor", 3)) == 3


def test_search_build_citations_on_sparse_document(client):
    """build_citations produz CitationItem correto para chunk esparso."""
    from medasist.generation.citations import CitationItem

    settings = _settings()
    store = _bula_store(
        client,
        settings,
        ["Amoxicilina 500mg: dose habitual de 1 cápsula."],
        metadatas=[
            {
                "doc_type": "bula",
                "source_path": "bula_amoxicilina.pdf",
                "sha256": "abc",
                "chunk_index": 0,
                "char_count": 40,
                "page": 3,
                "section": "Posologia",
            }
        ],
        ids=["id_0"],
    )

    index = get_sparse_index(store, settings)
    assert index is not None
    doc = index.search("amoxicilina", 1)[0][0]

    citations = build_citations([doc])
    assert len(citations) == 1
    item: CitationItem = citations[0]
    assert item.index == 1
    assert item.source == "bula_amoxicilina.pdf"
    assert item.section == "Posologia"
    assert item.page == "3"


def test_search_lexical_relevance_guard_on_sparse_document(client):
    """_lexical_relevance_guard funciona sobre chunk esparso (lê source_path)."""
    settings = _settings()
    store = _bula_store(
        client,
        settings,
        ["Amoxicilina 500mg: dose habitual de 1 cápsula a cada 8 horas."],
        metadatas=[{"doc_type": "bula", "source_path": "bula_amoxicilina.pdf"}],
        ids=["id_0"],
    )

    index = get_sparse_index(store, settings)
    assert index is not None
    doc = index.search("amoxicilina 500mg", 1)[0][0]

    # guarda aprova: chunk contém o medicamento da consulta
    guarded = _lexical_relevance_guard(
        "Qual a dose de amoxicilina?", [(doc, 1.0)], settings
    )
    assert len(guarded) == 1

    # guarda bloqueia: consulta menciona outro fármaco não presente no chunk
    blocked = _lexical_relevance_guard(
        "Qual a dose de dipirona?", [(doc, 1.0)], settings
    )
    assert blocked == []
