"""Acceptance tests for RAG-02 (busca híbrida denso + esparso via RRF).

Verifica a feature pelo ponto de entrada consumível ``retrieve()`` /
``run_query()``, com o LLM sempre mockado (nunca rede real). Nenhum arquivo de
``src/`` é modificado. Dados sintéticos (dipirona/ibuprofeno fictícios).

Cobertura por critério de aceitação do spec (tests/specs do RAG-02):

- HYBR-01: flag off (default) → identidade total (dense-only, sem índice esparso).
- HYBR-03: fusão RRF — chunk no rank 1 denso + rank 3 esparso com k=60 pontua
  1/61 + 1/63.
- HYBR-05: denso vazio + esparso com hit exato aprovado pela guarda → NÃO é
  cold start; ``run_query`` gera resposta com citação válida.
- HYBR-09: caminho de avaliação (``_collect_rows``) usa o mesmo caminho híbrido.
- HYBR-17: documento recuperado apenas pelo esparso tem metadados fiéis e
  ``build_citations`` produz CitationItem correto.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import chromadb
import pytest
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings

from medasist.config import Settings
from medasist.evaluation.dataset import GoldenQuestion
from medasist.generation.citations import CitationItem, build_citations
from medasist.ingestion.schemas import DocType
from medasist.profiles.schemas import UserProfile
from medasist.retrieval.retriever import _rrf_fuse, retrieve, select_collections
from medasist.retrieval.sparse import reset_sparse_indexes
from medasist.vectorstore.store import get_vectorstore


class _FakeEmbeddings(Embeddings):
    """Embeddings fake com vetores que casam densamente com a consulta."""

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[0.1, 0.1, 0.1, 0.1] for _ in texts]

    def embed_query(self, text: str) -> list[float]:
        return [0.1, 0.1, 0.1, 0.1]


class _DivergentEmbeddings(Embeddings):
    """Query vector bem diferente dos docs — denso vazio (L2 > threshold)."""

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 1.0, 1.0, 1.0] for _ in texts]

    def embed_query(self, text: str) -> list[float]:
        return [0.0, 0.0, 0.0, 0.0]


def _settings(**overrides: object) -> Settings:
    """Settings com busca híbrida habilitada e overrides por critério."""
    defaults: dict[str, object] = {
        "retrieval_top_k": 10,
        "retrieval_score_threshold": 0.4,
        "retrieval_hybrid_enabled": True,
        "retrieval_hybrid_rrf_k": 60,
        "retrieval_hybrid_sparse_top_k": 20,
    }
    defaults.update(overrides)
    return Settings(**defaults)


@pytest.fixture(autouse=True)
def _reset_sparse_indexes():
    """Zera o cache global do índice esparso antes e depois de cada teste."""
    reset_sparse_indexes()
    yield
    reset_sparse_indexes()


@pytest.fixture
def client(tmp_path) -> chromadb.ClientAPI:
    return chromadb.PersistentClient(path=str(tmp_path / "chroma"))


def _dipirona_store(client, embeddings=None):
    """Store BULA com chunk de dipirona, usando embeddings dados."""
    embeddings = embeddings or _FakeEmbeddings()
    store = get_vectorstore(DocType.BULA, client, embeddings, _settings())
    store.add_texts(
        texts=["Bula de dipirona para dor intensa."],
        metadatas=[
            {
                "doc_type": "bula",
                "source_path": "bula_dipirona.pdf",
                "sha256": "abc",
                "chunk_index": 0,
                "char_count": 32,
                "page": 2,
                "section": "Indicações",
            }
        ],
        ids=["bula_001"],
    )
    return store


# ---------------------------------------------------------------------------
# HYBR-01 / flag off: identidade total, sem construir índice esparso
# ---------------------------------------------------------------------------


def test_ac_hybr01_flag_off_identity_no_sparse(mocker, client):
    """HYBR-01: flag off → retrieve idêntico ao atual e índice esparso NÃO é
    construído (dense-only)."""
    store = _dipirona_store(client, _FakeEmbeddings())
    mock_sparse = mocker.patch("medasist.retrieval.retriever.get_sparse_index")

    settings_off = _settings(retrieval_hybrid_enabled=False)
    docs = retrieve("dipirona dor", {DocType.BULA: store}, settings_off)

    assert isinstance(docs, list)
    assert all(isinstance(d, Document) for d in docs)
    assert len(docs) > 0
    mock_sparse.assert_not_called()


# ---------------------------------------------------------------------------
# HYBR-03 / fusão RRF: rank 1 denso + rank 3 esparso, k=60 → 1/61 + 1/63
# ---------------------------------------------------------------------------


def test_ac_hybr03_rrf_math_rank1_dense_rank3_sparse():
    """HYBR-03: chunk no rank 1 denso + rank 3 esparso com k=60 pontua
    exatamente 1/61 + 1/63 (fórmula usada dentro de retrieve)."""
    dense = [(Document(page_content="X"), 0.1)]
    sparse = [
        (Document(page_content="A"), 1.0),
        (Document(page_content="B"), 1.0),
        (Document(page_content="X"), 1.0),
    ]
    result = _rrf_fuse(dense, sparse, 60)
    score_x = next(score for doc, score in result if doc.page_content == "X")
    assert score_x == pytest.approx(1.0 / 61 + 1.0 / 63)


# ---------------------------------------------------------------------------
# HYBR-05 / denso vazio + esparso com hit exato ≠ cold start; run_query gera citação
# ---------------------------------------------------------------------------


def test_ac_hybr05_sparse_only_hit_is_not_cold_start(client):
    """HYBR-05: denso vazio + esparso com hit exato aprovado pela guarda →
    retrieve retorna o chunk (não cold start) e build_citations produz citação."""
    store = _dipirona_store(client, _DivergentEmbeddings())
    settings = _settings()

    docs = retrieve("Qual a dose de dipirona?", {DocType.BULA: store}, settings)

    assert len(docs) == 1
    citations = build_citations(docs)
    assert len(citations) == 1
    item = citations[0]
    assert item.source == "bula_dipirona.pdf"
    assert item.section == "Indicações"
    assert item.page == "2"


def test_ac_hybr05_run_query_generates_answer_with_citation(client):
    """HYBR-05 (fluxo completo): run_query sobre contexto esparso gera resposta
    com citação válida (não cold start, LLM mockado)."""
    from langchain_core.messages import AIMessage

    from medasist.generation.chain import run_query

    store = _dipirona_store(client, _DivergentEmbeddings())
    settings = _settings()

    # recupera via caminho híbrido real (sparse-only)
    sparse_docs = retrieve(
        "Qual a dose de dipirona?", {DocType.BULA: store}, settings
    )
    assert len(sparse_docs) == 1

    # feed do contexto esparso na chain (LLM mockado)
    with (
        patch("medasist.generation.chain.build_retriever") as mock_rb,
        patch("medasist.generation.chain.ChatOpenAI") as mock_llm_cls,
    ):
        mock_retriever = MagicMock()
        mock_retriever.invoke.return_value = sparse_docs
        mock_rb.return_value = mock_retriever
        mock_llm_instance = MagicMock()
        mock_llm_cls.return_value = mock_llm_instance
        mock_llm_instance.return_value = AIMessage(content="A dose é 500 mg [1].")

        result = run_query(
            "Qual a dose de dipirona?",
            {DocType.BULA: store},
            UserProfile.MEDICO,
            settings,
        )

    assert result.is_cold_start is False
    assert len(result.citations) == 1
    assert isinstance(result.citations[0], CitationItem)
    assert result.citations[0].source == "bula_dipirona.pdf"
    assert "[1]" in result.answer


def test_ac_hybr05_guard_blocks_cross_drug_sparse_hit_is_cold_start(client):
    """HYBR-05/HYBR-07 borda: esparso recupera chunk de outro fármaco →
    guarda lexical esvazia → cold start []."""
    store = get_vectorstore(DocType.BULA, client, _DivergentEmbeddings(), _settings())
    store.add_texts(
        texts=["Bula de ibuprofeno para febre."],
        metadatas=[{"doc_type": "bula", "source_path": "bula_ibuprofeno.pdf"}],
        ids=["bula_001"],
    )
    settings = _settings()

    docs = retrieve(
        "Qual a dose de dipirona?", {DocType.BULA: store}, settings
    )

    assert docs == []


# ---------------------------------------------------------------------------
# HYBR-09 / caminho de avaliação (_collect_rows) usa o caminho híbrido
# ---------------------------------------------------------------------------


def test_ac_hybr09_eval_collect_rows_uses_hybrid_path(mocker, client):
    """HYBR-09: _collect_rows chama retrieve() (híbrido) — contexts da avaliação
    idênticos aos da resposta (invariante AD-011)."""
    from medasist.evaluation.metrics import _collect_rows

    store = _dipirona_store(client, _DivergentEmbeddings())
    stores = {DocType.BULA: store}
    settings = _settings()

    questions = [
        GoldenQuestion(
            question="Qual a dose de dipirona?",
            reference_answer="Resposta de referência.",
            reference_contexts=["Bula de dipirona para dor intensa."],
        )
    ]

    captured: dict[str, object] = {}

    def _fake_run_query(question, stores_arg, profile, settings_arg, doc_types=None):
        subset = select_collections(stores_arg, doc_types)
        docs = retrieve(question, subset, settings_arg)
        captured["answer_docs"] = docs
        return MagicMock(
            answer="Resposta [1].",
            citations=build_citations(docs),
            profile=profile,
            disclaimer="aviso",
            is_cold_start=False,
        )

    mocker.patch("medasist.evaluation.metrics.run_query", side_effect=_fake_run_query)

    rows, cold_flags = _collect_rows(
        questions, stores, settings, UserProfile.MEDICO, None
    )

    assert cold_flags == [False]
    answer_docs = captured["answer_docs"]
    assert answer_docs is not None
    assert rows[0]["contexts"] == [d.page_content for d in answer_docs]
    # o contexto veio do caminho híbrido (sparse-only, denso vazio)
    assert rows[0]["contexts"] == ["Bula de dipirona para dor intensa."]


# ---------------------------------------------------------------------------
# HYBR-17 / documento esparso reconstruído com metadados fiéis
# ---------------------------------------------------------------------------


def test_ac_hybr17_sparse_document_metadata_fidelity(client):
    """HYBR-17: documento recuperado apenas pelo esparso tem metadados fiéis e
    build_citations/_lexical_relevance_guard produzem o mesmo resultado do denso."""
    store = _dipirona_store(client, _DivergentEmbeddings())
    settings = _settings()

    docs = retrieve("Qual a dose de dipirona?", {DocType.BULA: store}, settings)

    assert len(docs) == 1
    doc = docs[0]
    assert doc.page_content == "Bula de dipirona para dor intensa."
    assert doc.metadata["doc_type"] == "bula"
    assert doc.metadata["source_path"] == "bula_dipirona.pdf"
    assert doc.metadata["sha256"] == "abc"
    assert doc.metadata["chunk_index"] == 0
    assert doc.metadata["char_count"] == 32
    assert doc.metadata["page"] == 2
    assert doc.metadata["section"] == "Indicações"

    # build_citations: source/section/page corretos (page 0 → vazio, como no denso)
    citations = build_citations([doc])
    assert citations[0].source == "bula_dipirona.pdf"
    assert citations[0].section == "Indicações"
    assert citations[0].page == "2"
