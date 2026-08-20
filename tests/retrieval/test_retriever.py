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
from medasist.retrieval.retriever import retrieve, select_collections
from medasist.retrieval.sparse import reset_sparse_indexes
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


@pytest.fixture(autouse=True)
def _reset_sparse_indexes():
    """Zera o cache global do índice esparso antes e depois de cada teste."""
    reset_sparse_indexes()
    yield
    reset_sparse_indexes()


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
# Testes — reranking cross-encoder (RAG-01)
# ---------------------------------------------------------------------------


def _mock_store_with_candidates(candidates: list[tuple[Document, float]]) -> MagicMock:
    """Store mock cujos candidatos e distâncias L2 são totalmente controlados."""
    store = MagicMock(spec=Chroma)
    store.similarity_search_with_score.return_value = candidates
    return store


def test_retrieve_flag_off_returns_l2_identity_and_never_instantiates_model(
    mocker, client, embeddings, settings
):
    """Flag off (default): ordem L2 preservada; rerank_documents/modelo não chamados."""
    from medasist.retrieval.retriever import retrieve

    store = get_vectorstore(DocType.BULA, client, embeddings, settings)
    store.add_texts(
        texts=["Alphazol X: indicado para hipertensão arterial sistêmica."],
        metadatas=[{"doc_type": "bula", "source": "alphazol.pdf", "page": 1}],
        ids=["bula_001"],
    )

    mock_rerank = mocker.patch("medasist.retrieval.retriever.rerank_documents")
    mock_ce = mocker.patch("sentence_transformers.CrossEncoder")

    settings_loose = Settings(
        retrieval_top_k=10,
        retrieval_score_threshold=10.0,
    )
    docs = retrieve("hipertensão", {DocType.BULA: store}, settings_loose)

    assert isinstance(docs, list)
    assert all(isinstance(d, Document) for d in docs)
    mock_rerank.assert_not_called()
    mock_ce.assert_not_called()


def test_retrieve_flag_on_reorders_by_rerank_score(mocker, settings):
    """Flag on + CrossEncoder mockado: retrieve retorna docs na ordem do mock."""
    from medasist.retrieval.retriever import retrieve

    candidates = [
        (Document(page_content="A", metadata={"doc_type": "bula"}), 0.1),
        (Document(page_content="B", metadata={"doc_type": "bula"}), 0.2),
        (Document(page_content="C", metadata={"doc_type": "bula"}), 0.3),
    ]
    store = _mock_store_with_candidates(candidates)
    instance = MagicMock()
    instance.predict.return_value = [0.1, 0.9, 0.5]  # B > C > A
    mocker.patch("sentence_transformers.CrossEncoder", return_value=instance)

    settings_on = Settings(
        retrieval_top_k=10,
        retrieval_score_threshold=0.4,
        retrieval_rerank_enabled=True,
    )

    docs = retrieve("hipertensão", {DocType.BULA: store}, settings_on)

    assert isinstance(docs, list)
    assert all(isinstance(d, Document) for d in docs)
    assert [d.page_content for d in docs] == ["B", "C", "A"]


def test_retrieve_flag_on_respects_retrieval_top_k(mocker, settings):
    """Flag on: máximo de retrieval_top_k documentos retornados após o rerank."""
    from medasist.retrieval.retriever import retrieve

    candidates = [
        (Document(page_content=f"chunk-{i}", metadata={"doc_type": "manual"}), 0.1)
        for i in range(6)
    ]
    store = _mock_store_with_candidates(candidates)
    instance = MagicMock()
    instance.predict.return_value = [float(i) for i in range(6)]
    mocker.patch("sentence_transformers.CrossEncoder", return_value=instance)

    settings_on = Settings(
        retrieval_top_k=3,
        retrieval_score_threshold=0.4,
        retrieval_rerank_enabled=True,
    )

    docs = retrieve("conteúdo médico", {DocType.MANUAL: store}, settings_on)

    assert len(docs) <= 3
    assert isinstance(docs, list)
    assert all(isinstance(d, Document) for d in docs)


