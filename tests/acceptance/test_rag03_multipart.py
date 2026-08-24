"""Acceptance tests for RAG-03 multi-parte (decomposição de perguntas compostas).

Verifica a feature pelos pontos de entrada consumíveis ``retrieve()``,
``run_query()``, ``stream_answer()`` e ``_collect_rows()``, com o LLM de split e
de geração sempre mockados via patch no local real (nunca rede) e o ChromaDB
real em ``tmp_path``. Nenhum arquivo de ``src/`` é modificado. Dados sintéticos
(bula de dipirona fictícia, Alphazol/Betazol fictícios).

Cobertura por critério de aceitação do spec RAG-03 multi-parte (MP-01..MP-14):

- MP-01: flag off → identidade total, sem chamada ao LLM de split.
- MP-02: flag on + pergunta composta → 2+ sub-perguntas, cada uma passando pelo
  funil ``retrieve()`` com o MESMO threshold L2 e a MESMA guarda lexical.
- MP-03: todas as sub-perguntas hit → validate_citations por sub, citações
  re-numeradas num espaço 1-based único e ``[N]`` remapeados no merged.
- MP-04: resposta merged com ≥1 citação válida
  ``[N] <nome_doc> — Seção: <seção>, Pág. <pág>`` + disclaimer médico.
- MP-05: cap ``retrieval_decompose_max_sub_questions`` (default 5) — 7 do LLM
  → processa só as 5 primeiras.
- MP-06: sub-pergunta curta + ``retrieval_query_rewrite_enabled=True`` → passa
  pela reescrita curta ANTES do retrieval (funnel independente por sub).
- MP-07: split falha/timeout/saída malformada/0 sub-perguntas → identidade,
  ``logger.exception``, NUNCA propaga.
- MP-08: exatamente 1 sub-pergunta → identidade, sem re-numeração indevida.
- MP-09: TODAS as sub-perguntas miss → cold start total.
- MP-10: ALGUMAS miss → hits no merged + ``unanswered_sub_questions``, sem
  fabricar conteúdo.
- MP-11: sub-resposta sem citação válida → tratada como miss, órfãos removidos
  antes da re-numeração.
- MP-12: ``_collect_rows`` (avaliação) percorre o MESMO caminho de ``run_query``
  com decomposição ativa (invariante AD-011).
- MP-13: sub-perguntas usadas APENAS como ``question`` de cada sub — nunca a
  lista de sub-perguntas/pergunta original composta/meta no prompt de geração.
- MP-14: ``stream_answer`` com decomposição → deltas concatenados = resposta
  merged, citações re-numeradas, cold start parcial.
"""

from __future__ import annotations

import logging
import re
from unittest.mock import MagicMock, patch

import chromadb
import pytest
from langchain_core.embeddings import Embeddings
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda
from pydantic import SecretStr

from medasist.config import Settings
from medasist.evaluation.dataset import GoldenQuestion
from medasist.evaluation.metrics import _collect_rows
from medasist.generation.chain import run_query, stream_answer
from medasist.generation.citations import CitationItem
from medasist.ingestion.schemas import DocType
from medasist.profiles.schemas import UserProfile
from medasist.retrieval.retriever import retrieve
from medasist.vectorstore.store import get_vectorstore

_ADMIN_KEY = "very-strong-key-0123456789"

# Corpus sintético — bula fictícia de dipirona (nunca dado real de paciente).
_CHUNK_DOSE = "A dose de dipirona é 500 mg por via oral."
_CHUNK_ALCOOL = "Não tomar dipirona com álcool durante o tratamento."
_SOURCE = "bula_dipirona.pdf"

_COMPOUND = "Qual a dose de dipirona e posso tomar com álcool?"
_SUB_DOSE = "Qual a dose de dipirona?"
_SUB_ALCOOL = "Posso tomar dipirona com álcool?"
_SUBS = [_SUB_DOSE, _SUB_ALCOOL]


class _TopicEmbeddings(Embeddings):
    """Embeddings sintéticos que separam os dois tópicos da bula fictícia.

    Chunk de dose → vetor A; chunk de interação com álcool → vetor B. Query
    com ``dose`` → A (recupera só o chunk de dose), query com ``álcool`` → B
    (recupera só o chunk de interação), demais queries → vetor D distante de
    ambos (cold start denso). Distância L2 entre vetores diferentes é
    ``sqrt(2) ≈ 1.41`` — acima do threshold default 0.4; entre iguais é 0.
    """

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [
            [1.0, 0.0, 0.0, 0.0] if "dose" in t.lower() else [0.0, 1.0, 0.0, 0.0]
            for t in texts
        ]

    def embed_query(self, text: str) -> list[float]:
        lower = text.lower()
        if "dose" in lower:
            return [1.0, 0.0, 0.0, 0.0]
        if "álcool" in lower or "alcool" in lower:
            return [0.0, 1.0, 0.0, 0.0]
        return [0.5, 0.5, 0.0, 0.0]


