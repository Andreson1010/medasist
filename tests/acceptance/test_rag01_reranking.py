from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from langchain_chroma import Chroma
from langchain_core.documents import Document
from pydantic import ValidationError

from medasist.config import Settings
from medasist.evaluation.dataset import GoldenQuestion
from medasist.generation.citations import CitationItem, build_citations
from medasist.ingestion.schemas import DocType
from medasist.profiles.schemas import UserProfile
from medasist.retrieval import reranker
from medasist.retrieval.retriever import retrieve, select_collections

"""Acceptance tests for RAG-01 (cross-encoder reranking of retrieval).

Verifies the feature from the outside, through the consumer-facing
``retrieve()`` / ``run_query()`` entry points, with the ``CrossEncoder``
always mocked (never a real model or network). No ``src/`` file is modified.

Each test maps to one acceptance criterion from the approved story:

- AC-01..AC-04 happy path (ordering, single batch, eval-path invariant,
  citations on reranked context).
- AC-05..AC-07 failure paths (reranker failure, cold start, disabled).
- AC-08..AC-12 business rules (cold-start safety, contract, top_k cap,
  settings constraints, singleton reuse).
- AC-13/AC-14 additional spec criteria (rerank after lexical guard;
  default rerank_top_n >= retrieval_top_k).
"""

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ENV_EXAMPLE = _REPO_ROOT / ".env.example"

