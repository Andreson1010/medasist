"""Acceptance tests for RAG-03 (reescrita/expansão de consultas curtas).

Verifica a feature pelos pontos de entrada consumíveis ``retrieve()``,
``run_query()``, ``build_citations()`` e ``_collect_rows()``, com o LLM sempre
mockado (nunca rede real) e o ChromaDB real em ``tmp_path``. Nenhum arquivo de
``src/`` é modificado. Dados sintéticos (dipirona/ibuprofeno fictícios).

Cobertura por critério de aceitação do spec RAG-03 (RQ-03-01..RQ-03-11):

- RQ-03-01: flag on + consulta curta → LLM de reescrita expande e a consulta
  expandida é usada na busca (consulta curta sozinha seria cold start denso).
- RQ-03-02: geração e citações referenciam a pergunta original (não a expandida).
- RQ-03-03: flag off (default) → identidade, sem chamada ao LLM de reescrita.
- RQ-03-04: consulta em/acima do mínimo → verbatim, sem chamada extra ao LLM.
- RQ-03-05: falha do LLM de reescrita → consulta original, erro logado, sem propagar.
- RQ-03-06: saída vazia/whitespace/inválida → consulta original.
- RQ-03-07: stores vazio → [] e LLM de reescrita NUNCA chamado.
- RQ-03-08: expansão não recupera nada acima do threshold → [] (cold start),
  sem chamar o LLM de geração.
- RQ-03-09: comprimento limitado por max_output + prompt proíbe preâmbulo.
- RQ-03-10: caminho de avaliação (_collect_rows) exercita o mesmo retrieve/reescrita.
- RQ-03-11: novas settings documentadas em .env.example com defaults/constraints.
"""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import chromadb
import pytest
from langchain_core.embeddings import Embeddings
from langchain_core.messages import AIMessage
from pydantic import SecretStr

from medasist.config import Settings
from medasist.evaluation.dataset import GoldenQuestion
from medasist.generation.citations import CitationItem
from medasist.ingestion.schemas import DocType
from medasist.profiles.schemas import UserProfile
from medasist.retrieval.retriever import retrieve
from medasist.vectorstore.store import get_vectorstore

_REPO_ROOT = Path(__file__).resolve().parents[2]
_ENV_EXAMPLE = _REPO_ROOT / ".env.example"

_ADMIN_KEY = "very-strong-key-0123456789"

_EXPANDED = "Qual a dose de dipirona para adultos?"


class _LengthSensitiveEmbeddings(Embeddings):
    """Query longa casa densamente com os docs; query curta vira cold start.

    ``embed_query`` retorna vetor idêntico aos docs apenas quando a consulta
    tem 3+ palavras — assim "dipirona" (1 palavra) é cold start denso e a
    consulta expandida (ou uma com ``min_length`` tokens, ex: "dipirona febre
    dor") recupera o chunk, demonstrando o ganho da reescrita.
    """

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 1.0, 1.0, 1.0] for _ in texts]

    def embed_query(self, text: str) -> list[float]:
        if len(text.split()) >= 3:
            return [1.0, 1.0, 1.0, 1.0]
        return [0.0, 0.0, 0.0, 0.0]


class _DivergentEmbeddings(Embeddings):
    """Query vector sempre distante dos docs — denso vazio (L2 > threshold)."""

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 1.0, 1.0, 1.0] for _ in texts]

    def embed_query(self, text: str) -> list[float]:
        return [0.0, 0.0, 0.0, 0.0]


def _settings(**overrides: object) -> Settings:
    """Settings com reescrita habilitada e overrides por critério."""
    defaults: dict[str, object] = {
        "retrieval_top_k": 10,
        "retrieval_score_threshold": 0.4,
        "retrieval_query_rewrite_enabled": True,
        "retrieval_query_rewrite_min_length": 3,
    }
    defaults.update(overrides)
    return Settings(admin_api_key=SecretStr(_ADMIN_KEY), **defaults)


@pytest.fixture
def client(tmp_path) -> chromadb.ClientAPI:
    return chromadb.PersistentClient(path=str(tmp_path / "chroma"))


def _bula_store(
    client: chromadb.ClientAPI,
    embeddings: Embeddings,
    settings: Settings,
    text: str = "Bula de dipirona para dor intensa.",
) -> object:
    """Store BULA com um chunk de dipirona, usando embeddings dados."""
    store = get_vectorstore(DocType.BULA, client, embeddings, settings)
    store.add_texts(
        texts=[text],
        metadatas=[
            {
                "doc_type": "bula",
                "source_path": "bula_dipirona.pdf",
                "page": 2,
                "section": "Posologia",
            }
        ],
        ids=["bula_001"],
    )
    return store