class _DivergentEmbeddings(Embeddings):
    """Query vector sempre distante dos docs — denso vazio (L2 > threshold)."""

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 1.0, 1.0, 1.0] for _ in texts]

    def embed_query(self, text: str) -> list[float]:
        return [0.0, 0.0, 0.0, 0.0]


class _LengthSensitiveEmbeddings(Embeddings):
    """Query com 3+ palavras casa com os docs; query curta vira cold start denso.

    Permite demonstrar a reescrita curta por sub-pergunta (MP-06): uma sub
    curta ("Posso beber?") sozinha é cold start; a reescrita expandida recupera
    o chunk.
    """

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 1.0, 1.0, 1.0] for _ in texts]

    def embed_query(self, text: str) -> list[float]:
        if len(text.split()) >= 3:
            return [1.0, 1.0, 1.0, 1.0]
        return [0.0, 0.0, 0.0, 0.0]


def _settings(**overrides: object) -> Settings:
    """Settings com decomposição habilitada e overrides por critério."""
    defaults: dict[str, object] = {
        "retrieval_top_k": 10,
        "retrieval_score_threshold": 0.4,
        "retrieval_query_rewrite_enabled": False,
        "retrieval_decompose_enabled": True,
        "retrieval_decompose_max_sub_questions": 5,
        "retrieval_decompose_min_tokens": 4,
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
    chunks: list[tuple[str, str, str]] | None = None,
) -> object:
    """Store BULA com chunks sintéticos de dipirona (texto, seção, página)."""
    if chunks is None:
        chunks = [
            (_CHUNK_DOSE, "Posologia", "2"),
            (_CHUNK_ALCOOL, "Interações", "4"),
        ]
    store = get_vectorstore(DocType.BULA, client, embeddings, settings)
    store.add_texts(
        texts=[text for text, _section, _page in chunks],
        metadatas=[
            {
                "doc_type": "bula",
                "source_path": _SOURCE,
                "page": page,
                "section": section,
            }
            for _text, section, page in chunks
        ],
        ids=[f"bula_{i:03d}" for i in range(len(chunks))],
    )
    return store


def _patch_split_llm(mocker, content: str) -> MagicMock:
    """Patcheia o ChatOpenAI do split no local real do módulo de decompose."""
    instance = MagicMock()
    instance.return_value = AIMessage(content=content)
    mock_cls = mocker.patch(
        "medasist.retrieval.decompose.ChatOpenAI", return_value=instance
    )
    return mock_cls


def _patch_gen_llm(mocker, answers: list[str]) -> tuple[MagicMock, MagicMock]:
    """Patcheia o ChatOpenAI de geração no local real da chain.

    ``instance`` retorna um ``AIMessage`` por chamada (``return_value`` quando
    há uma única resposta; ``side_effect`` quando há uma por sub-pergunta).
    Retorna ``(mock_cls, instance)`` para inspeção dos prompts de geração.
    """
    instance = MagicMock()
    if len(answers) == 1:
        instance.return_value = AIMessage(content=answers[0])
    else:
        instance.side_effect = [AIMessage(content=a) for a in answers]
    mock_cls = mocker.patch(
        "medasist.generation.chain.ChatOpenAI", return_value=instance
    )
    return mock_cls, instance


def _patch_rewrite_llm(mocker, content: str) -> MagicMock:
    """Patcheia o ChatOpenAI da reescrita curta no local real do módulo."""
    instance = MagicMock()
    instance.return_value = AIMessage(content=content)
    mock_cls = mocker.patch(
        "medasist.retrieval.query_rewrite.ChatOpenAI", return_value=instance
    )
    return mock_cls


def _consume(gen) -> tuple[list[str], tuple[list[CitationItem], bool]]:
    """Consome um gerador de ``stream_answer`` até o estado terminal.

    Returns
    -------
    tuple[list[str], tuple[list[CitationItem], bool]]
        ``(deltas, terminal)`` onde ``terminal`` é o valor de retorno do gerador.
    """
    deltas: list[str] = []
    terminal: tuple[list[CitationItem], bool] = ([], True)
    while True:
        try:
            deltas.append(next(gen))
        except StopIteration as stop:
            terminal = stop.value
            break
    return deltas, terminal


def _make_stream_llm(*deltas: str) -> RunnableLambda:
    """LLM fake cujo ``.stream`` yield deltas (mesmo padrão de test_chain.py)."""

    def fake_stream(input, config=None, **kwargs):  # type: ignore[no-untyped-def]
        yield from deltas

    return RunnableLambda(fake_stream)