_DEFAULT_STORE_DOCS = {
    DocType.BULA: "bula_alphazol.pdf",
    DocType.DIRETRIZ: "diretriz_dm2.pdf",
    DocType.PROTOCOLO: "protocolo_hpv.pdf",
    DocType.MANUAL: "manual_insulina.pdf",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _settings(**overrides: object) -> Settings:
    """Settings com defaults de rerank e overrides por critério."""
    defaults: dict[str, object] = {
        "retrieval_top_k": 10,
        "retrieval_score_threshold": 0.4,
    }
    defaults.update(overrides)
    return Settings(**defaults)


def _candidates(*items: tuple[str, float, DocType]) -> list[tuple[Document, float]]:
    """Monta candidatos (Document, distância L2) com metadata coerente.

    Parameters
    ----------
    items : tuple[str, float, DocType]
        Sequência de (page_content, distância L2, doc_type).

    Returns
    -------
    list[tuple[Document, float]]
        Candidatos na ordem dada.
    """
    pairs: list[tuple[Document, float]] = []
    for i, (content, score, doc_type) in enumerate(items):
        doc = Document(
            page_content=content,
            metadata={
                "source": _DEFAULT_STORE_DOCS[doc_type],
                "doc_type": doc_type.value,
                "section": "Posologia",
                "page": str(i + 1),
            },
        )
        pairs.append((doc, score))
    return pairs


def _store_with_candidates(
    candidates: list[tuple[Document, float]],
) -> MagicMock:
    """Store mock cujos candidatos e distâncias L2 são totalmente controlados."""
    store = MagicMock(spec=Chroma)
    store.similarity_search_with_score.return_value = candidates
    return store


def _contents(docs: list[Document]) -> list[str]:
    return [d.page_content for d in docs]


@pytest.fixture(autouse=True)
def _reset_reranker():
    """Zera o singleton global do CrossEncoder antes e depois de cada teste."""
    reranker._reranker = None
    yield
    reranker._reranker = None


@pytest.fixture
def mock_cross_encoder(mocker: MagicMock):
    """Patcheia ``sentence_transformers.CrossEncoder`` com predict controlado.

    Returns
    -------
    tuple[MagicMock, MagicMock]
        (classe mockada, instância mockada).
    """
    instance = MagicMock()
    mock_cls = mocker.patch(
        "sentence_transformers.CrossEncoder", return_value=instance
    )
    return mock_cls, instance


# ---------------------------------------------------------------------------
# AC-01 / happy path: order by reranker score, respect retrieval_top_k
# ---------------------------------------------------------------------------


def test_ac01_rerank_enabled_orders_by_score_and_respects_top_k(
    mock_cross_encoder,
) -> None:
    """AC-01: rerank habilitado + modelo carregado → Document retornados
    ordenados por score do reranker (maior primeiro), respeitando top_k."""
    _, instance = mock_cross_encoder
    # L2 order: A, B, C, D (ascending distance). Reranker inverts → B > D > C > A
    instance.predict.return_value = [0.1, 0.9, 0.5, 0.7]
    candidates = _candidates(
        ("conteudo A", 0.1, DocType.BULA),
        ("conteudo B", 0.2, DocType.BULA),
        ("conteudo C", 0.3, DocType.BULA),
        ("conteudo D", 0.4, DocType.BULA),
    )
    store = _store_with_candidates(candidates)
    settings = _settings(retrieval_top_k=3, retrieval_rerank_enabled=True)

    docs = retrieve("hipertensão arterial", {DocType.BULA: store}, settings)

    assert isinstance(docs, list)
    assert all(isinstance(d, Document) for d in docs)
    # reranker scores: B=0.9, D=0.7, C=0.5, A=0.1 → [B, D, C] after top_k=3
    assert _contents(docs) == ["conteudo B", "conteudo D", "conteudo C"]
    assert len(docs) == 3


# ---------------------------------------------------------------------------
# AC-02 / multiple collections scored in a single batched call
# ---------------------------------------------------------------------------


def test_ac02_multiple_collections_single_batched_call(
    mock_cross_encoder,
) -> None:
    """AC-02: candidatos de até 4 stores são pontuados em UMA chamada em batch,
    cobrindo todos até ``rerank_top_n``."""
    _, instance = mock_cross_encoder
    stores = {
        DocType.BULA: _store_with_candidates(
            _candidates(("bula 1", 0.1, DocType.BULA), ("bula 2", 0.2, DocType.BULA))
        ),
        DocType.DIRETRIZ: _store_with_candidates(
            _candidates(("diretriz 1", 0.1, DocType.DIRETRIZ))
        ),
        DocType.PROTOCOLO: _store_with_candidates(
            _candidates(("protocolo 1", 0.1, DocType.PROTOCOLO))
        ),
        DocType.MANUAL: _store_with_candidates(
            _candidates(
                ("manual 1", 0.1, DocType.MANUAL),
                ("manual 2", 0.2, DocType.MANUAL),
            )
        ),
    }
    # top_n default 20 >= 6 candidatos → todos pontuados
    settings = _settings(retrieval_rerank_enabled=True)
    instance.predict.return_value = [0.9] * 6

    docs = retrieve("tratamento geral", stores, settings)

    assert len(docs) == 6
    instance.predict.assert_called_once()
    pairs = instance.predict.call_args.args[0]
    contents = {pair[1] for pair in pairs}
    assert contents == {
        "bula 1",
        "bula 2",
        "diretriz 1",
        "protocolo 1",
        "manual 1",
        "manual 2",
    }


# ---------------------------------------------------------------------------
# AC-03 / eval path (_collect_rows) applies rerank on the same path (AD-011)
# ---------------------------------------------------------------------------


def test_ac03_eval_collect_rows_applies_rerank_identical_contexts(
    mocker: MagicMock, mock_cross_encoder
) -> None:
    """AC-03: o fluxo RAGAS (``_collect_rows``) chama ``retrieve()`` diretamente
    e aplica o rerank no mesmo caminho de produção, mantendo os contexts da
    avaliação idênticos aos usados na resposta (invariante AD-011)."""
    from medasist.evaluation.metrics import _collect_rows

    _, instance = mock_cross_encoder
    # L2 order: A, B, C. Reranker reordena → B, C, A
    instance.predict.return_value = [0.2, 0.9, 0.4]
    candidates = _candidates(
        ("ctx A", 0.1, DocType.BULA),
        ("ctx B", 0.2, DocType.BULA),
        ("ctx C", 0.3, DocType.BULA),
    )
    store = _store_with_candidates(candidates)
    stores = {DocType.BULA: store}
    settings = _settings(retrieval_rerank_enabled=True)

    questions = [
        GoldenQuestion(
            question="hipertensão arterial",
            reference_answer="Resposta de referência.",
            reference_contexts=["ctx A", "ctx B", "ctx C"],
        )
    ]

    captured: dict[str, object] = {}

    def _fake_run_query(question, stores_arg, profile, settings_arg, doc_types=None):
        # espelha o caminho de produção (run_query → select_collections → retrieve)
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

    mocker.patch(
        "medasist.evaluation.metrics.run_query", side_effect=_fake_run_query
    )

    rows, cold_flags = _collect_rows(
        questions, stores, settings, UserProfile.MEDICO, None
    )

    assert cold_flags == [False]
    answer_docs = captured["answer_docs"]
    assert answer_docs is not None
    # AD-011: contexts da avaliação == contexts usados na resposta (rerank aplicado)
    assert rows[0]["contexts"] == _contents(answer_docs)  # type: ignore[arg-type]
    assert rows[0]["contexts"] == ["ctx B", "ctx C", "ctx A"]


# ---------------------------------------------------------------------------
# AC-04 / citations on a reranked context still map to valid CitationItem
# ---------------------------------------------------------------------------


def test_ac04_reranked_context_citations_map_to_valid_items(mocker: MagicMock) -> None:
    """AC-04: com um contexto rerankado, ``run_query`` gera resposta cujas
    citações ``[N]`` mapeiam todas para ``CitationItem`` válidos."""
    from langchain_core.messages import AIMessage

    from medasist.generation.chain import run_query

    settings = _settings(retrieval_rerank_enabled=True)
    settings.retrieval_rerank_enabled = True
    stores = MagicMock()

    # docs em ordem rerankada (mais relevante primeiro)
    reranked_docs = [
        Document(
            page_content="conteudo B",
            metadata={"source": "bula_b.pdf", "section": "Posologia", "page": "2"},
        ),
        Document(
            page_content="conteudo A",
            metadata={"source": "bula_a.pdf", "section": "Posologia", "page": "1"},
        ),
    ]

    with (
        patch("medasist.generation.chain.build_retriever") as mock_rb,
        patch("medasist.generation.chain.ChatOpenAI") as mock_llm_cls,
    ):
        mock_retriever = MagicMock()
        mock_retriever.invoke.return_value = reranked_docs
        mock_rb.return_value = mock_retriever
        mock_llm_instance = MagicMock()
        mock_llm_cls.return_value = mock_llm_instance
        mock_llm_instance.return_value = AIMessage(content="Recomendo [1] e [2].")

        result = run_query(
            "qual a dose?", stores, UserProfile.MEDICO, settings
        )

    assert result.is_cold_start is False
    assert len(result.citations) == 2
    assert all(isinstance(c, CitationItem) for c in result.citations)
    assert {c.index for c in result.citations} == {1, 2}
    assert result.citations[0].source == "bula_b.pdf"
    assert result.citations[1].source == "bula_a.pdf"
    # cada marcador [N] presente na resposta corresponde a um CitationItem
    assert "[1]" in result.answer and "[2]" in result.answer


# ---------------------------------------------------------------------------
# AC-05 / failure path: reranker fails → L2 order, query does not fail
# ---------------------------------------------------------------------------


def test_ac05_reranker_failure_returns_l2_order_and_logs(
    mock_cross_encoder, caplog
) -> None:
    """AC-05: falha do reranker (erro/timeout) não falha a query; docs retornam
    na ordem L2 original e o erro é registrado em log."""
    _, instance = mock_cross_encoder
    instance.predict.side_effect = RuntimeError("timeout do reranker")
    candidates = _candidates(
        ("A", 0.1, DocType.BULA),
        ("B", 0.2, DocType.BULA),
        ("C", 0.3, DocType.BULA),
    )
    store = _store_with_candidates(candidates)
    settings = _settings(retrieval_rerank_enabled=True)

    with caplog.at_level(logging.ERROR, logger="medasist.retrieval.reranker"):
        docs = retrieve("hipertensão", {DocType.BULA: store}, settings)

    # não falha: retorna docs, ordem L2 original preservada
    assert _contents(docs) == ["A", "B", "C"]
    assert isinstance(docs, list)
    assert all(isinstance(d, Document) for d in docs)
    assert any(
        r.levelno == logging.ERROR and "Reranker falhou" in r.getMessage()
        for r in caplog.records
    )


# ---------------------------------------------------------------------------
# AC-06 / failure path: cold start unchanged, reranker not called
# ---------------------------------------------------------------------------


def test_ac06_cold_start_unchanged_reranker_not_called(
    mocker: MagicMock,
) -> None:
    """AC-06: nenhum candidato passa o threshold L2 (cold start) → lista vazia,
    mensagem fixa e o reranker NÃO é chamado."""
    from medasist.generation.chain import run_query

    # --- retrieve(): cold start → [] e rerank não chamado ---
    mock_rerank = mocker.patch("medasist.retrieval.retriever.rerank_documents")
    store = _store_with_candidates([])
    settings = _settings(retrieval_rerank_enabled=True)

    docs = retrieve("qualquer consulta", {DocType.BULA: store}, settings)

    assert docs == []
    mock_rerank.assert_not_called()

    # --- run_query(): mensagem fixa em cold start ---
    with patch("medasist.generation.chain.build_retriever") as mock_rb:
        mock_retriever = MagicMock()
        mock_retriever.invoke.return_value = []
        mock_rb.return_value = mock_retriever

        result = run_query(
            "qualquer consulta",
            {DocType.BULA: store},
            UserProfile.MEDICO,
            settings,
        )

    assert result.is_cold_start is True
    assert result.citations == []
    assert result.answer == settings.cold_start_message


# ---------------------------------------------------------------------------
# AC-07 / disabled → identity, model not loaded nor called
# ---------------------------------------------------------------------------


def test_ac07_disabled_returns_l2_identity_model_not_loaded(
    mocker: MagicMock,
) -> None:
    """AC-07: reranker desabilitado → docs na ordem L2 original e o reranker
    NÃO é carregado nem chamado (identidade)."""
    mock_rerank = mocker.patch("medasist.retrieval.retriever.rerank_documents")
    mock_ce = mocker.patch("sentence_transformers.CrossEncoder")
    candidates = _candidates(
        ("A", 0.1, DocType.BULA),
        ("B", 0.2, DocType.BULA),
        ("C", 0.3, DocType.BULA),
    )
    store = _store_with_candidates(candidates)
    settings = _settings(retrieval_rerank_enabled=False)

    docs = retrieve("hipertensão", {DocType.BULA: store}, settings)

    assert _contents(docs) == ["A", "B", "C"]
    assert isinstance(docs, list)
    assert all(isinstance(d, Document) for d in docs)
    mock_rerank.assert_not_called()
    mock_ce.assert_not_called()


# ---------------------------------------------------------------------------
# AC-08 / cold-start decision stays on pre-rerank L2 (medical safety rule)
# ---------------------------------------------------------------------------


def test_ac08_rerank_never_turns_valid_context_into_cold_start(
    mock_cross_encoder,
) -> None:
    """AC-08: rerank nunca transforma um retrieval não-cold-start em cold start —
    a decisão permanece baseada no threshold L2 pré-rerank, mesmo com scores
    baixos do reranker."""
    _, instance = mock_cross_encoder
    # todos os scores do reranker baixos → mesmo assim o contexto não esvazia
    instance.predict.return_value = [0.0, 0.0, 0.0, 0.0]
    candidates = _candidates(
        ("A", 0.1, DocType.BULA),
        ("B", 0.2, DocType.BULA),
        ("C", 0.3, DocType.BULA),
        ("D", 0.4, DocType.BULA),
    )
    store = _store_with_candidates(candidates)
    settings = _settings(retrieval_rerank_enabled=True)

    docs = retrieve("hipertensão", {DocType.BULA: store}, settings)

    assert len(docs) > 0
    assert isinstance(docs, list)
    assert all(isinstance(d, Document) for d in docs)


# ---------------------------------------------------------------------------
# AC-09 / public signature list[Document] and chain contract unchanged
# ---------------------------------------------------------------------------


def test_ac09_public_signature_and_chain_contract_unchanged(
    mocker: MagicMock, mock_cross_encoder
) -> None:
    """AC-09: sob qualquer configuração de rerank, ``retrieve()`` continua
    retornando ``list[Document]`` e a chain (``run_query``) não muda de
    contrato."""
    from medasist.generation.chain import GenerationResult, run_query

    # --- retrieve() com flag on: list[Document] ---
    _, instance = mock_cross_encoder
    instance.predict.return_value = [0.1, 0.9, 0.5]
    candidates = _candidates(
        ("A", 0.1, DocType.BULA),
        ("B", 0.2, DocType.BULA),
        ("C", 0.3, DocType.BULA),
    )
    store = _store_with_candidates(candidates)
    settings_on = _settings(retrieval_rerank_enabled=True)

    docs_on = retrieve("hipertensão", {DocType.BULA: store}, settings_on)
    assert isinstance(docs_on, list)
    assert all(isinstance(d, Document) for d in docs_on)

    # --- retrieve() com flag off: list[Document] (identidade) ---
    settings_off = _settings(retrieval_rerank_enabled=False)
    docs_off = retrieve("hipertensão", {DocType.BULA: store}, settings_off)
    assert isinstance(docs_off, list)
    assert all(isinstance(d, Document) for d in docs_off)

    # --- run_query com rerank on: GenerationResult (contrato intacto) ---
    from langchain_core.messages import AIMessage

    with (
        patch("medasist.generation.chain.build_retriever") as mock_rb,
        patch("medasist.generation.chain.ChatOpenAI") as mock_llm_cls,
    ):
        mock_retriever = MagicMock()
        mock_retriever.invoke.return_value = [doc for doc, _ in candidates[:2]]
        mock_rb.return_value = mock_retriever
        mock_llm_instance = MagicMock()
        mock_llm_cls.return_value = mock_llm_instance
        mock_llm_instance.return_value = AIMessage(content="Resposta [1].")

        result = run_query(
            "qual a dose?",
            {DocType.BULA: store},
            UserProfile.MEDICO,
            settings_on,
        )

    assert isinstance(result, GenerationResult)
    assert result.is_cold_start is False
    assert len(result.citations) == 1
    assert isinstance(result.citations[0], CitationItem)


# ---------------------------------------------------------------------------
# AC-10 / final cut: at most retrieval_top_k returned
# ---------------------------------------------------------------------------


def test_ac10_returned_count_at_most_retrieval_top_k(mock_cross_encoder) -> None:
    """AC-10: com rerank habilitado, o corte final devolve no máximo
    ``retrieval_top_k`` documentos."""
    _, instance = mock_cross_encoder
    candidates = _candidates(
        *[
            (f"chunk-{i}", 0.1, DocType.MANUAL)
            for i in range(6)
        ]
    )
    store = _store_with_candidates(candidates)
    instance.predict.return_value = [float(i) for i in range(6)]
    settings = _settings(retrieval_top_k=3, retrieval_rerank_enabled=True)

    docs = retrieve("conteúdo médico", {DocType.MANUAL: store}, settings)

    assert len(docs) <= 3
    assert isinstance(docs, list)
    assert all(isinstance(d, Document) for d in docs)


# ---------------------------------------------------------------------------
# AC-11 / settings constraints (gt=0) + .env.example entries
# ---------------------------------------------------------------------------


def test_ac11_settings_constraints_and_env_example() -> None:
    """AC-11: as novas settings ``retrieval_rerank_*`` carregadas via
    pydantic-settings têm restrições de valor (``gt=0``) e entradas
    correspondentes em ``.env.example``."""
    # defaults
    s = _settings()
    assert s.retrieval_rerank_enabled is False
    assert s.retrieval_rerank_model == "BAAI/bge-reranker-base"
    assert s.retrieval_rerank_top_n == 20
    assert s.retrieval_rerank_batch_size == 16

    # gt=0: rejeita 0 e negativos
    for field in ("retrieval_rerank_top_n", "retrieval_rerank_batch_size"):
        for bad in (0, -1):
            with pytest.raises(ValidationError):
                _settings(**{field: bad})

    # .env.example documenta as 4 settings
    text = _ENV_EXAMPLE.read_text(encoding="utf-8")
    for key in (
        "RETRIEVAL_RERANK_ENABLED",
        "RETRIEVAL_RERANK_MODEL",
        "RETRIEVAL_RERANK_TOP_N",
        "RETRIEVAL_RERANK_BATCH_SIZE",
    ):
        assert f"{key}=" in text, f"{key} ausente em .env.example"


# ---------------------------------------------------------------------------
# AC-12 / shared model: loaded once (singleton) and reused, no reload per query
# ---------------------------------------------------------------------------


def test_ac12_singleton_loaded_once_and_reused(mock_cross_encoder) -> None:
    """AC-12: múltiplas queries com o modelo compartilhado carregam o reranker
    uma única vez e o reutilizam, sem recarregamento por query."""
    mock_cls, instance = mock_cross_encoder
    instance.predict.return_value = [0.1, 0.9, 0.5]
    candidates = _candidates(
        ("A", 0.1, DocType.BULA),
        ("B", 0.2, DocType.BULA),
        ("C", 0.3, DocType.BULA),
    )
    store = _store_with_candidates(candidates)
    settings = _settings(retrieval_rerank_enabled=True)

    for _ in range(5):
        retrieve("hipertensão", {DocType.BULA: store}, settings)

    # o modelo é instanciado apenas uma vez, reutilizado nas demais queries
    mock_cls.assert_called_once()
    assert instance.predict.call_count == 5


def test_ac12_singleton_thread_safe_loaded_once_under_contention(
    mocker: MagicMock,
) -> None:
    """AC-12 (borda): sob concorrência, o singleton thread-safe carrega o
    modelo uma única vez, mesmo com múltiplas queries simultâneas."""
    instance = MagicMock()
    instance.predict.return_value = [0.1, 0.9, 0.5]
    mock_cls = mocker.patch(
        "sentence_transformers.CrossEncoder", return_value=instance
    )
    candidates = _candidates(
        ("A", 0.1, DocType.BULA),
        ("B", 0.2, DocType.BULA),
        ("C", 0.3, DocType.BULA),
    )
    store = _store_with_candidates(candidates)
    settings = _settings(retrieval_rerank_enabled=True)
    n = 8
    barrier = threading.Barrier(n)

    def _worker() -> list[str]:
        barrier.wait()
        return _contents(retrieve("hipertensão", {DocType.BULA: store}, settings))

    with ThreadPoolExecutor(max_workers=n) as ex:
        results = list(ex.map(lambda _: _worker(), range(n)))

    mock_cls.assert_called_once()
    assert len(results) == n
    assert all(r == ["B", "C", "A"] for r in results)


# ---------------------------------------------------------------------------
# AC-13 / spec bonus: rerank runs AFTER the lexical guard
# ---------------------------------------------------------------------------


def test_ac13_rerank_runs_after_lexical_guard(
    mocker: MagicMock, mock_cross_encoder
) -> None:
    """AC-13: o rerank acontece DEPOIS do guarda lexical — quando o guarda
    esvazia a lista (cold start lexical), o reranker não é chamado."""
    _, instance = mock_cross_encoder
    # consulta menciona "dipirona" (sufixo 'ona'), mas nenhum chunk o contém
    candidates = _candidates(
        ("Conteúdo sobre outro fármaco sem o termo.", 0.1, DocType.BULA)
    )
    store = _store_with_candidates(candidates)
    settings = _settings(
        retrieval_rerank_enabled=True,
        retrieval_score_threshold=10.0,
    )

    docs = retrieve(
        "Qual a dose máxima de dipirona para adultos?",
        {DocType.BULA: store},
        settings,
    )

    assert docs == []
    instance.predict.assert_not_called()


# ---------------------------------------------------------------------------
# AC-14 / spec bonus: default rerank_top_n >= retrieval_top_k
# ---------------------------------------------------------------------------


def test_ac14_default_rerank_top_n_ge_retrieval_top_k() -> None:
    """AC-14: sem valor explícito de ``rerank_top_n``, o default é 20
    (>= ``retrieval_top_k``), limitando o batch enviado ao reranker."""
    s = _settings()  # retrieval_top_k=10, rerank_top_n default
    assert s.retrieval_rerank_top_n == 20
    assert s.retrieval_rerank_top_n >= s.retrieval_top_k