def _patch_rewrite_llm(mocker, content: str = _EXPANDED) -> MagicMock:
    """Patcheia o ChatOpenAI da reescrita no local real do módulo."""
    instance = MagicMock()
    instance.return_value = AIMessage(content=content)
    mock_cls = mocker.patch(
        "medasist.retrieval.query_rewrite.ChatOpenAI", return_value=instance
    )
    return mock_cls


# ---------------------------------------------------------------------------
# RQ-03-01 / flag on + curta → expansão usada na busca
# ---------------------------------------------------------------------------


def test_ac_rq0301_short_query_expands_and_retrieves(mocker, client):
    """RQ-03-01: "dipirona" (curta, cold start denso sozinha) é expandida via LLM
    e a consulta expandida recupera o chunk da bula."""
    settings = _settings()
    store = _bula_store(client, _LengthSensitiveEmbeddings(), settings)
    _patch_rewrite_llm(mocker, _EXPANDED)

    # pré-condição: a consulta curta sozinha é cold start (sem reescrita)
    settings_off = _settings(retrieval_query_rewrite_enabled=False)
    assert retrieve("dipirona", {DocType.BULA: store}, settings_off) == []

    # com reescrita: a consulta expandida recupera o chunk
    docs = retrieve("dipirona", {DocType.BULA: store}, settings)

    assert len(docs) == 1
    assert docs[0].page_content == "Bula de dipirona para dor intensa."


def test_ac_rq0301_short_query_uses_expanded_query_in_search(mocker, client):
    """RQ-03-01 (interno): a busca por similaridade usa a consulta expandida."""
    settings = _settings()
    store = _bula_store(client, _LengthSensitiveEmbeddings(), settings)
    _patch_rewrite_llm(mocker, _EXPANDED)

    with patch.object(
        store, "similarity_search_with_score", wraps=store.similarity_search_with_score
    ) as spy:
        retrieve("dipirona", {DocType.BULA: store}, settings)
        assert spy.call_args.args[0] == _EXPANDED


# ---------------------------------------------------------------------------
# RQ-03-02 / geração e citações usam a pergunta original
# ---------------------------------------------------------------------------


def test_ac_rq0302_generation_and_citations_use_original_question(mocker, client):
    """RQ-03-02: run_query usa a consulta expandida no retrieval, mas o prompt de
    geração e as citações referenciam a pergunta original."""
    from medasist.generation.chain import run_query

    settings = _settings()
    store = _bula_store(client, _LengthSensitiveEmbeddings(), settings)
    stores = {DocType.BULA: store}
    _patch_rewrite_llm(mocker, _EXPANDED)

    gen_instance = MagicMock()
    gen_instance.return_value = AIMessage(content="A dose de dipirona é 500 mg [1].")
    gen_cls = MagicMock(return_value=gen_instance)
    with patch("medasist.generation.chain.ChatOpenAI", gen_cls):
        result = run_query("dipirona", stores, UserProfile.MEDICO, settings)

    assert result.is_cold_start is False
    assert len(result.citations) == 1
    assert isinstance(result.citations[0], CitationItem)
    assert result.citations[0].source == "bula_dipirona.pdf"
    assert result.citations[0].section == "Posologia"
    assert "[1]" in result.answer

    # o prompt de geração recebeu a pergunta ORIGINAL, não a expandida
    # (ChatPromptTemplate da chain produz um ChatPromptValue — convertido a
    # texto para a verificação)
    prompt_input = gen_instance.call_args.args[0]
    prompt_text = prompt_input.to_string()
    assert "dipirona" in prompt_text
    assert _EXPANDED not in prompt_text


# ---------------------------------------------------------------------------
# RQ-03-03 / flag off → identidade, sem LLM de reescrita
# ---------------------------------------------------------------------------


def test_ac_rq0303_flag_off_identity_no_llm(mocker, client):
    """RQ-03-03: flag off (default) → retrieve idêntico, sem chamada ao LLM."""
    settings = _settings(retrieval_query_rewrite_enabled=False)
    store = _bula_store(client, _LengthSensitiveEmbeddings(), settings)
    mock_llm = _patch_rewrite_llm(mocker, _EXPANDED)

    # flag off + consulta curta: usa a consulta original (cold start denso)
    docs = retrieve("dipirona", {DocType.BULA: store}, settings)

    assert docs == []
    mock_llm.assert_not_called()


# ---------------------------------------------------------------------------
# RQ-03-04 / consulta em/acima do mínimo → verbatim, sem LLM
# ---------------------------------------------------------------------------