# ---------------------------------------------------------------------------
# MP-01 / flag off → identidade total, sem split
# ---------------------------------------------------------------------------


def test_mp01_flag_off_identity_no_split(mocker, client):
    """MP-01: com ``retrieval_decompose_enabled=False`` (default) a pergunta é
    usada inalterada, SEM divisão, SEM chamada ao LLM de split e SEM merge —
    o retrieval consulta exatamente a pergunta original e o resultado é o
    caminho único atual (citações 1-based, sem ``unanswered_sub_questions``)."""
    settings = _settings(retrieval_decompose_enabled=False)
    store = _bula_store(client, _TopicEmbeddings(), settings)
    stores = {DocType.BULA: store}
    mock_split = mocker.patch("medasist.retrieval.decompose.ChatOpenAI")
    _patch_gen_llm(mocker, ["A dose de dipirona é 500 mg [1]."])

    with patch.object(
        store, "similarity_search_with_score", wraps=store.similarity_search_with_score
    ) as spy:
        result = run_query(_COMPOUND, stores, UserProfile.MEDICO, settings)

    mock_split.assert_not_called()
    assert result.is_cold_start is False
    assert result.answer == "A dose de dipirona é 500 mg [1]."
    assert [c.index for c in result.citations] == [1]
    assert result.unanswered_sub_questions == []
    # identidade: a busca usou a pergunta original, sem sub-divisão
    assert spy.call_count == 1
    assert spy.call_args.args[0] == _COMPOUND


# ---------------------------------------------------------------------------
# MP-02 / composta → 2+ sub-perguntas, cada uma no MESMO funil retrieve()
# ---------------------------------------------------------------------------


def test_mp02_compound_decomposes_and_each_sub_uses_funnel(mocker, client):
    """MP-02: flag on + pergunta composta → o split (mockado) divide em 2 sub-
    perguntas e EACH sub-pergunta passa pelo funil ``retrieve()`` com o mesmo
    threshold L2 e a mesma guarda lexical — a busca por similaridade consulta
    cada sub (nunca a composta) e cada parte recupera seu próprio chunk."""
    settings = _settings()
    store = _bula_store(client, _TopicEmbeddings(), settings)
    stores = {DocType.BULA: store}
    _patch_split_llm(mocker, "\n".join(_SUBS))
    _patch_gen_llm(
        mocker,
        ["Dose de dipirona: 500 mg [1].", "Evite álcool durante o tratamento [1]."],
    )

    with patch.object(
        store, "similarity_search_with_score", wraps=store.similarity_search_with_score
    ) as spy:
        result = run_query(_COMPOUND, stores, UserProfile.MEDICO, settings)

    queries = [call.args[0] for call in spy.call_args_list]
    assert queries == _SUBS  # 2 sub-perguntas, cada uma consultada no funil
    assert _COMPOUND not in queries  # a pergunta composta nunca é buscada inteira

    assert result.is_cold_start is False
    # cada sub hit com a própria citação (threshold L2 + guarda passaram)
    assert [c.index for c in result.citations] == [1, 2]
    assert result.citations[0].section == "Posologia"
    assert result.citations[1].section == "Interações"


def test_mp02_each_sub_passes_same_retrieve_with_guard(mocker, client):
    """MP-02 (guarda lexical): cada sub-pergunta passa pelo MESMO ``retrieve()``
    público — com o mesmo threshold L2 e a mesma guarda lexical das settings.
    A sub sobre dipirona recupera o chunk certo; uma sub sobre outra droga
    ausente do corpus é esvaziada pela guarda (miss segura, nunca contornada)."""
    settings = _settings()
    store = _bula_store(client, _TopicEmbeddings(), settings)
    stores = {DocType.BULA: store}

    # funil público por sub: mesmo settings, mesmo threshold, mesma guarda
    docs_dose = retrieve(_SUB_DOSE, stores, settings)
    docs_alcool = retrieve(_SUB_ALCOOL, stores, settings)
    assert [d.metadata["section"] for d in docs_dose] == ["Posologia"]
    assert [d.metadata["section"] for d in docs_alcool] == ["Interações"]

    # guarda lexical ativa dentro do funil de cada sub: droga ausente → cold
    # start (nunca responde usando chunk de outra droga)
    assert retrieve("Qual a dose de otraprofeno?", stores, settings) == []

    # e o run_query decomposto respeita a guarda por sub (a sub da outra droga
    # vira miss registrada, sem fabricar)
    _patch_split_llm(mocker, "Qual a dose de dipirona?\nQual a dose de otraprofeno?")
    _patch_gen_llm(mocker, ["Dose de dipirona: 500 mg [1]."])
    result = run_query(_COMPOUND, stores, UserProfile.MEDICO, settings)

    assert result.is_cold_start is False
    assert result.unanswered_sub_questions == ["Qual a dose de otraprofeno?"]
    assert result.answer == "Dose de dipirona: 500 mg [1]."