def test_retrieve_cold_start_does_not_call_reranker(mocker, empty_stores, settings):
    """Cold start (nenhum candidato L2): retorna [] e reranker não é chamado."""
    from medasist.retrieval.retriever import retrieve

    mock_rerank = mocker.patch("medasist.retrieval.retriever.rerank_documents")

    settings_on = Settings(
        retrieval_top_k=10,
        retrieval_score_threshold=0.4,
        retrieval_rerank_enabled=True,
    )

    docs = retrieve("qualquer consulta médica", empty_stores, settings_on)

    assert docs == []
    mock_rerank.assert_not_called()


def test_retrieve_lexical_cold_start_does_not_call_reranker(mocker, client, settings):
    """Cold start lexical: guarda esvazia → retorna [] e reranker não é chamado."""
    from medasist.retrieval.retriever import retrieve

    store = get_vectorstore(DocType.BULA, client, _FakeEmbeddings(), settings)
    store.add_texts(
        texts=["A dose maxima permitida por dia em adultos e de 640 gotas (3.200mg)."],
        metadatas=[{"doc_type": "bula", "source": "bula_ibuprofeno.pdf"}],
        ids=["bula_001"],
    )
    mock_rerank = mocker.patch("medasist.retrieval.retriever.rerank_documents")

    settings_on = Settings(
        retrieval_top_k=10,
        retrieval_score_threshold=10.0,
        retrieval_rerank_enabled=True,
    )

    docs = retrieve(
        "Qual a dose máxima de dipirona para adultos?",
        {DocType.BULA: store},
        settings_on,
    )

    assert docs == []
    mock_rerank.assert_not_called()


def test_retrieve_flag_on_never_empties_valid_context(mocker, settings):
    """Rerank nunca transforma não-cold-start em cold start (regra de segurança)."""
    from medasist.retrieval.retriever import retrieve

    candidates = [
        (Document(page_content=f"chunk-{i}", metadata={"doc_type": "bula"}), 0.1)
        for i in range(4)
    ]
    store = _mock_store_with_candidates(candidates)
    instance = MagicMock()
    # Todos os scores do reranker baixos — mesmo assim o contexto não esvazia
    instance.predict.return_value = [0.0, 0.0, 0.0, 0.0]
    mocker.patch("sentence_transformers.CrossEncoder", return_value=instance)

    settings_on = Settings(
        retrieval_top_k=10,
        retrieval_score_threshold=0.4,
        retrieval_rerank_enabled=True,
    )

    docs = retrieve("hipertensão", {DocType.BULA: store}, settings_on)

    assert len(docs) > 0
    assert isinstance(docs, list)
    assert all(isinstance(d, Document) for d in docs)


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


def test_retrieve_lexical_guard_blocks_cross_drug_cold_start(client, settings):
    """Consulta sobre um medicamento não presente nos chunks retorna vazio.

    Simula o cenário real: pergunta sobre dipirona recuperando chunks de
    ibuprofeno (analgésico semelhante). Como nenhum chunk menciona dipirona,
    a guarda lexical trata como cold start em vez de permitir alucinação.
    """
    from medasist.retrieval.retriever import retrieve

    store = get_vectorstore(DocType.BULA, client, _FakeEmbeddings(), settings)
    store.add_texts(
        texts=["A dose maxima permitida por dia em adultos e de 640 gotas (3.200mg)."],
        metadatas=[{"doc_type": "bula", "source": "bula_ibuprofeno.pdf"}],
        ids=["bula_001"],
    )

    settings_loose = Settings(
        retrieval_top_k=10,
        retrieval_score_threshold=10.0,
    )

    docs = retrieve(
        "Qual a dose máxima de dipirona para adultos?",
        {DocType.BULA: store},
        settings_loose,
    )

    assert docs == []


