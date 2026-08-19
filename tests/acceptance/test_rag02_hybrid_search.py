"""Acceptance tests for RAG-02 (busca híbrida denso + esparso via RRF).

Verifica a feature pelos pontos de entrada consumíveis ``retrieve()``,
``run_query()``, ``build_citations()`` e ``_collect_rows()``, com o LLM sempre
mockado (nunca rede real) e o ChromaDB real em ``tmp_path``. Nenhum arquivo de
``src/`` é modificado. Dados sintéticos (dipirona/ibuprofeno/Amoxicilina/Alphazol/
Zolatril fictícios).

Cobertura por critério de aceitação do spec RAG-02 (HYBR-01..HYBR-24):

- HYBR-01: flag off (default) → identidade (mesma ordem L2) e índice esparso NÃO
  construído.
- HYBR-02: contrato ``list[Document]``/``GenerationResult`` mantido com híbrido ativo.
- HYBR-03: fusão RRF — chunk no rank 1 denso + rank 3 esparso com k=60 pontua
  1/61 + 1/63 (observado via scores do log).
- HYBR-04: ordem do funil: guarda → rerank → corte em ``retrieval_top_k``.
- HYBR-05: denso vazio + esparso com hit exato aprovado pela guarda → NÃO é cold
  start; ``run_query`` gera resposta com citação válida.
- HYBR-06: denso vazio E esparso vazio → ``[]`` e ``run_query`` devolve
  ``cold_start_message`` sem chamar o LLM.
- HYBR-07: guarda lexical esvazia contexto esparso de outro fármaco → ``[]``.
- HYBR-08: mesmo ``page_content`` nos dois caminhos → chunk único com RRF somado.
- HYBR-09: caminho de avaliação (``_collect_rows``) usa o caminho híbrido.
- HYBR-10: ``select_collections`` limita candidatos esparsos ao DocType.
- HYBR-11: empate de score RRF → ordem determinística (denso precede esparso).
- HYBR-12: índice construído uma única vez por DocType e reutilizado
  (sequencial + concorrência).
- HYBR-13: falha esparsa → dense-only, erro logado, contexto denso preservado.
- HYBR-14: normalização de acentos (query com acento ↔ corpus sem, e inverso).
- HYBR-15: dosagem "500mg" íntegra e "10 mg"/"10mg" → mesmo token.
- HYBR-16: ``retrieval_sparse_stopwords`` própria (mg/ml/g/kg preservados).
- HYBR-17: documento esparso reconstruído com metadados fiéis.
- HYBR-18: chunk ingerido após o índice construído fica visível na query seguinte.
- HYBR-19: biblioteca BM25 declarada em requirements.txt e requirements-api.txt.
- HYBR-20: coleção vazia → zero candidatos esparsos → cold start ``[]``.
- HYBR-21: defaults de ``Settings`` + documentação em ``.env.example``.
- HYBR-22: override por env refletido em ``Settings``.
- HYBR-23: valores inválidos (rrf_k=0, sparse_top_k=-1) → erro de validação.
- HYBR-24: log mantém campos atuais + aditivos ``n_dense_candidates``/
  ``n_sparse_candidates``; ``scores`` é o score final RRF.
"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import MagicMock, patch

import chromadb
import pytest
from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from pydantic import SecretStr, ValidationError

from medasist.config import Settings
from medasist.evaluation.dataset import GoldenQuestion
from medasist.generation.citations import CitationItem, build_citations
from medasist.ingestion.schemas import DocType
from medasist.profiles.schemas import UserProfile
from medasist.retrieval.retriever import build_retriever, retrieve, select_collections
from medasist.retrieval.sparse import reset_sparse_indexes, tokenize
from medasist.vectorstore.store import get_vectorstore

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ENV_EXAMPLE = _REPO_ROOT / ".env.example"

_ADMIN_KEY = "very-strong-key-0123456789"


class _FakeEmbeddings(Embeddings):
    """Embeddings fake que casam densamente com a consulta (L2 <= threshold)."""

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


class _RankedFakeEmbeddings(Embeddings):
    """Embeddings com distância L2 distinta por chunk (para ordem determinística)."""

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[float(i % 10) * 0.1 + 0.1] * 4 for i, _ in enumerate(texts)]

    def embed_query(self, text: str) -> list[float]:
        return [0.1, 0.1, 0.1, 0.1]


class _FakeSparseIndex:
    """Índice esparso fake com hits controlados (para controlar ranks do RRF)."""

    def __init__(self, hits: list[tuple[Document, float]]) -> None:
        self._hits = hits

    def search(self, query: str, top_k: int) -> list[tuple[Document, float]]:
        return self._hits


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
    return Settings(admin_api_key=SecretStr(_ADMIN_KEY), **defaults)


@pytest.fixture(autouse=True)
def _reset_sparse_indexes():
    """Zera o cache global do índice esparso antes e depois de cada teste."""
    reset_sparse_indexes()
    yield
    reset_sparse_indexes()


@pytest.fixture
def client(tmp_path) -> chromadb.ClientAPI:
    return chromadb.PersistentClient(path=str(tmp_path / "chroma"))


def _bula_store(
    client: chromadb.ClientAPI,
    embeddings: Embeddings | None = None,
    settings: Settings | None = None,
) -> Chroma:
    """Store BULA com um chunk de dipirona, usando embeddings dados."""
    embeddings = embeddings or _FakeEmbeddings()
    settings = settings or _settings()
    store = get_vectorstore(DocType.BULA, client, embeddings, settings)
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


def _retrieve_record(caplog) -> logging.LogRecord | None:
    """Retorna o registro consolidado de retrieval (prefixo ``retrieve:``)."""
    return next(
        (r for r in caplog.records if r.getMessage().startswith("retrieve:")),
        None,
    )


# ---------------------------------------------------------------------------
# HYBR-01 / flag off: identidade total, sem construir índice esparso
# ---------------------------------------------------------------------------


def test_ac_hybr01_flag_off_identity_no_sparse(mocker, client):
    """HYBR-01: flag off → retrieve idêntico ao atual (mesma ordem L2) e índice
    esparso NÃO é construído (dense-only)."""
    settings_off = _settings(retrieval_hybrid_enabled=False)
    store = get_vectorstore(DocType.BULA, client, _RankedFakeEmbeddings(), settings_off)
    store.add_texts(
        texts=["Bula de Alphazol para hipertensão.", "Bula de Betazol para febre."],
        metadatas=[
            {"doc_type": "bula", "source_path": "alphazol.pdf"},
            {"doc_type": "bula", "source_path": "betazol.pdf"},
        ],
        ids=["bula_001", "bula_002"],
    )
    mock_sparse = mocker.patch("medasist.retrieval.retriever.get_sparse_index")

    settings_loose = _settings(
        retrieval_hybrid_enabled=False, retrieval_score_threshold=10.0
    )
    docs = retrieve("hipertensão", {DocType.BULA: store}, settings_loose)

    assert isinstance(docs, list)
    assert all(isinstance(d, Document) for d in docs)
    # identidade: mesma ordem L2 do caminho denso (chunk0 L2=0 antes de chunk1 L2=0.2)
    assert [d.page_content for d in docs] == [
        "Bula de Alphazol para hipertensão.",
        "Bula de Betazol para febre.",
    ]
    mock_sparse.assert_not_called()


# ---------------------------------------------------------------------------
# HYBR-02 / contrato list[Document] + GenerationResult inalterado
# ---------------------------------------------------------------------------


def test_ac_hybr02_contract_list_document_and_generationresult_unchanged(
    mocker, client
):
    """HYBR-02: com híbrido ativo, retrieve/build_retriever/select_collections
    mantêm o contrato ``list[Document]`` e run_query mantém o ``GenerationResult``
    consumido pela API (LLM mockado)."""
    from langchain_core.messages import AIMessage
    from langchain_core.retrievers import BaseRetriever

    from medasist.generation.chain import GenerationResult, run_query

    store = _bula_store(client, _DivergentEmbeddings())
    settings = _settings()
    stores = {DocType.BULA: store}

    # retrieve: list[Document] (hit esparso, denso vazio)
    docs = retrieve("Qual a dose de dipirona?", stores, settings)
    assert isinstance(docs, list)
    assert all(isinstance(d, Document) for d in docs)

    # select_collections: subconjunto dict
    subset = select_collections(stores, [DocType.BULA])
    assert subset == stores

    # build_retriever: BaseRetriever com invoke → list[Document]
    retriever = build_retriever(subset, settings)
    assert isinstance(retriever, BaseRetriever)
    invoked = retriever.invoke("Qual a dose de dipirona?")
    assert isinstance(invoked, list)
    assert all(isinstance(d, Document) for d in invoked)

    # run_query: GenerationResult com o shape consumido pela API (LLM mockado)
    with patch("medasist.generation.chain.ChatOpenAI") as mock_llm_cls:
        mock_llm_instance = MagicMock()
        mock_llm_cls.return_value = mock_llm_instance
        mock_llm_instance.return_value = AIMessage(content="A dose é 500 mg [1].")
        result = run_query(
            "Qual a dose de dipirona?", stores, UserProfile.MEDICO, settings
        )

    assert isinstance(result, GenerationResult)
    assert result.answer and "[1]" in result.answer
    assert result.profile == UserProfile.MEDICO
    assert result.disclaimer == settings.disclaimer
    assert isinstance(result.citations, list)
    assert hasattr(result, "is_cold_start")


# ---------------------------------------------------------------------------
# HYBR-03 / fusão RRF: rank 1 denso + rank 3 esparso, k=60 → 1/61 + 1/63
# ---------------------------------------------------------------------------


def test_ac_hybr03_rrf_math_via_retrieve_scores(caplog, mocker, client):
    """HYBR-03: chunk no rank 1 denso + rank 3 esparso com k=60 pontua
    exatamente 1/61 + 1/63 e a lista final é ordenada por score RRF decrescente
    (scores observados no record consolidado de retrieve)."""
    store = _bula_store(client, _FakeEmbeddings())
    settings = _settings()
    hits = [
        (
            Document(
                page_content="Bula de dipirona adulta e febre.",
                metadata={"doc_type": "bula", "source_path": "a.pdf"},
            ),
            2.0,
        ),
        (
            Document(
                page_content="Bula de dipirona gotas.",
                metadata={"doc_type": "bula", "source_path": "b.pdf"},
            ),
            2.0,
        ),
        (
            Document(
                page_content="Bula de dipirona para dor intensa.",
                metadata={"doc_type": "bula", "source_path": "bula_dipirona.pdf"},
            ),
            2.0,
        ),
    ]
    mocker.patch(
        "medasist.retrieval.retriever.get_sparse_index",
        return_value=_FakeSparseIndex(hits),
    )

    with caplog.at_level(logging.INFO, logger="medasist.retrieval.retriever"):
        docs = retrieve("Qual a dose de dipirona?", {DocType.BULA: store}, settings)

    # X no rank 1 denso + rank 3 esparso → 1/61 + 1/63; A rank 1 esparso → 1/61;
    # B rank 2 esparso → 1/62. Ordem por RRF desc: X, A, B.
    assert [d.page_content for d in docs] == [
        "Bula de dipirona para dor intensa.",
        "Bula de dipirona adulta e febre.",
        "Bula de dipirona gotas.",
    ]
    record = _retrieve_record(caplog)
    assert record is not None
    scores = list(record.args[3])
    assert scores[0] == pytest.approx(1.0 / 61 + 1.0 / 63)
    assert scores[1] == pytest.approx(1.0 / 61)
    assert scores[2] == pytest.approx(1.0 / 62)
    assert scores == sorted(scores, reverse=True)


# ---------------------------------------------------------------------------
# HYBR-04 / ordem do funil: guarda → rerank → corte em retrieval_top_k
# ---------------------------------------------------------------------------


def test_ac_hybr04_pipeline_rerank_after_guard_and_topk_cut(mocker, client):
    """HYBR-04: com híbrido + rerank ativos, o rerank roda DEPOIS da guarda sobre
    a lista fundida e o corte final respeita ``retrieval_top_k``."""
    settings = _settings(retrieval_rerank_enabled=True)
    store = get_vectorstore(DocType.BULA, client, _DivergentEmbeddings(), settings)
    store.add_texts(
        texts=["Bula de dipirona para dor intensa.", "Bula de dipirona gotas."],
        metadatas=[
            {"doc_type": "bula", "source_path": "bula_dipirona.pdf"},
            {"doc_type": "bula", "source_path": "bula_dipirona_gotas.pdf"},
        ],
        ids=["bula_001", "bula_002"],
    )
    mock_rerank = mocker.patch(
        "medasist.retrieval.retriever.rerank_documents",
        side_effect=lambda docs, query, settings: list(reversed(docs)),
    )
    stores = {DocType.BULA: store}

    docs = retrieve("Qual a dose de dipirona dor?", stores, settings)

    assert len(docs) == 2
    assert mock_rerank.call_count == 1
    fused_input = mock_rerank.call_args.args[0]
    assert len(fused_input) == 2
    # rerank recebeu a lista fundida pós-guarda (pares Document, score RRF)
    assert all(isinstance(d, Document) for d, _ in fused_input)
    # rerank rodou sobre a lista fundida → ordem reversa aplicada no resultado
    fused_contents = [d.page_content for d, _ in fused_input]
    assert [d.page_content for d in docs] == list(reversed(fused_contents))

    # corte final em retrieval_top_k
    settings_k1 = _settings(retrieval_rerank_enabled=True, retrieval_top_k=1)
    docs_k1 = retrieve("Qual a dose de dipirona dor?", stores, settings_k1)
    assert len(docs_k1) == 1


# ---------------------------------------------------------------------------
# HYBR-05 / denso vazio + esparso com hit exato ≠ cold start; run_query gera citação
# ---------------------------------------------------------------------------


def test_ac_hybr05_sparse_only_hit_is_not_cold_start(client):
    """HYBR-05: denso vazio + esparso com hit exato aprovado pela guarda →
    retrieve retorna o chunk (não cold start) e build_citations produz citação."""
    store = _bula_store(client, _DivergentEmbeddings())
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

    store = _bula_store(client, _DivergentEmbeddings())
    settings = _settings()

    # recupera via caminho híbrido real (sparse-only)
    sparse_docs = retrieve("Qual a dose de dipirona?", {DocType.BULA: store}, settings)
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


# ---------------------------------------------------------------------------
# HYBR-06 / denso vazio E esparso vazio → cold start, sem chamar o LLM
# ---------------------------------------------------------------------------


def test_ac_hybr06_both_empty_returns_cold_start_message_without_llm(mocker, client):
    """HYBR-06: denso vazio E esparso vazio → retrieve [] e run_query devolve
    ``cold_start_message`` SEM chamar o LLM."""
    from medasist.generation.chain import run_query

    settings = _settings()
    store = get_vectorstore(DocType.BULA, client, _DivergentEmbeddings(), settings)
    stores = {DocType.BULA: store}

    # retrieve → [] (denso vazio + esparso vazio)
    assert retrieve("Qual a dose de dipirona?", stores, settings) == []

    # run_query → cold_start_message, LLM não chamado
    mock_llm = mocker.patch("medasist.generation.chain.ChatOpenAI")
    result = run_query("Qual a dose de dipirona?", stores, UserProfile.MEDICO, settings)

    assert result.is_cold_start is True
    assert result.citations == []
    assert result.answer == settings.cold_start_message
    mock_llm.assert_not_called()


# ---------------------------------------------------------------------------
# HYBR-07 / guarda lexical esvazia contexto esparso de outro fármaco → []
# ---------------------------------------------------------------------------


def test_ac_hybr07_guard_blocks_cross_drug_sparse_hit_is_cold_start(client):
    """HYBR-07: o esparso recupera chunk de outro fármaco (via token comum
    "febre"), mas a guarda lexical esvazia o contexto porque a consulta menciona
    dipirona e o chunk não a contém → cold start []. Sem contornar a guarda."""
    store = get_vectorstore(DocType.BULA, client, _DivergentEmbeddings(), _settings())
    store.add_texts(
        texts=["Bula de ibuprofeno para febre."],
        metadatas=[{"doc_type": "bula", "source_path": "bula_ibuprofeno.pdf"}],
        ids=["bula_001"],
    )
    settings = _settings()

    # token "febre" faz o esparso recuperar o chunk de ibuprofeno; a guarda bloqueia
    docs = retrieve(
        "Qual a dose de dipirona para febre?", {DocType.BULA: store}, settings
    )

    assert docs == []


# ---------------------------------------------------------------------------
# HYBR-08 / mesmo page_content nos dois caminhos → chunk único com RRF somado
# ---------------------------------------------------------------------------


def test_ac_hybr08_same_chunk_both_paths_dedup_and_summed_rrf(caplog, mocker, client):
    """HYBR-08: o mesmo ``page_content`` vem do denso (rank d) e do esparso
    (rank s) → retornado uma única vez com score RRF = 1/(k+d) + 1/(k+s)."""
    store = _bula_store(client, _FakeEmbeddings())
    settings = _settings()
    same_doc = Document(
        page_content="Bula de dipirona para dor intensa.",
        metadata={"doc_type": "bula", "source_path": "bula_dipirona.pdf"},
    )
    mocker.patch(
        "medasist.retrieval.retriever.get_sparse_index",
        return_value=_FakeSparseIndex([(same_doc, 2.0)]),
    )

    with caplog.at_level(logging.INFO, logger="medasist.retrieval.retriever"):
        docs = retrieve("Qual a dose de dipirona?", {DocType.BULA: store}, settings)

    # uma única ocorrência do chunk (dedup cross-path)
    assert len(docs) == 1
    assert docs[0].page_content == "Bula de dipirona para dor intensa."
    record = _retrieve_record(caplog)
    assert record is not None
    scores = list(record.args[3])
    # rank 1 denso (1/61) + rank 1 esparso (1/61) → 2/61
    assert scores[0] == pytest.approx(1.0 / 61 + 1.0 / 61)


# ---------------------------------------------------------------------------
# HYBR-09 / caminho de avaliação (_collect_rows) usa o caminho híbrido
# ---------------------------------------------------------------------------


def test_ac_hybr09_eval_collect_rows_uses_hybrid_path(mocker, client):
    """HYBR-09: _collect_rows chama retrieve() (híbrido) — contexts da avaliação
    idênticos aos da resposta (invariante AD-011)."""
    from medasist.evaluation.metrics import _collect_rows

    store = _bula_store(client, _DivergentEmbeddings())
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
# HYBR-10 / select_collections limita candidatos esparsos ao DocType
# ---------------------------------------------------------------------------


def test_ac_hybr10_select_collections_limits_sparse_to_doctype(client):
    """HYBR-10: doc_types=[BULA] via select_collections limita os candidatos
    esparsos à coleção de bulas (mesmo isolamento per-DocType do denso)."""
    settings = _settings()
    store_bula = get_vectorstore(DocType.BULA, client, _DivergentEmbeddings(), settings)
    store_bula.add_texts(
        texts=["Bula de dipirona para dor intensa."],
        metadatas=[{"doc_type": "bula", "source_path": "bula_dipirona.pdf"}],
        ids=["bula_001"],
    )
    store_manual = get_vectorstore(
        DocType.MANUAL, client, _DivergentEmbeddings(), settings
    )
    store_manual.add_texts(
        texts=["Manual de dipirona para referência."],
        metadatas=[{"doc_type": "manual", "source_path": "manual_dipirona.pdf"}],
        ids=["manual_001"],
    )
    stores = {DocType.BULA: store_bula, DocType.MANUAL: store_manual}

    # sem filtro: candidatos esparsos das duas coleções
    docs_all = retrieve("dipirona dor", stores, settings)
    assert {d.metadata["doc_type"] for d in docs_all} == {"bula", "manual"}

    # doc_types=[BULA]: apenas a coleção de bulas
    subset = select_collections(stores, [DocType.BULA])
    docs_bula = retrieve("dipirona dor", subset, settings)
    assert docs_bula
    assert all(d.metadata["doc_type"] == "bula" for d in docs_bula)


# ---------------------------------------------------------------------------
# HYBR-11 / empate de score RRF → ordem determinística (denso precede esparso)
# ---------------------------------------------------------------------------


def test_ac_hybr11_rrf_tie_dense_precedes_sparse(mocker, client):
    """HYBR-11: dois chunks com score RRF idêntico → ordem determinística
    (denso precede esparso em empate, ordenação estável)."""
    store = _bula_store(client, _FakeEmbeddings())  # denso X rank 1 → 1/61
    settings = _settings()
    y = Document(
        page_content="Bula de dipirona gotas.",
        metadata={"doc_type": "bula", "source_path": "bula_dipirona_gotas.pdf"},
    )
    # esparso Y rank 1 → 1/61 (empate com X)
    mocker.patch(
        "medasist.retrieval.retriever.get_sparse_index",
        return_value=_FakeSparseIndex([(y, 2.0)]),
    )

    docs = retrieve("Qual a dose de dipirona?", {DocType.BULA: store}, settings)

    # X (denso) precede Y (esparso) no empate
    assert [d.page_content for d in docs] == [
        "Bula de dipirona para dor intensa.",
        "Bula de dipirona gotas.",
    ]


# ---------------------------------------------------------------------------
# HYBR-12 / índice construído uma única vez por DocType e reutilizado
# ---------------------------------------------------------------------------


def test_ac_hybr12_index_built_once_per_doctype_and_reused(mocker, client):
    """HYBR-12: com híbrido ativo, o índice BM25 é construído uma única vez por
    DocType a partir da coleção ChromaDB e reutilizado nas queries seguintes."""
    from medasist.retrieval.sparse import SparseIndex

    store = _bula_store(client, _DivergentEmbeddings())
    settings = _settings()
    original_build = SparseIndex.build
    calls = 0

    def counting_build(store_arg, settings_arg):
        nonlocal calls
        calls += 1
        return original_build(store_arg, settings_arg)

    mocker.patch(
        "medasist.retrieval.sparse.SparseIndex.build", side_effect=counting_build
    )

    stores = {DocType.BULA: store}
    docs1 = retrieve("Qual a dose de dipirona?", stores, settings)
    docs2 = retrieve("Qual a dose de dipirona?", stores, settings)
    assert docs1 and docs2
    assert calls == 1  # construído uma única vez, reutilizado

    # segundo DocType → nova construção (uma por coleção)
    store_manual = get_vectorstore(
        DocType.MANUAL, client, _DivergentEmbeddings(), settings
    )
    store_manual.add_texts(
        texts=["Manual de dipirona para referência."],
        metadatas=[{"doc_type": "manual", "source_path": "manual_dipirona.pdf"}],
        ids=["manual_001"],
    )
    stores_two = {DocType.BULA: store, DocType.MANUAL: store_manual}
    docs3 = retrieve("Qual a dose de dipirona?", stores_two, settings)
    assert docs3
    assert calls == 2  # uma construção por DocType
    retrieve("Qual a dose de dipirona?", stores_two, settings)
    assert calls == 2  # reutilizado (nenhuma reconstrução)


def test_ac_hybr12_concurrent_queries_build_index_once(mocker, client):
    """HYBR-12 (concorrência): múltiplas queries simultâneas constroem o índice
    uma única vez (double-checked locking) e todas retornam o mesmo resultado."""
    from medasist.retrieval.sparse import SparseIndex

    store = _bula_store(client, _DivergentEmbeddings())
    settings = _settings()
    original_build = SparseIndex.build
    calls = 0
    lock = threading.Lock()

    def counting_build(store_arg, settings_arg):
        nonlocal calls
        with lock:
            calls += 1
        return original_build(store_arg, settings_arg)

    mocker.patch(
        "medasist.retrieval.sparse.SparseIndex.build", side_effect=counting_build
    )

    stores = {DocType.BULA: store}
    n = 8
    barrier = threading.Barrier(n)

    def worker():
        barrier.wait()
        return retrieve("Qual a dose de dipirona?", stores, settings)

    with ThreadPoolExecutor(max_workers=n) as ex:
        results = list(ex.map(lambda _: worker(), range(n)))

    assert calls == 1
    assert all(len(r) == 1 for r in results)
    assert all(
        r[0].page_content == "Bula de dipirona para dor intensa." for r in results
    )


# ---------------------------------------------------------------------------
# HYBR-13 / falha esparsa → dense-only, erro logado, contexto denso preservado
# ---------------------------------------------------------------------------


def test_ac_hybr13_sparse_search_failure_degrades_to_dense_only_and_logs(
    mocker, client, caplog
):
    """HYBR-13: falha na consulta esparsa (query time) → dense-only preservado,
    erro logado, exceção nunca propagada e contexto denso válido mantido."""
    store = _bula_store(client, _FakeEmbeddings())  # denso com hit
    settings = _settings()
    failing_index = MagicMock()
    failing_index.search.side_effect = RuntimeError("falha na busca esparsa")
    mocker.patch(
        "medasist.retrieval.retriever.get_sparse_index", return_value=failing_index
    )

    with caplog.at_level(logging.ERROR, logger="medasist.retrieval.retriever"):
        docs = retrieve("Qual a dose de dipirona?", {DocType.BULA: store}, settings)

    assert len(docs) > 0  # contexto denso preservado (não esvaziado)
    assert all(isinstance(d, Document) for d in docs)
    assert any("Erro na busca esparsa" in r.getMessage() for r in caplog.records)


def test_ac_hybr13_sparse_build_failure_degrades_to_dense_only(mocker, client):
    """HYBR-13: falha na construção do índice esparso (get_sparse_index → None)
    → dense-only preservado, sem propagar exceção."""
    store = _bula_store(client, _FakeEmbeddings())
    settings = _settings()
    mocker.patch("medasist.retrieval.retriever.get_sparse_index", return_value=None)

    docs = retrieve("Qual a dose de dipirona?", {DocType.BULA: store}, settings)

    assert len(docs) > 0
    assert all(isinstance(d, Document) for d in docs)


# ---------------------------------------------------------------------------
# HYBR-14 / normalização de acentos (ambos os sentidos)
# ---------------------------------------------------------------------------


def test_ac_hybr14_accented_query_matches_unaccented_corpus(client):
    """HYBR-14: corpus sem acentos ("bula de dipirona") + query com acentos
    ("Dipironá") → termos casados de forma equivalente (hit esparso)."""
    store = _bula_store(client, _DivergentEmbeddings())  # "Bula de dipirona..."
    docs = retrieve("Dipironá", {DocType.BULA: store}, _settings())

    assert len(docs) == 1
    assert docs[0].page_content == "Bula de dipirona para dor intensa."


def test_ac_hybr14_unaccented_query_matches_accented_corpus(client):
    """HYBR-14 (inverso): corpus com acentos ("dór") + query sem acentos ("dor")
    → termos casados de forma equivalente."""
    settings = _settings()
    store = get_vectorstore(DocType.BULA, client, _DivergentEmbeddings(), settings)
    store.add_texts(
        texts=["Bula de dipirona para dór intensa."],
        metadatas=[{"doc_type": "bula", "source_path": "bula_dipirona.pdf"}],
        ids=["bula_001"],
    )

    docs = retrieve("Qual a dose de dipirona dor?", {DocType.BULA: store}, settings)

    assert len(docs) == 1
    assert "dór" in docs[0].page_content


# ---------------------------------------------------------------------------
# HYBR-15 / dosagens íntegras e normalização dígito-unidade
# ---------------------------------------------------------------------------


def test_ac_hybr15_dosage_token_500mg_intact_end_to_end(client):
    """HYBR-15: query "amoxicilina 500mg" mantém "500mg" como token íntegro e
    casa com "Amoxicilina 500mg" do corpus (hit esparso)."""
    settings = _settings()
    store = get_vectorstore(DocType.BULA, client, _DivergentEmbeddings(), settings)
    store.add_texts(
        texts=["Amoxicilina 500mg: dose habitual de 1 cápsula a cada 8 horas."],
        metadatas=[{"doc_type": "bula", "source_path": "bula_amoxicilina.pdf"}],
        ids=["bula_001"],
    )

    docs = retrieve("amoxicilina 500mg", {DocType.BULA: store}, settings)

    assert len(docs) == 1
    assert "500mg" in docs[0].page_content


def test_ac_hybr15_digit_unit_space_normalized_end_to_end(client):
    """HYBR-15: "10 mg" (com espaço) e "10mg" (sem espaço) normalizam para o
    mesmo token ``10mg`` e casam (decisão Q6)."""
    settings = _settings()
    store = get_vectorstore(DocType.BULA, client, _DivergentEmbeddings(), settings)
    store.add_texts(
        texts=["Amoxicilina 10mg: dose habitual de 1 cápsula."],
        metadatas=[{"doc_type": "bula", "source_path": "bula_amoxicilina.pdf"}],
        ids=["bula_001"],
    )

    # query com espaço "10 mg" casa o corpus "10mg"
    docs = retrieve("amoxicilina 10 mg", {DocType.BULA: store}, settings)

    assert len(docs) == 1
    assert "10mg" in docs[0].page_content


# ---------------------------------------------------------------------------
# HYBR-16 / stopwords esparsas próprias (mg/ml/g/kg preservados)
# ---------------------------------------------------------------------------


def test_ac_hybr16_sparse_stopwords_preserve_dosage_units(client):
    """HYBR-16: a tokenização esparsa usa ``retrieval_sparse_stopwords`` própria
    e NÃO remove as unidades de dosagem mg/ml/g/kg (preservadas no esparso)."""
    settings = _settings()

    # config: lista esparsa separada, sem unidades de dosagem
    assert "mg" not in settings.retrieval_sparse_stopwords
    assert "ml" not in settings.retrieval_sparse_stopwords
    assert "g" not in settings.retrieval_sparse_stopwords
    assert "kg" not in settings.retrieval_sparse_stopwords

    # tokenizer público: unidades preservadas (não são stopwords esparsas)
    assert tokenize("mg ml g kg", settings) == ["mg", "ml", "g", "kg"]

    # end-to-end: query com unidade de dosagem recupera o chunk certo
    store = get_vectorstore(DocType.BULA, client, _DivergentEmbeddings(), settings)
    store.add_texts(
        texts=["Bula de Alphazol 500 mg para dor intensa."],
        metadatas=[{"doc_type": "bula", "source_path": "bula_alphazol.pdf"}],
        ids=["bula_001"],
    )
    docs = retrieve("Alphazol 500mg", {DocType.BULA: store}, settings)
    assert len(docs) == 1
    assert "500 mg" in docs[0].page_content


# ---------------------------------------------------------------------------
# HYBR-17 / documento esparso reconstruído com metadados fiéis
# ---------------------------------------------------------------------------


def test_ac_hybr17_sparse_document_metadata_fidelity(client):
    """HYBR-17: documento recuperado apenas pelo esparso tem metadados fiéis e
    build_citations/_lexical_relevance_guard produzem o mesmo resultado do denso."""
    store = _bula_store(client, _DivergentEmbeddings())
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


# ---------------------------------------------------------------------------
# HYBR-18 / chunk ingerido após o índice construído fica visível na query seguinte
# ---------------------------------------------------------------------------


def test_ac_hybr18_ingest_after_index_built_visible_next_query(client):
    """HYBR-18: chunk ingerido na mesma coleção após o índice construído torna-se
    recuperável pelo esparso na query seguinte (índice refletido/invalidado)."""
    settings = _settings()
    store = get_vectorstore(DocType.BULA, client, _DivergentEmbeddings(), settings)
    store.add_texts(
        texts=["Bula de dipirona para dor intensa."],
        metadatas=[{"doc_type": "bula", "source_path": "bula_dipirona.pdf"}],
        ids=["bula_001"],
    )
    stores = {DocType.BULA: store}

    # primeira query constrói o índice e recupera o chunk de dipirona
    assert len(retrieve("Qual a dose de dipirona?", stores, settings)) == 1

    # ingest de novo chunk na mesma coleção (simula /ingest)
    store.add_texts(
        texts=["Bula de Zolatril para febre."],
        metadatas=[{"doc_type": "bula", "source_path": "bula_zolatril.pdf"}],
        ids=["bula_002"],
    )

    # query seguinte → o novo chunk fica visível ao esparso (índice invalidado)
    docs = retrieve("Qual a dose de Zolatril para febre?", stores, settings)
    assert any("Zolatril" in d.page_content for d in docs)


# ---------------------------------------------------------------------------
# HYBR-19 / biblioteca BM25 declarada em requirements (gap RAG-01 não repetido)
# ---------------------------------------------------------------------------


def test_ac_hybr19_requirements_declare_bm25_library():
    """HYBR-19: a biblioteca BM25 (rank_bm25) é declarada em requirements.txt e
    requirements-api.txt (importável no runtime da API)."""
    for req in ("requirements.txt", "requirements-api.txt"):
        text = (_REPO_ROOT / req).read_text(encoding="utf-8")
        assert "rank_bm25" in text, f"rank_bm25 ausente em {req}"


# ---------------------------------------------------------------------------
# HYBR-20 / coleção vazia → zero candidatos esparsos → cold start []
# ---------------------------------------------------------------------------


def test_ac_hybr20_empty_collection_zero_sparse_candidates_cold_start(caplog, client):
    """HYBR-20: coleção vazia → zero candidatos esparsos sem erro; combinado com
    denso vazio → cold start [] (com contagens de candidatos no log)."""
    settings = _settings()
    store = get_vectorstore(DocType.BULA, client, _DivergentEmbeddings(), settings)

    with caplog.at_level(logging.INFO, logger="medasist.retrieval.retriever"):
        docs = retrieve("Qual a dose de dipirona?", {DocType.BULA: store}, settings)

    assert docs == []
    record = _retrieve_record(caplog)
    assert record is not None
    message = record.getMessage()
    assert "cold_start=True" in message
    assert "n_dense_candidates=0" in message
    assert "n_sparse_candidates=0" in message


# ---------------------------------------------------------------------------
# HYBR-21 / defaults de Settings + .env.example documentado
# ---------------------------------------------------------------------------


def test_ac_hybr21_settings_defaults_and_env_example_documented():
    """HYBR-21: Settings com defaults expõe retrieval_hybrid_enabled=False,
    retrieval_hybrid_rrf_k=60 (gt=0), retrieval_hybrid_sparse_top_k=20 (>= top_k)
    e retrieval_sparse_stopwords; documentadas em .env.example."""
    s = Settings(admin_api_key=SecretStr(_ADMIN_KEY))
    assert s.retrieval_hybrid_enabled is False
    assert s.retrieval_hybrid_rrf_k == 60
    assert s.retrieval_hybrid_sparse_top_k == 20
    assert s.retrieval_hybrid_sparse_top_k >= s.retrieval_top_k
    assert isinstance(s.retrieval_sparse_stopwords, tuple)
    assert "mg" not in s.retrieval_sparse_stopwords

    text = _ENV_EXAMPLE.read_text(encoding="utf-8")
    for key in (
        "RETRIEVAL_HYBRID_ENABLED",
        "RETRIEVAL_HYBRID_RRF_K",
        "RETRIEVAL_HYBRID_SPARSE_TOP_K",
        "RETRIEVAL_SPARSE_STOPWORDS",
    ):
        assert f"{key}=" in text, f"{key} ausente em .env.example"


# ---------------------------------------------------------------------------
# HYBR-22 / override por env refletido em Settings
# ---------------------------------------------------------------------------


def test_ac_hybr22_env_override_reflected_in_settings(monkeypatch):
    """HYBR-22: .env/ambiente define RETRIEVAL_HYBRID_ENABLED=true e demais
    variáveis híbridas → Settings reflete os valores."""
    monkeypatch.setenv("RETRIEVAL_HYBRID_ENABLED", "true")
    monkeypatch.setenv("RETRIEVAL_HYBRID_RRF_K", "30")
    monkeypatch.setenv("RETRIEVAL_HYBRID_SPARSE_TOP_K", "5")
    monkeypatch.setenv("RETRIEVAL_SPARSE_STOPWORDS", '["de", "a", "para"]')

    s = Settings(admin_api_key=SecretStr(_ADMIN_KEY))

    assert s.retrieval_hybrid_enabled is True
    assert s.retrieval_hybrid_rrf_k == 30
    assert s.retrieval_hybrid_sparse_top_k == 5
    assert s.retrieval_sparse_stopwords == ("de", "a", "para")


# ---------------------------------------------------------------------------
# HYBR-23 / valores inválidos → erro de validação (fail-fast)
# ---------------------------------------------------------------------------


def test_ac_hybr23_invalid_values_fail_fast():
    """HYBR-23: retrieval_hybrid_rrf_k=0 ou retrieval_hybrid_sparse_top_k=-1
    lançam erro de validação (fail-fast)."""
    with pytest.raises(ValidationError):
        _settings(retrieval_hybrid_rrf_k=0)
    with pytest.raises(ValidationError):
        _settings(retrieval_hybrid_sparse_top_k=-1)


# ---------------------------------------------------------------------------
# HYBR-24 / log mantém campos atuais + campos aditivos de candidatos
# ---------------------------------------------------------------------------


def test_ac_hybr24_log_keeps_current_fields_and_adds_candidate_counts(caplog, client):
    """HYBR-24: com híbrido ativo, o record consolidado mantém chunks/scores/
    latency_ms/cold_start/doc_types e adiciona n_dense_candidates e
    n_sparse_candidates; scores é o score final RRF (um por doc)."""
    store = _bula_store(client, _DivergentEmbeddings())
    settings = _settings()

    with caplog.at_level(logging.INFO, logger="medasist.retrieval.retriever"):
        docs = retrieve("Qual a dose de dipirona?", {DocType.BULA: store}, settings)

    assert len(docs) == 1
    record = _retrieve_record(caplog)
    assert record is not None
    message = record.getMessage()
    # campos atuais preservados
    assert f"chunks={len(docs)}" in message
    assert "scores=[" in message
    assert "latency_ms=" in message
    assert "cold_start=False" in message
    assert "doc_types=['bula']" in message
    # campos aditivos
    assert "hybrid=True" in message
    assert "n_dense_candidates=0" in message
    assert "n_sparse_candidates=1" in message
    # scores = score final RRF (paralelo aos docs retornados)
    scores = list(record.args[3])
    assert len(scores) == len(docs) == 1
    assert isinstance(scores[0], float)