# ---------------------------------------------------------------------------
# MP-03 / todas hit → validate_citations + re-numeração 1-based + [N] remap
# ---------------------------------------------------------------------------


def test_mp03_all_hits_validated_and_renumbered(mocker, client):
    """MP-03: todas as sub-perguntas hit → cada sub-resposta passa por
    validate_citations (sem ``[N]`` órfão), as citações são re-numeradas num
    espaço 1-based único e os ``[N]`` são remapeados no texto merged."""
    settings = _settings()
    store = _bula_store(client, _TopicEmbeddings(), settings)
    stores = {DocType.BULA: store}
    _patch_split_llm(mocker, "\n".join(_SUBS))
    _patch_gen_llm(
        mocker,
        ["Dose de dipirona: 500 mg [1].", "Evite álcool durante o tratamento [1]."],
    )

    result = run_query(_COMPOUND, stores, UserProfile.MEDICO, settings)

    # [N] de cada sub remapeado por offset acumulado: [1] e [2] no texto merged
    assert result.answer == (
        "Dose de dipirona: 500 mg [1].\n\nEvite álcool durante o tratamento [2]."
    )
    assert [c.index for c in result.citations] == [1, 2]
    assert result.citations[0].source == _SOURCE
    assert result.citations[0].section == "Posologia"
    assert result.citations[1].source == _SOURCE
    assert result.citations[1].section == "Interações"

    # sem marcador órfão: todo [N] do merged tem CitationItem correspondente
    markers = [int(m) for m in re.findall(r"\[(\d+)\]", result.answer)]
    valid_indices = {c.index for c in result.citations}
    assert set(markers) <= valid_indices
    assert result.unanswered_sub_questions == []


def test_mp03_non_contiguous_citations_no_collision(mocker, client):
    """RAG-03 fix (HIGH): sub com citações NÃO-contíguas não colide no merge.

    Uma sub recupera 3 chunks mas o LLM cita apenas ``[1]`` e ``[3]`` (``[2]``
    não referenciada — ``validate_citations`` preserva os índices ORIGINAIS,
    deixando o conjunto {1, 3} não-contíguo). A re-numeração por deslocamento
    linear (``len``) faria a sub seguinte colidir num índice já usado. A
    re-numeração SEQUENCIAL via mapa produz índices únicos 1..M com ``[N]``
    apontando 1:1 para a fonte.
    """
    settings = _settings()
    chunks = [
        ("A dose inicial de dipirona é 500 mg.", "Posologia", "2"),
        ("A dose máxima de dipirona é 1000 mg.", "Posologia", "3"),
        ("Em idosos, reduzir a dose pela metade.", "Geriatria", "5"),
        ("Não tomar dipirona com álcool durante o tratamento.", "Interações", "4"),
    ]
    store = _bula_store(client, _TopicEmbeddings(), settings, chunks=chunks)
    stores = {DocType.BULA: store}
    _patch_split_llm(mocker, "\n".join(_SUBS))
    _patch_gen_llm(
        mocker,
        [
            "Dose inicial de dipirona: 500 mg [1]; em idosos reduzir a dose [3].",
            "Evite álcool durante o tratamento [1].",
        ],
    )

    result = run_query(_COMPOUND, stores, UserProfile.MEDICO, settings)

    assert result.is_cold_start is False
    # sub1 {1,3} → {1,2}; sub2 {1} → {3}: sem colisão, índices únicos 1..M
    assert result.answer == (
        "Dose inicial de dipirona: 500 mg [1]; em idosos reduzir a dose [2]."
        "\n\nEvite álcool durante o tratamento [3]."
    )
    indices = [c.index for c in result.citations]
    assert indices == [1, 2, 3]
    assert len(set(indices)) == 3  # sem colisão (regra 1:1 citação↔fonte)
    # cada [N] aponta 1:1 para a fonte certa (seções na ordem das sub)
    assert [c.section for c in result.citations] == [
        "Posologia",
        "Geriatria",
        "Interações",
    ]
    # todo [N] do texto tem CitationItem correspondente
    markers = [int(m) for m in re.findall(r"\[(\d+)\]", result.answer)]
    assert markers == indices


# ---------------------------------------------------------------------------
# MP-04 / merged com ≥1 citação válida + disclaimer médico
# ---------------------------------------------------------------------------