def test_ac_rq0304_not_short_query_verbatim_no_llm(mocker, client):
    """RQ-03-04: consulta com tokens de conteúdo >= min_length → verbatim, sem LLM."""
    settings = _settings(retrieval_query_rewrite_min_length=3)
    store = _bula_store(client, _LengthSensitiveEmbeddings(), settings)
    mock_llm = _patch_rewrite_llm(mocker, _EXPANDED)

    # "dipirona febre dor" tem exatamente 3 tokens de conteúdo (limite estrito <)
    docs = retrieve("dipirona febre dor", {DocType.BULA: store}, settings)

    assert len(docs) == 1
    mock_llm.assert_not_called()


# ---------------------------------------------------------------------------
# RQ-03-05 / falha do LLM de reescrita → original, erro logado, sem propagar
# ---------------------------------------------------------------------------


def test_ac_rq0305_llm_failure_degrades_to_original(mocker, client, caplog):
    """RQ-03-05: falha/timeout do LLM de reescrita → consulta original, erro
    logado e exceção nunca propagada (retrieve continua normal)."""
    settings = _settings()
    store = _bula_store(client, _LengthSensitiveEmbeddings(), settings)

    instance = MagicMock()
    instance.side_effect = RuntimeError("LM Studio indisponível")
    mocker.patch("medasist.retrieval.query_rewrite.ChatOpenAI", return_value=instance)

    with caplog.at_level(logging.ERROR, logger="medasist.retrieval.query_rewrite"):
        docs = retrieve("dipirona", {DocType.BULA: store}, settings)

    assert docs == []
    assert any(
        r.levelno == logging.ERROR and "Query rewrite falhou" in r.getMessage()
        for r in caplog.records
    )


# ---------------------------------------------------------------------------
# RQ-03-06 / saída vazia/whitespace/inválida → consulta original
# ---------------------------------------------------------------------------


def test_ac_rq0306_invalid_output_falls_back_to_original(mocker, client):
    """RQ-03-06: LLM retorna saída vazia/inválida → consulta original (cold start)."""
    settings = _settings()
    store = _bula_store(client, _LengthSensitiveEmbeddings(), settings)

    for bad_output in ("", "   ", "!!! ... ???"):
        _patch_rewrite_llm(mocker, bad_output)
        docs = retrieve("dipirona", {DocType.BULA: store}, settings)
        assert docs == [], f"saída inválida {bad_output!r} deveria cair na original"


# ---------------------------------------------------------------------------
# RQ-03-07 / stores vazio → [] e LLM de reescrita nunca chamado
# ---------------------------------------------------------------------------


def test_ac_rq0307_empty_stores_no_rewrite(mocker, client):
    """RQ-03-07: stores vazio → retrieve [] antes de qualquer reescrita; o LLM de
    reescrita NUNCA é chamado."""
    settings = _settings()
    _patch_rewrite_llm(mocker, _EXPANDED)
    store = get_vectorstore(
        DocType.BULA, client, _LengthSensitiveEmbeddings(), settings
    )
    empty_stores = {DocType.BULA: store}

    mock_rw = mocker.patch("medasist.retrieval.retriever.rewrite_query")

    docs = retrieve("dipirona", empty_stores, settings)

    assert docs == []
    mock_rw.assert_not_called()


# ---------------------------------------------------------------------------
# RQ-03-08 / expansão não recupera nada → cold start, sem LLM de geração
# ---------------------------------------------------------------------------


def test_ac_rq0308_expansion_no_hit_is_cold_start(mocker, client):
    """RQ-03-08: mesmo com reescrita, se a expansão não recupera nada acima do
    threshold → [] e run_query devolve cold_start_message sem o LLM de geração."""
    from medasist.generation.chain import run_query

    settings = _settings()
    store = _bula_store(client, _DivergentEmbeddings(), settings)
    stores = {DocType.BULA: store}
    _patch_rewrite_llm(mocker, _EXPANDED)

    # retrieve: denso vazio mesmo expandido → cold start []
    assert retrieve("dipirona", stores, settings) == []

    # run_query: cold_start_message, LLM de geração NÃO chamado
    mock_gen = mocker.patch("medasist.generation.chain.ChatOpenAI")
    result = run_query("dipirona", stores, UserProfile.MEDICO, settings)

    assert result.is_cold_start is True
    assert result.citations == []
    assert result.answer == settings.cold_start_message
    mock_gen.assert_not_called()


# ---------------------------------------------------------------------------
# RQ-03-09 / comprimento limitado por max_output + prompt proíbe preâmbulo
# ---------------------------------------------------------------------------