def test_retrieve_lexical_guard_allows_same_drug(client, settings):
    """Consulta sobre medicamento presente nos chunks mantém o resultado."""
    from medasist.retrieval.retriever import retrieve

    store = get_vectorstore(DocType.BULA, client, _FakeEmbeddings(), settings)
    store.add_texts(
        texts=["Amoxicilina 500mg: dose habitual de 1 cápsula a cada 8 horas."],
        metadatas=[{"doc_type": "bula", "source": "bula_amoxicilina.pdf"}],
        ids=["bula_001"],
    )

    settings_loose = Settings(
        retrieval_top_k=10,
        retrieval_score_threshold=10.0,
    )

    docs = retrieve(
        "Qual a dose de amoxicilina para adultos?",
        {DocType.BULA: store},
        settings_loose,
    )

    assert len(docs) > 0
    assert all(isinstance(d, Document) for d in docs)


def test_retrieve_lexical_guard_ignores_queries_without_drug(client, settings):
    """Consulta sem termo de medicamento não é bloqueada pela guarda."""
    from medasist.retrieval.retriever import retrieve

    store = get_vectorstore(DocType.DIRETRIZ, client, _FakeEmbeddings(), settings)
    store.add_texts(
        texts=["Diretriz de hipertensão: reduzir sal e praticar atividade física."],
        metadatas=[{"doc_type": "diretriz", "source": "htn_guideline.pdf"}],
        ids=["dir_001"],
    )

    settings_loose = Settings(
        retrieval_top_k=10,
        retrieval_score_threshold=10.0,
    )

    docs = retrieve(
        "Como controlar a hipertensão arterial?",
        {DocType.DIRETRIZ: store},
        settings_loose,
    )

    assert len(docs) > 0


def test_drug_terms_in_extracts_drug_suffixes(settings):
    """_drug_terms_in identifica termos com sufixo de droga na consulta."""
    from medasist.retrieval.retriever import _drug_terms_in

    assert _drug_terms_in("Qual a dose de dipirona para adultos?", settings) == {
        "dipirona"
    }
    assert _drug_terms_in("Amoxicilina 500mg a cada 8 horas", settings) == {
        "amoxicilina"
    }
    assert _drug_terms_in("Como tratar pneumonia em camaleões?", settings) == set()


def test_drug_terms_in_ignores_common_words(settings):
    """Palavras comuns que terminam em sufixo de droga não são medicamentos."""
    from medasist.retrieval.retriever import _drug_terms_in

    for query in (
        "Evitar exposição ao sol durante o tratamento",
        "Quantas vezes tomaram o medicamento por dia?",
        "Como a paciente menina deve tomar o remédio?",
        "Qual o efeito colateral do álcool?",
    ):
        assert _drug_terms_in(query, settings) == set()


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


# ---------------------------------------------------------------------------
# Testes — busca híbrida denso + esparso (RAG-02)
# ---------------------------------------------------------------------------


class _DivergentEmbeddings(Embeddings):
    """Query vector bem diferente dos docs — distância L2 alta (denso vazio)."""

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 1.0, 1.0, 1.0] for _ in texts]

    def embed_query(self, text: str) -> list[float]:
        return [0.0, 0.0, 0.0, 0.0]


def _hybrid_settings(**overrides: object) -> Settings:
    """Settings com busca híbrida habilitada."""
    defaults: dict[str, object] = {
        "retrieval_top_k": 10,
        "retrieval_score_threshold": 0.4,
        "retrieval_hybrid_enabled": True,
        "retrieval_hybrid_rrf_k": 60,
        "retrieval_hybrid_sparse_top_k": 20,
    }
    defaults.update(overrides)
    return Settings(**defaults)


def test_retrieve_hybrid_flag_off_never_builds_sparse_index(mocker, client, settings):
    """Flag off: retrieve não constrói índice esparso (rank_bm25 não importado)."""
    store = get_vectorstore(DocType.BULA, client, _FakeEmbeddings(), settings)
    store.add_texts(
        texts=["Alphazol X: indicado para hipertensão arterial sistêmica."],
        metadatas=[{"doc_type": "bula", "source_path": "alphazol.pdf", "page": 1}],
        ids=["bula_001"],
    )

    mock_sparse = mocker.patch("medasist.retrieval.retriever.get_sparse_index")

    settings_loose = Settings(
        retrieval_top_k=10,
        retrieval_score_threshold=10.0,
    )
    docs = retrieve("hipertensão", {DocType.BULA: store}, settings_loose)

    assert isinstance(docs, list)
    assert all(isinstance(d, Document) for d in docs)
    mock_sparse.assert_not_called()