def test_mp04_merged_has_valid_citations_and_disclaimer(mocker, client):
    """MP-04: a resposta merged tem ≥1 citação válida no formato
    ``[N] <nome_doc> — Seção: <seção>, Pág. <pág>`` e inclui o disclaimer
    médico obrigatório."""
    settings = _settings()
    store = _bula_store(client, _TopicEmbeddings(), settings)
    stores = {DocType.BULA: store}
    _patch_split_llm(mocker, "\n".join(_SUBS))
    _patch_gen_llm(
        mocker,
        ["Dose de dipirona: 500 mg [1].", "Evite álcool durante o tratamento [1]."],
    )

    result = run_query(_COMPOUND, stores, UserProfile.MEDICO, settings)

    assert result.is_cold_start is False
    assert len(result.citations) >= 1
    for citation in result.citations:
        formatted = (
            f"[{citation.index}] {citation.source} — "
            f"Seção: {citation.section}, Pág. {citation.page}"
        )
        assert citation.source == _SOURCE
        assert citation.section in {"Posologia", "Interações"}
        assert citation.page in {"2", "4"}
        assert formatted.startswith(f"[{citation.index}] {_SOURCE} — Seção: ")

    assert "[1]" in result.answer and "[2]" in result.answer
    # disclaimer médico obrigatório (regra de segurança inegociável)
    assert result.disclaimer == settings.disclaimer
    assert "não substitui avaliação médica presencial" in result.disclaimer


# ---------------------------------------------------------------------------
# MP-05 / cap max_sub_questions (default 5)
# ---------------------------------------------------------------------------


def test_mp05_cap_five_sub_questions(mocker, client):
    """MP-05: quando o LLM de split retorna mais que
    ``retrieval_decompose_max_sub_questions`` (default 5), apenas as 5 primeiras
    são processadas e as demais descartadas (a busca consulta exatamente 5)."""
    default_settings = Settings(admin_api_key=SecretStr(_ADMIN_KEY))
    assert default_settings.retrieval_decompose_max_sub_questions == 5
    settings = _settings()  # max_sub_questions = 5
    store = _bula_store(client, _DivergentEmbeddings(), settings)
    stores = {DocType.BULA: store}
    _patch_split_llm(mocker, "\n".join(f"sub {i}" for i in range(7)))
    mock_gen = mocker.patch("medasist.generation.chain.ChatOpenAI")

    with patch.object(
        store, "similarity_search_with_score", wraps=store.similarity_search_with_score
    ) as spy:
        result = run_query(_COMPOUND, stores, UserProfile.MEDICO, settings)

    queries = [call.args[0] for call in spy.call_args_list]
    assert len(queries) == 5
    assert queries == [f"sub {i}" for i in range(5)]
    # as 6ª/7ª sub-perguntas nunca são consultadas
    assert "sub 5" not in queries and "sub 6" not in queries

    # todas as 5 são miss → cold start total e LLM de geração nunca chamado
    assert result.is_cold_start is True
    assert result.citations == []
    mock_gen.assert_not_called()


# ---------------------------------------------------------------------------
# MP-06 / sub curta + rewrite on → reescrita ANTES do retrieval
# ---------------------------------------------------------------------------


def test_mp06_short_sub_rewritten_before_retrieval(mocker, client):
    """MP-06: com ``retrieval_query_rewrite_enabled=True``, uma sub-pergunta
    curta passa pela reescrita de consulta curta ANTES do retrieval do próprio
    funil — a sub curta sozinha seria cold start denso; a reescrita (mockada)
    recupera o chunk e a parte entra no merged."""
    settings = _settings(
        retrieval_query_rewrite_enabled=True,
        retrieval_query_rewrite_min_length=3,
    )
    store = _bula_store(client, _LengthSensitiveEmbeddings(), settings)
    stores = {DocType.BULA: store}
    _patch_split_llm(mocker, "Qual a dose de dipirona para dor intensa?\nPosso beber?")
    _patch_rewrite_llm(mocker, "Posso beber com dipirona?")
    _patch_gen_llm(
        mocker,
        [
            "Dose de dipirona: 500 mg [1].",
            "Beber com dipirona não é recomendado [1].",
        ],
    )

    # pré-condição: "Posso beber?" sozinha é cold start denso (sem reescrita)
    assert retrieve("Posso beber?", stores, settings) != []

    with patch.object(
        store, "similarity_search_with_score", wraps=store.similarity_search_with_score
    ) as spy:
        result = run_query(_COMPOUND, stores, UserProfile.MEDICO, settings)

    queries = [call.args[0] for call in spy.call_args_list]
    # a sub curta foi reescrita ANTES do retrieval do seu funil
    assert "Posso beber com dipirona?" in queries
    assert "Posso beber?" not in queries

    # a parte da sub curta está no merged (não virou cold start)
    assert result.is_cold_start is False
    assert "Beber com dipirona não é recomendado [2]." in result.answer
    assert [c.index for c in result.citations] == [1, 2]