def test_ac_rq0309_max_output_limits_rewritten_query_length(mocker, client):
    """RQ-03-09: a saída não-confiável do LLM é truncada e a busca por
    similaridade usa a consulta efetiva limitada a
    ``retrieval_query_rewrite_max_output`` caracteres."""
    settings = _settings(retrieval_query_rewrite_max_output=10)
    store = _bula_store(client, _LengthSensitiveEmbeddings(), settings)
    _patch_rewrite_llm(mocker, "x" * 500)

    # a busca por similaridade recebe a consulta efetiva truncada (<= max_output)
    with patch.object(
        store, "similarity_search_with_score", wraps=store.similarity_search_with_score
    ) as spy:
        docs = retrieve("dipirona", {DocType.BULA: store}, settings)
        effective_query = spy.call_args.args[0]

    assert effective_query == "x" * 10
    assert len(effective_query) <= settings.retrieval_query_rewrite_max_output
    # truncada (1 palavra) não recupera nada acima do threshold → cold start []
    assert docs == []


def test_ac_rq0309_expansion_prompt_forbids_preamble(client):
    """RQ-03-09: o prompt de expansão proíbe preâmbulo/cochicho."""
    from medasist.retrieval.query_rewrite import _EXPANSION_PROMPT

    template = _EXPANSION_PROMPT.template.lower()
    assert "preâmbulo" in template or "preambulo" in template
    assert "apenas" in template


# ---------------------------------------------------------------------------
# RQ-03-10 / caminho de avaliação usa o mesmo retrieve/reescrita (AD-011)
# ---------------------------------------------------------------------------


def test_ac_rq0310_eval_collect_rows_uses_rewrite_path(mocker, client):
    """RQ-03-10: o caminho de avaliação (_collect_rows) exercita o MESMO
    ``retrieve()`` com reescrita usado pela API (invariante AD-011): a pergunta
    curta "dipirona" é expandida pelo LLM de reescrita (mockado) e o contexto da
    avaliação vem da consulta expandida — nenhuma lógica de reescrita fora de
    ``retrieve()``. O ``run_query`` real roda, com o LLM de geração mockado."""
    from medasist.evaluation.metrics import _collect_rows

    settings = _settings()
    store = _bula_store(client, _LengthSensitiveEmbeddings(), settings)
    stores = {DocType.BULA: store}
    _patch_rewrite_llm(mocker, _EXPANDED)

    # mocka apenas o LLM de geração (nunca rede); o run_query real roda
    gen_instance = MagicMock()
    gen_instance.return_value = AIMessage(content="A dose de dipirona é 500 mg [1].")
    mocker.patch("medasist.generation.chain.ChatOpenAI", return_value=gen_instance)

    questions = [
        GoldenQuestion(
            question="dipirona",
            reference_answer="Resposta de referência.",
            reference_contexts=["Bula de dipirona para dor intensa."],
        )
    ]

    rows, cold_flags = _collect_rows(
        questions, stores, settings, UserProfile.MEDICO, None
    )

    assert cold_flags == [False]
    assert rows[0]["question"] == "dipirona"
    # o contexto da avaliação veio do retrieve() real, com reescrita aplicada
    # (a consulta curta sozinha seria cold start; a expandida recupera o chunk)
    assert rows[0]["contexts"] == ["Bula de dipirona para dor intensa."]


# ---------------------------------------------------------------------------
# RQ-03-11 / novas settings documentadas em .env.example
# ---------------------------------------------------------------------------


def test_ac_rq0311_env_example_documents_rewrite_settings():
    """RQ-03-11: .env.example documenta as settings de reescrita com defaults."""
    s = Settings(admin_api_key=SecretStr(_ADMIN_KEY))
    assert s.retrieval_query_rewrite_enabled is False
    assert s.retrieval_query_rewrite_min_length == 3
    assert s.retrieval_query_rewrite_model == s.lm_studio_llm_model
    assert s.retrieval_query_rewrite_temperature == 0.0
    assert s.retrieval_query_rewrite_max_tokens == 128
    assert s.retrieval_query_rewrite_max_output == 200

    text = _ENV_EXAMPLE.read_text(encoding="utf-8")
    for key in (
        "RETRIEVAL_QUERY_REWRITE_ENABLED",
        "RETRIEVAL_QUERY_REWRITE_MIN_LENGTH",
        "RETRIEVAL_QUERY_REWRITE_MODEL",
        "RETRIEVAL_QUERY_REWRITE_TEMPERATURE",
        "RETRIEVAL_QUERY_REWRITE_MAX_TOKENS",
        "RETRIEVAL_QUERY_REWRITE_MAX_OUTPUT",
    ):
        assert f"{key}=" in text, f"{key} ausente em .env.example"