def test_retrieve_hybrid_sparse_only_hit_is_not_cold_start(client, settings):
    """Denso vazio + esparso com hit exato aprovado pela guarda ≠ cold start."""
    store = get_vectorstore(DocType.BULA, client, _DivergentEmbeddings(), settings)
    store.add_texts(
        texts=["Bula de dipirona para dor intensa."],
        metadatas=[{"doc_type": "bula", "source_path": "bula_dipirona.pdf", "page": 2}],
        ids=["bula_001"],
    )

    settings_on = _hybrid_settings()
    docs = retrieve(
        "Qual a dose de dipirona para adultos?",
        {DocType.BULA: store},
        settings_on,
    )

    assert len(docs) > 0
    assert isinstance(docs, list)
    assert all(isinstance(d, Document) for d in docs)
    assert docs[0].metadata["source_path"] == "bula_dipirona.pdf"


def test_retrieve_hybrid_both_empty_is_cold_start(client, settings):
    """Denso vazio E esparso vazio → lista vazia (cold start)."""
    store = get_vectorstore(DocType.BULA, client, _DivergentEmbeddings(), settings)
    store.add_texts(
        texts=["Bula de ibuprofeno para febre."],
        metadatas=[{"doc_type": "bula", "source_path": "bula_ibuprofeno.pdf"}],
        ids=["bula_001"],
    )

    settings_on = _hybrid_settings()
    docs = retrieve(
        "Qual a dose de dipirona para adultos?",
        {DocType.BULA: store},
        settings_on,
    )

    assert docs == []


def test_retrieve_hybrid_lexical_guard_blocks_cross_drug_sparse_hit(client, settings):
    """Guarda lexical bloqueia hit esparso genuíno de outro fármaco → [].

    A consulta ("...dipirona... febre") compartilha o token "febre" com o chunk
    de ibuprofeno ("Bula de ibuprofeno para febre."), então o caminho esparso
    recupera o chunk de verdade; a guarda esvazia o contexto porque a consulta
    menciona dipirona e o chunk não a contém → cold start [].
    """
    store = get_vectorstore(DocType.BULA, client, _DivergentEmbeddings(), settings)
    store.add_texts(
        texts=["Bula de ibuprofeno para febre."],
        metadatas=[{"doc_type": "bula", "source_path": "bula_ibuprofeno.pdf"}],
        ids=["bula_001"],
    )

    settings_on = _hybrid_settings()

    # pré-condição: o token "febre" gera um hit esparso genuíno (≠ denso vazio);
    # sem isso o teste passaria mesmo com a guarda removida/broken
    from medasist.retrieval.sparse import get_sparse_index

    index = get_sparse_index(store, settings_on)
    assert index is not None
    assert index.search(
        "Qual a dose de dipirona para febre?",
        settings_on.retrieval_hybrid_sparse_top_k,
    )

    docs = retrieve(
        "Qual a dose de dipirona para febre?",
        {DocType.BULA: store},
        settings_on,
    )

    assert docs == []


def test_retrieve_hybrid_per_doctype_isolation(client, settings):
    """doc_types=[BULA] limita candidatos esparsos à coleção de bulas (HYBR-10)."""
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
        texts=["Manual de dipirona para referência rápida."],
        metadatas=[{"doc_type": "manual", "source_path": "manual_dipirona.pdf"}],
        ids=["manual_001"],
    )
    stores = {DocType.BULA: store_bula, DocType.MANUAL: store_manual}

    subset = select_collections(stores, [DocType.BULA])
    settings_on = _hybrid_settings()
    docs = retrieve("dipirona dor", subset, settings_on)

    assert len(docs) > 0
    assert all(d.metadata["doc_type"] == "bula" for d in docs)