# ---------------------------------------------------------------------------
# MP-07 / split falha/timeout/malformado/0 → identidade, logger.exception,
#         NUNCA propaga
# ---------------------------------------------------------------------------


def test_mp07_split_failure_identity_logs_no_propagate(mocker, client, caplog):
    """MP-07: falha/timeout do LLM de split → a pergunta original é usada
    (identidade), o erro é logado com ``logger.exception`` e NUNCA propaga."""
    settings = _settings()
    store = _bula_store(client, _TopicEmbeddings(), settings)
    stores = {DocType.BULA: store}
    instance = MagicMock()
    instance.side_effect = RuntimeError("LM Studio indisponível")
    mocker.patch("medasist.retrieval.decompose.ChatOpenAI", return_value=instance)
    _patch_gen_llm(mocker, ["A dose de dipirona é 500 mg [1]."])

    with (
        caplog.at_level(logging.ERROR, logger="medasist.retrieval.decompose"),
        patch.object(
            store,
            "similarity_search_with_score",
            wraps=store.similarity_search_with_score,
        ) as spy,
    ):
        result = run_query(_COMPOUND, stores, UserProfile.MEDICO, settings)

    # identidade: a busca usou a pergunta ORIGINAL (sem sub-divisão)
    assert spy.call_count == 1
    assert spy.call_args.args[0] == _COMPOUND
    assert result.is_cold_start is False
    assert [c.index for c in result.citations] == [1]
    # logger.exception foi chamado
    assert any(
        r.levelno == logging.ERROR and "Decomposição falhou" in r.getMessage()
        for r in caplog.records
    )


@pytest.mark.parametrize(
    "bad_output", ["", "   ", "!!!", "apenas lixo sem pergunta válida"]
)
def test_mp07_malformed_or_empty_output_identity(mocker, client, bad_output):
    """MP-07: saída malformada/0 sub-perguntas do split → identidade (pergunta
    original usada no retrieval) e a exceção nunca é propagada."""
    settings = _settings()
    store = _bula_store(client, _TopicEmbeddings(), settings)
    stores = {DocType.BULA: store}
    _patch_split_llm(mocker, bad_output)
    _patch_gen_llm(mocker, ["A dose de dipirona é 500 mg [1]."])

    with patch.object(
        store, "similarity_search_with_score", wraps=store.similarity_search_with_score
    ) as spy:
        result = run_query(_COMPOUND, stores, UserProfile.MEDICO, settings)

    assert spy.call_args.args[0] == _COMPOUND  # identidade
    assert result.is_cold_start is False
    assert result.answer == "A dose de dipirona é 500 mg [1]."
    assert [c.index for c in result.citations] == [1]
    assert result.unanswered_sub_questions == []


# ---------------------------------------------------------------------------
# MP-08 / exatamente 1 sub-pergunta → identidade, sem re-numeração indevida
# ---------------------------------------------------------------------------


def test_mp08_single_sub_identity_no_renumber(mocker, client):
    """MP-08: quando o split retorna exatamente 1 sub-pergunta, a pergunta é
    processada como única (identidade) — o retrieval usa a pergunta ORIGINAL e
    não há re-numeração indevida (marcadores continuam ``[1]``)."""
    settings = _settings()
    store = _bula_store(client, _TopicEmbeddings(), settings)
    stores = {DocType.BULA: store}
    _patch_split_llm(mocker, _SUB_DOSE)
    _patch_gen_llm(mocker, ["A dose de dipirona é 500 mg [1]."])

    with patch.object(
        store, "similarity_search_with_score", wraps=store.similarity_search_with_score
    ) as spy:
        result = run_query(_COMPOUND, stores, UserProfile.MEDICO, settings)

    assert spy.call_count == 1
    assert spy.call_args.args[0] == _COMPOUND  # identidade: pergunta original
    assert result.answer == "A dose de dipirona é 500 mg [1]."
    assert [c.index for c in result.citations] == [1]  # sem re-numeração
    assert result.unanswered_sub_questions == []


# ---------------------------------------------------------------------------
# MP-09 / TODAS as sub-perguntas miss → cold start total
# ---------------------------------------------------------------------------


def test_mp09_all_miss_cold_start_total(mocker, client):
    """MP-09: TODAS as sub-perguntas miss (retrieval vazio) → cold start total:
    ``is_cold_start=True``, resposta fixa, citações vazias e LLM de geração
    NUNCA chamado (zero geração, zero fabricação)."""
    settings = _settings()
    store = _bula_store(client, _DivergentEmbeddings(), settings)
    stores = {DocType.BULA: store}
    mock_split = _patch_split_llm(mocker, "\n".join(_SUBS))
    mock_gen = mocker.patch("medasist.generation.chain.ChatOpenAI")

    result = run_query(_COMPOUND, stores, UserProfile.MEDICO, settings)

    mock_split.assert_called()  # a decomposição aconteceu
    assert result.is_cold_start is True
    assert result.answer == settings.cold_start_message
    assert result.citations == []
    assert result.unanswered_sub_questions == []
    mock_gen.assert_not_called()


# ---------------------------------------------------------------------------
# MP-10 / ALGUMAS miss → hits no merged + unanswered_sub_questions, sem fabricar
# ---------------------------------------------------------------------------


def test_mp10_some_miss_unanswered_no_fabrication(mocker, client):
    """MP-10: ALGUMAS sub-perguntas miss → os hits entram no merged e os textos
    das misses são registrados em ``unanswered_sub_questions``, sem fabricar
    conteúdo para as partes não respondidas."""
    settings = _settings()
    store = _bula_store(client, _TopicEmbeddings(), settings)
    stores = {DocType.BULA: store}
    _patch_split_llm(mocker, f"{_SUB_DOSE}\nPosso beber?")
    _patch_gen_llm(mocker, ["Dose de dipirona: 500 mg [1]."])

    result = run_query(_COMPOUND, stores, UserProfile.MEDICO, settings)

    assert result.is_cold_start is False
    assert result.answer == "Dose de dipirona: 500 mg [1]."
    assert [c.index for c in result.citations] == [1]
    # a miss é registrada com o TEXTO da sub-pergunta — nada é fabricado
    assert result.unanswered_sub_questions == ["Posso beber?"]
    assert "Posso beber?" not in result.answer


# ---------------------------------------------------------------------------
# MP-11 / sub-resposta sem citação válida → miss, órfãos removidos antes da
#         re-numeração
# ---------------------------------------------------------------------------


def test_mp11_sub_without_valid_citation_is_miss(mocker, client):
    """MP-11: uma sub-pergunta que hit no retrieval mas cuja geração não produz
    citação válida (marcador ``[9]`` alucinado, sem CitationItem) é tratada como
    miss — o marcador órfão é removido ANTES da re-numeração e a sub não entra
    no merged."""
    settings = _settings()
    store = _bula_store(client, _TopicEmbeddings(), settings)
    stores = {DocType.BULA: store}
    _patch_split_llm(mocker, "\n".join(_SUBS))
    _patch_gen_llm(
        mocker,
        ["Dose de dipirona: 500 mg [1].", "Alguma interação com álcool [9]."],
    )

    result = run_query(_COMPOUND, stores, UserProfile.MEDICO, settings)

    assert result.is_cold_start is False
    assert result.answer == "Dose de dipirona: 500 mg [1]."
    assert "[9]" not in result.answer  # órfão removido antes da re-numeração
    assert [c.index for c in result.citations] == [1]
    assert result.unanswered_sub_questions == [_SUB_ALCOOL]


# ---------------------------------------------------------------------------
# MP-12 / _collect_rows (avaliação) percorre o MESMO caminho de run_query
# ---------------------------------------------------------------------------


def test_mp12_collect_rows_uses_same_decompose_path(mocker, client):
    """MP-12: o caminho de avaliação ``_collect_rows`` exercita o MESMO
    ``run_query`` com decomposição ativa (invariante AD-011) — nenhuma lógica de
    decomposição fora do layer ``run_query``. A pergunta composta é decomposta e
    a linha da avaliação carrega a resposta merged das duas partes."""
    settings = _settings()
    store = _bula_store(client, _TopicEmbeddings(), settings)
    stores = {DocType.BULA: store}
    _patch_split_llm(mocker, "\n".join(_SUBS))
    _patch_gen_llm(
        mocker,
        ["Dose de dipirona: 500 mg [1].", "Evite álcool durante o tratamento [1]."],
    )

    questions = [
        GoldenQuestion(
            question=_COMPOUND,
            reference_answer="Dose de dipirona e interação com álcool.",
            reference_contexts=[_CHUNK_DOSE, _CHUNK_ALCOOL],
        )
    ]

    rows, cold_flags = _collect_rows(
        questions, stores, settings, UserProfile.MEDICO, None
    )

    assert cold_flags == [False]
    # a resposta veio do run_query REAL decomposto (merged das duas partes)
    assert "Dose de dipirona: 500 mg [1]." in rows[0]["answer"]
    assert "Evite álcool durante o tratamento [2]." in rows[0]["answer"]
    # contexts vieram do retrieve real do mesmo pipeline (AD-011)
    assert rows[0]["contexts"] == [_CHUNK_DOSE]