def test_retrieve_hybrid_respects_top_k(client, settings):
    """Híbrido respeita o corte final em retrieval_top_k."""
    store = get_vectorstore(DocType.BULA, client, _FakeEmbeddings(), settings)
    texts = [f"Bula de dipirona dor seção {i}." for i in range(10)]
    store.add_texts(
        texts=texts,
        metadatas=[{"doc_type": "bula"} for _ in texts],
        ids=[f"bula_{i:02d}" for i in range(10)],
    )

    settings_on = _hybrid_settings(retrieval_top_k=3)
    docs = retrieve("dipirona dor", {DocType.BULA: store}, settings_on)

    assert isinstance(docs, list)
    assert all(isinstance(d, Document) for d in docs)
    assert len(docs) <= 3


def test_retrieve_hybrid_fusion_orders_by_rrf(client, settings):
    """Híbrido funde denso + esparso por RRF, mantendo contrato list[Document]."""
    store = get_vectorstore(DocType.BULA, client, _FakeEmbeddings(), settings)
    store.add_texts(
        texts=["Alphazol X: indicado para hipertensão arterial sistêmica."],
        metadatas=[{"doc_type": "bula", "source_path": "alphazol.pdf", "page": 1}],
        ids=["bula_001"],
    )

    settings_on = _hybrid_settings()
    docs = retrieve("hipertensão Alphazol", {DocType.BULA: store}, settings_on)

    assert isinstance(docs, list)
    assert all(isinstance(d, Document) for d in docs)


def test_retrieve_hybrid_sparse_failure_falls_back_to_dense_only(
    client, mocker, settings
):
    """Falha esparsa → dense-only preservado; contexto denso nunca esvaziado."""
    store = get_vectorstore(DocType.BULA, client, _FakeEmbeddings(), settings)
    store.add_texts(
        texts=["Alphazol X: indicado para hipertensão arterial sistêmica."],
        metadatas=[{"doc_type": "bula", "source_path": "alphazol.pdf", "page": 1}],
        ids=["bula_001"],
    )

    failing_index = MagicMock()
    failing_index.search.side_effect = RuntimeError("falha na busca esparsa")
    mocker.patch(
        "medasist.retrieval.retriever.get_sparse_index",
        return_value=failing_index,
    )

    settings_on = _hybrid_settings()
    docs = retrieve("hipertensão", {DocType.BULA: store}, settings_on)

    assert len(docs) > 0
    assert isinstance(docs, list)
    assert all(isinstance(d, Document) for d in docs)


def test_retrieve_hybrid_logs_additive_candidate_counts(caplog, client, settings):
    """Log híbrido contém n_dense_candidates/n_sparse_candidates + campos atuais."""
    store = get_vectorstore(DocType.BULA, client, _FakeEmbeddings(), settings)
    store.add_texts(
        texts=["Alphazol X: indicado para hipertensão arterial sistêmica."],
        metadatas=[{"doc_type": "bula", "source_path": "alphazol.pdf", "page": 1}],
        ids=["bula_001"],
    )

    settings_on = _hybrid_settings()
    with caplog.at_level(logging.INFO, logger="medasist.retrieval.retriever"):
        docs = retrieve("hipertensão Alphazol", {DocType.BULA: store}, settings_on)

    record = _retrieve_record(caplog)
    assert record is not None
    message = record.getMessage()
    assert f"chunks={len(docs)}" in message
    assert "latency_ms=" in message
    assert "cold_start=False" in message
    assert "doc_types=['bula']" in message
    assert "hybrid=True" in message
    assert "n_dense_candidates=" in message
    assert "n_sparse_candidates=" in message


def test_retrieve_hybrid_cold_start_logs_additive_fields(caplog, client, settings):
    """Cold start híbrido loga contagens de candidatos denso/esparso."""
    store = get_vectorstore(DocType.BULA, client, _DivergentEmbeddings(), settings)
    store.add_texts(
        texts=["Bula de ibuprofeno para febre."],
        metadatas=[{"doc_type": "bula", "source_path": "bula_ibuprofeno.pdf"}],
        ids=["bula_001"],
    )

    settings_on = _hybrid_settings()
    with caplog.at_level(logging.INFO, logger="medasist.retrieval.retriever"):
        docs = retrieve(
            "Qual a dose de dipirona?",
            {DocType.BULA: store},
            settings_on,
        )

    assert docs == []
    record = _retrieve_record(caplog)
    assert record is not None
    message = record.getMessage()
    assert "cold_start=True" in message
    assert "hybrid=True" in message
    assert "n_dense_candidates=0" in message
    assert "n_sparse_candidates=" in message


# ---------------------------------------------------------------------------
# Testes — reescrita de consulta (RAG-03)
# ---------------------------------------------------------------------------


def test_retrieve_uses_rewritten_query_in_search(mocker, settings):
    """Com reescrita, o retrieve usa a consulta expandida na busca por similaridade."""
    from medasist.retrieval.retriever import retrieve

    store = _mock_store_with_candidates(
        [
            (
                Document(
                    page_content="Bula sobre febre.", metadata={"doc_type": "bula"}
                ),
                0.1,
            )
        ]
    )
    mocker.patch(
        "medasist.retrieval.retriever.rewrite_query",
        return_value="Qual a causa da febre em adultos?",
    )

    settings_loose = Settings(
        retrieval_top_k=10,
        retrieval_score_threshold=10.0,
    )
    retrieve("febre", {DocType.BULA: store}, settings_loose)

    store.similarity_search_with_score.assert_called_once_with(
        "Qual a causa da febre em adultos?", k=10
    )


def test_retrieve_empty_stores_does_not_call_rewrite(mocker, settings):
    """Stores vazio → cold start [] e a reescrita nunca é chamada (RQ-03-07)."""
    from medasist.retrieval.retriever import retrieve

    mock_rewrite = mocker.patch("medasist.retrieval.retriever.rewrite_query")

    docs = retrieve("dipirona", {}, settings)

    assert docs == []
    mock_rewrite.assert_not_called()


def test_retrieve_flag_off_uses_original_query_identity(mocker, settings):
    """Flag off (identidade): a busca usa a consulta original, sem reescrita."""
    from medasist.retrieval.retriever import retrieve

    store = _mock_store_with_candidates(
        [
            (
                Document(
                    page_content="Bula sobre febre.", metadata={"doc_type": "bula"}
                ),
                0.1,
            )
        ]
    )
    mocker.patch(
        "medasist.retrieval.retriever.rewrite_query",
        side_effect=lambda q, s: q,
    )

    settings_loose = Settings(
        retrieval_top_k=10,
        retrieval_score_threshold=10.0,
    )
    retrieve("febre", {DocType.BULA: store}, settings_loose)

    store.similarity_search_with_score.assert_called_once_with("febre", k=10)


def test_retrieve_logs_rewritten_flag_when_rewrite_changes_query(
    mocker, client, settings, caplog
):
    """Log consolidado inclui o campo aditivo rewritten=True quando a query mudou."""
    from medasist.retrieval.retriever import retrieve

    store = get_vectorstore(DocType.BULA, client, _FakeEmbeddings(), settings)
    store.add_texts(
        texts=["Bula de dipirona para dor intensa."],
        metadatas=[{"doc_type": "bula", "source_path": "bula_dipirona.pdf"}],
        ids=["bula_001"],
    )
    mocker.patch(
        "medasist.retrieval.retriever.rewrite_query",
        return_value="Qual a dose de dipirona para adultos?",
    )

    settings_loose = Settings(
        retrieval_top_k=10,
        retrieval_score_threshold=10.0,
    )
    with caplog.at_level(logging.INFO, logger="medasist.retrieval.retriever"):
        docs = retrieve("dipirona", {DocType.BULA: store}, settings_loose)

    assert len(docs) > 0
    record = _retrieve_record(caplog)
    assert record is not None
    assert "rewritten=True" in record.getMessage()