# ---------------------------------------------------------------------------
# MP-13 / sub-perguntas usadas APENAS como question de cada sub
# ---------------------------------------------------------------------------


def test_mp13_subs_only_as_each_sub_question(mocker, client):
    """MP-13: cada sub-pergunta é usada APENAS como ``question`` do seu próprio
    funil — o prompt de geração de cada sub contém somente a sub-pergunta em
    questão, nunca a lista de sub-perguntas, nunca a pergunta original composta
    e nenhuma meta de decomposição."""
    settings = _settings()
    store = _bula_store(client, _TopicEmbeddings(), settings)
    stores = {DocType.BULA: store}
    _patch_split_llm(mocker, "\n".join(_SUBS))
    _, gen_instance = _patch_gen_llm(
        mocker,
        ["Dose de dipirona: 500 mg [1].", "Evite álcool durante o tratamento [1]."],
    )

    run_query(_COMPOUND, stores, UserProfile.MEDICO, settings)

    prompts = [call.args[0].to_string() for call in gen_instance.call_args_list]
    assert len(prompts) == 2
    # prompt da sub 1: contém só a sub 1 como pergunta
    assert _SUB_DOSE in prompts[0]
    assert _SUB_ALCOOL not in prompts[0]
    assert _COMPOUND not in prompts[0]
    # prompt da sub 2: contém só a sub 2 como pergunta
    assert _SUB_ALCOOL in prompts[1]
    assert _SUB_DOSE not in prompts[1]
    assert _COMPOUND not in prompts[1]
    # nenhuma meta de decomposição interpolada
    for prompt in prompts:
        assert "sub-pergunta" not in prompt.lower()
        assert "decompos" not in prompt.lower()
        assert "pergunta composta" not in prompt.lower()


# ---------------------------------------------------------------------------
# MP-14 / stream_answer com decomposição → deltas = merged, citações
#         re-numeradas, cold start parcial
# ---------------------------------------------------------------------------


def test_mp14_stream_deltas_merge_and_renumber(mocker, client):
    """MP-14: ``stream_answer`` com decomposição gera deltas por sub-pergunta
    pelo mesmo funil do ``run_query``; o texto streamado (deltas concatenados)
    coincide com o ``answer`` merged síncrono — mesmo separador ``\\n\\n`` e
    marcadores ``[N]`` remapeados — e o terminal retorna as citações
    re-numeradas 1-based."""
    settings = _settings()
    store = _bula_store(client, _TopicEmbeddings(), settings)
    stores = {DocType.BULA: store}
    _patch_split_llm(mocker, "\n".join(_SUBS))
    mocker.patch(
        "medasist.generation.chain.ChatOpenAI",
        side_effect=[
            _make_stream_llm("Dose de dipirona: 500 mg [1]."),
            _make_stream_llm("Evite álcool durante o tratamento [1]."),
        ],
    )

    gen = stream_answer(_COMPOUND, stores, UserProfile.MEDICO, settings)
    deltas, (citations, is_cold_start) = _consume(gen)

    # deltas concatenados = resposta merged síncrona (separador + remap)
    assert "".join(deltas) == (
        "Dose de dipirona: 500 mg [1].\n\n" "Evite álcool durante o tratamento [2]."
    )
    assert is_cold_start is False
    # citações re-numeradas no espaço 1-based único
    assert [c.index for c in citations] == [1, 2]
    assert citations[0].source == _SOURCE
    assert citations[0].section == "Posologia"
    assert citations[1].source == _SOURCE
    assert citations[1].section == "Interações"


def test_mp14_stream_partial_cold_start(mocker, client):
    """MP-14 (política parcial): com uma sub miss e outra hit, ``stream_answer``
    emite deltas apenas do hit, retorna a citação re-numerada e ``False`` de
    cold start (nunca fabrica o conteúdo da parte miss)."""
    settings = _settings()
    store = _bula_store(client, _TopicEmbeddings(), settings)
    stores = {DocType.BULA: store}
    _patch_split_llm(mocker, f"{_SUB_DOSE}\nPosso beber?")
    mocker.patch(
        "medasist.generation.chain.ChatOpenAI",
        side_effect=[_make_stream_llm("Dose de dipirona: 500 mg [1].")],
    )

    gen = stream_answer(_COMPOUND, stores, UserProfile.MEDICO, settings)
    deltas, (citations, is_cold_start) = _consume(gen)

    assert "".join(deltas) == "Dose de dipirona: 500 mg [1]."
    assert is_cold_start is False
    assert [c.index for c in citations] == [1]
