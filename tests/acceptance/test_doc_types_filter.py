from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from medasist.generation.chain import GenerationResult
from medasist.generation.citations import CitationItem
from medasist.ingestion.schemas import DocType
from medasist.profiles.schemas import UserProfile

"""Acceptance tests for FIX-03 (doc_types filter on POST /query).

Verifies the feature from the outside, through the HTTP contract of
``POST /query``. The Chroma/LLM layer is replaced by a mocked chain so the
tests exercise validation, routing of ``doc_types`` into the chain, and the
scoping/cold-start/disclaimer semantics surfaced by the API given documented
chain behaviour. No ``src/`` file is modified.

Happy path (answer), failure path (422 / cold start) and boundary
(``doc_types`` omitted / ``[]`` / ``null``) are covered per criterion below.
"""

logger = logging.getLogger(__name__)

DISCLAIMER = (
    "Este sistema e um auxiliar informativo e nao substitui "
    "avaliacao medica presencial."
)

COLD_START_MESSAGE = "Nao encontrei informacoes relevantes para sua pergunta."

_SOURCE_BY_DOC: dict[DocType, str] = {
    DocType.BULA: "bula_amoxicilina.pdf",
    DocType.DIRETRIZ: "diretriz_dm2.pdf",
    DocType.PROTOCOLO: "protocolo_hpv.pdf",
    DocType.MANUAL: "manual_insulina.pdf",
}

_QUESTION = "Alguma pergunta de aceitacao?"

_PERFIL = UserProfile.MEDICO


def _make_result(
    doc_type: DocType,
    answer: str,
    *,
    is_cold_start: bool = False,
    include_citation: bool = True,
) -> GenerationResult:
    """Constrói GenerationResult com fonte coerente com um DocType.

    Parameters
    ----------
    doc_type : DocType
        Tipo de documento ao qual a citação (se presente) pertence.
    answer : str
        Texto da resposta.
    is_cold_start : bool
        Se ``True`` a resposta é uma mensagem fixa sem citações.
    include_citation : bool
        Se incluir uma citação ``[1]`` válida (ignorado em cold start).

    Returns
    -------
    GenerationResult
        Resultado sintético coerente com o tipo pedido.
    """
    if is_cold_start:
        return GenerationResult(
            answer=COLD_START_MESSAGE,
            citations=[],
            profile=_PERFIL,
            disclaimer=DISCLAIMER,
            is_cold_start=True,
        )
    citations = []
    if include_citation:
        citations.append(
            CitationItem(
                index=1,
                source=_SOURCE_BY_DOC[doc_type],
                section="Posologia",
                page="3",
            )
        )
    return GenerationResult(
        answer=answer,
        citations=citations,
        profile=_PERFIL,
        disclaimer=DISCLAIMER,
        is_cold_start=False,
    )


def _fixed_chain(result: GenerationResult) -> MagicMock:
    """Retorna chain mock que sempre devolve o mesmo resultado.

    Parameters
    ----------
    result : GenerationResult
        Resultado fixo a retornar em toda chamada.

    Returns
    -------
    MagicMock
        Chain cujo retorno é fixado em ``result``.
    """
    chain = MagicMock()
    chain.return_value = result
    return chain


def _doc_type_aware_chain() -> MagicMock:
    """Retorna chain mock que devolve resultado conforme o doc_types pedido.

    A resposta e a citação refletem o primeiro ``DocType`` pedido, permitindo
    provar que o mesmo objeto de chain não guarda estado entre requests e que
    o ``doc_types`` de cada request é repassado.

    Returns
    -------
    MagicMock
        Chain que inspeciona o argumento ``doc_types``.
    """
    chain = MagicMock()

    def _run(question: str, doc_types: list[DocType] | None = None) -> GenerationResult:
        dt = doc_types[0] if doc_types else DocType.BULA
        return _make_result(dt, f"Resposta para {dt.value}")

    chain.side_effect = _run
    return chain


@contextmanager
def _client(chain: MagicMock) -> Iterator[TestClient]:
    """Constrói TestClient com lifespan mockado e chain customizada.

    Patching espelha ``tests/api/conftest.py`` (nada de ChromaDB nem LLM real).

    Parameters
    ----------
    chain : MagicMock
        Chain a injetar para todos os perfis.

    Yields
    ------
    TestClient
        Cliente de teste com a app já inicializada.
    """
    chains = dict.fromkeys(UserProfile, chain)
    with (
        patch("medasist.api.main.get_all_vectorstores", return_value={}),
        patch(
            "medasist.api.main.build_chain",
            side_effect=lambda stores, profile, settings: chains[profile],
        ),
    ):
        from medasist.api.main import app

        with TestClient(app) as c:
            yield c


def _payload(
    question: str = _QUESTION,
    profile: str = "medico",
    doc_types: list[str] | None | object = ...,
) -> dict:
    """Monta payload JSON opcional incorporando doc_types.

    Parameters
    ----------
    question : str
        Pergunta.
    profile : str
        Perfil (valor string do enum).
    doc_types : list[str] | None | object
        Valor de ``doc_types``. ``...`` (default) omite o campo.

    Returns
    -------
    dict
        Payload para ``POST /query``.
    """
    body: dict = {"question": question, "profile": profile}
    if doc_types is not ...:
        body["doc_types"] = doc_types
    return body


# ---------------------------------------------------------------------------
# DTF-01 / HP1
# ---------------------------------------------------------------------------


def test_HP1_subset_question_only_in_excluded_types_returns_cold_start() -> None:
    """DTF-01/HP1: subset + pergunta só respondível em tipos excluídos → cold
    start, sem resposta."""
    chain = _fixed_chain(
        _make_result(DocType.BULA, COLD_START_MESSAGE, is_cold_start=True)
    )
    with _client(chain) as c:
        resp = c.post("/query", json=_payload(doc_types=["bula"]))
    assert resp.status_code == 200
    body = resp.json()
    assert body["is_cold_start"] is True
    assert body["answer"] == COLD_START_MESSAGE
    assert body["citations"] == []
    chain.assert_called_once_with(_QUESTION, [DocType.BULA])


def test_HP1_boundary_empty_filtered_subset_is_cold_start_not_error() -> None:
    """DTF-01/HP1 (falha/borda): subset pedido sem chunks → cold start, não erro."""
    chain = _fixed_chain(
        _make_result(DocType.BULA, COLD_START_MESSAGE, is_cold_start=True)
    )
    with _client(chain) as c:
        resp = c.post("/query", json=_payload(doc_types=["manual"]))
    assert resp.status_code == 200
    assert resp.json()["is_cold_start"] is True


# ---------------------------------------------------------------------------
# DTF-02 / HP2
# ---------------------------------------------------------------------------


def test_HP2_doc_types_bula_question_elsewhere_cold_start_no_citations() -> None:
    """DTF-02/HP2: ``doc_types=["bula"]`` e pergunta respondível apenas em
    outros tipos → cold start e nenhuma citação."""
    chain = _fixed_chain(
        _make_result(DocType.BULA, COLD_START_MESSAGE, is_cold_start=True)
    )
    with _client(chain) as c:
        resp = c.post("/query", json=_payload(doc_types=["bula"]))
    assert resp.status_code == 200
    body = resp.json()
    assert body["is_cold_start"] is True
    assert body["citations"] == []
    chain.assert_called_once_with(_QUESTION, [DocType.BULA])


# ---------------------------------------------------------------------------
# DTF-03 / HP3
# ---------------------------------------------------------------------------


def test_HP3_doc_types_bula_answerable_returns_answer_citations_resolve_to_bula() -> (
    None
):
    """DTF-03/HP3: ``doc_types=["bula"]`` com pergunta respondível em bula →
    resposta e todas as citações resolvem apenas para bula."""
    chain = _fixed_chain(
        _make_result(
            DocType.BULA, "A dose recomendada e 500 mg [1].", include_citation=True
        )
    )
    with _client(chain) as c:
        resp = c.post("/query", json=_payload(doc_types=["bula"]))
    assert resp.status_code == 200
    body = resp.json()
    assert body["answer"] == "A dose recomendada e 500 mg [1]."
    assert body["is_cold_start"] is False
    assert body["citations"], "resposta deve citar ao menos uma fonte"
    sources = {cit["source"] for cit in body["citations"]}
    assert sources == {"bula_amoxicilina.pdf"}
    chain.assert_called_once_with(_QUESTION, [DocType.BULA])


# ---------------------------------------------------------------------------
# DTF-04 / HP4
# ---------------------------------------------------------------------------


def test_HP4_doc_types_omitted_queries_all_identical_default() -> None:
    """DTF-04/HP4: ``doc_types`` omitido → consulta todas as coleções e é
    idêntico ao comportamento default."""
    default = _make_result(DocType.BULA, "Resposta default [1].", include_citation=True)
    chain = _fixed_chain(default)
    with _client(chain) as c:
        first = c.post("/query", json=_payload()).json()
        second = c.post("/query", json=_payload()).json()
    assert first == second
    assert first["is_cold_start"] is False
    assert chain.call_count == 2
    assert chain.call_args_list == [((_QUESTION, None),), ((_QUESTION, None),)]


# ---------------------------------------------------------------------------
# DTF-05 / HP5
# ---------------------------------------------------------------------------


def test_HP5_doc_types_empty_list_queries_all() -> None:
    """DTF-05/HP5: ``doc_types=[]`` → consulta todas as coleções (não vazio)."""
    chain = _fixed_chain(
        _make_result(DocType.BULA, "Resposta all [1].", include_citation=True)
    )
    with _client(chain) as c:
        resp = c.post("/query", json=_payload(doc_types=[]))
    assert resp.status_code == 200
    assert resp.json()["is_cold_start"] is False
    assert len(resp.json()["citations"]) == 1
    chain.assert_called_once_with(_QUESTION, [])


# ---------------------------------------------------------------------------
# DTF-06 / HP6
# ---------------------------------------------------------------------------


def test_HP6_doc_types_null_queries_all() -> None:
    """DTF-06/HP6: ``doc_types=null`` → consulta todas as coleções."""
    chain = _fixed_chain(
        _make_result(DocType.BULA, "Resposta null [1].", include_citation=True)
    )
    with _client(chain) as c:
        resp = c.post("/query", json=_payload(doc_types=None))
    assert resp.status_code == 200
    assert resp.json()["is_cold_start"] is False
    chain.assert_called_once_with(_QUESTION, None)


# ---------------------------------------------------------------------------
# DTF-07 / HP7
# ---------------------------------------------------------------------------


def test_HP7_sequential_bula_then_protocolo_second_returns_only_protocolo() -> None:
    """DTF-07/HP7: requests sequenciais ``["bula"]`` e ``["protocolo"]`` na
    mesma chain → segundo devolve apenas PROTOCOLO (chain compartilhada não
    foi mutada)."""
    chain = _doc_type_aware_chain()
    with _client(chain) as c:
        r1 = c.post("/query", json=_payload(doc_types=["bula"]))
        r2 = c.post("/query", json=_payload(doc_types=["protocolo"]))
    assert r1.status_code == 200
    assert r2.status_code == 200

    b1 = r1.json()
    b2 = r2.json()
    assert {cit["source"] for cit in b1["citations"]} == {"bula_amoxicilina.pdf"}
    assert {cit["source"] for cit in b2["citations"]} == {"protocolo_hpv.pdf"}
    assert "protocolo" in b2["answer"]
    assert "bula" not in b2["answer"]

    assert chain.call_args_list == [
        ((_QUESTION, [DocType.BULA]),),
        ((_QUESTION, [DocType.PROTOCOLO]),),
    ]


def test_HP7_boundary_same_chain_survives_three_distinct_types() -> None:
    """DTF-07/HP7 (borda): mesma chain com três tipos distintos e sequenciais
    nunca retorna resultado de um request anterior."""
    chain = _doc_type_aware_chain()
    with _client(chain) as c:
        r1 = c.post("/query", json=_payload(doc_types=["diretriz"]))
        r2 = c.post("/query", json=_payload(doc_types=["bula"]))
        r3 = c.post("/query", json=_payload(doc_types=["manual"]))
    sources = [{cit["source"] for cit in r.json()["citations"]} for r in (r1, r2, r3)]
    assert sources == [
        {"diretriz_dm2.pdf"},
        {"bula_amoxicilina.pdf"},
        {"manual_insulina.pdf"},
    ]


# ---------------------------------------------------------------------------
# DTF-08 / FR1
# ---------------------------------------------------------------------------


def test_FR1_invalid_non_doctype_returns_422() -> None:
    """DTF-08/FR1: valor inválido (ex.: ``"PACIENTE"``) em ``doc_types`` →
    HTTP 422 pela validação de enum do Pydantic."""
    chain = _fixed_chain(_make_result(DocType.BULA, "x"))
    with _client(chain) as c:
        resp = c.post("/query", json=_payload(doc_types=["PACIENTE"]))
    assert resp.status_code == 422
    chain.assert_not_called()


def test_FR1_boundary_partial_invalid_list_returns_422_no_response() -> None:
    """DTF-08/FR1 (borda): lista com um valor válido e um inválido é rejeitada
    por completo (422); nenhuma consulta é feita."""
    chain = _fixed_chain(_make_result(DocType.BULA, "x"))
    with _client(chain) as c:
        resp = c.post("/query", json=_payload(doc_types=["bula", "PACIENTE"]))
    assert resp.status_code == 422
    chain.assert_not_called()


def test_FR1_boundary_empty_question_still_422_by_length() -> None:
    """DTF-08/FR1 (borda): questão vazia continua dando 422 (min_length), não
    relacionado ao filtro."""
    chain = _fixed_chain(_make_result(DocType.BULA, "x"))
    with _client(chain) as c:
        resp = c.post("/query", json=_payload(question="", doc_types=["bula"]))
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# DTF-09 / FR2 (concurrency)
# ---------------------------------------------------------------------------


def test_FR2_concurrency_isolation_coverage_note() -> None:
    """DTF-09/FR2: isolamento de requests com doc_types distintos.

    COBERTURA INDIRETA: não é um teste real de concorrência. Via HTTP puro com
    chain mockada é impossível provar ausência de corrida sobre o dict
    compartilhado de stores (o slicing ocorre dentro de ``run_query``, fora do
    contrato HTTP). O substituto observável é: (a) HP7 prova que duas chamadas
    sequenciais à MESMA instância de chain com doc_types distintos produzem
    resultados escopados a cada um; (b) cada request novo repassa seu próprio
    doc_types (assert em call_args). Sem estar sob montagem de mock de
    ``run_query``/stores, corridas reais de dicionário não são detectáveis.
    Aqui apenas reforçamos que o segundo request não herda o filtro do primeiro
    no estado da app (cada POST dispara nova chamada com seu doc_types).
    """
    chain = _doc_type_aware_chain()
    with _client(chain) as c:
        r1 = c.post("/query", json=_payload(doc_types=["manual"]))
        r2 = c.post("/query", json=_payload(doc_types=["bula"]))
    assert {cit["source"] for cit in r1.json()["citations"]} == {"manual_insulina.pdf"}
    assert {cit["source"] for cit in r2.json()["citations"]} == {"bula_amoxicilina.pdf"}
    assert chain.call_args_list == [
        ((_QUESTION, [DocType.MANUAL]),),
        ((_QUESTION, [DocType.BULA]),),
    ]


# ---------------------------------------------------------------------------
# DTF-10 / BR1
# ---------------------------------------------------------------------------


def test_BR1_none_null_empty_means_query_all_never_query_nothing() -> None:
    """DTF-10/BR1: ``None``/``null``/``[]`` significam consultar TUDO e nunca
    consultar nada — nenhuma dessas entradas gera erro nem consulta vazia."""
    expected = _make_result(DocType.BULA, "Resposta all [1].", include_citation=True)
    chain = _fixed_chain(expected)
    with _client(chain) as c:
        omitted = c.post("/query", json=_payload()).json()
        null = c.post("/query", json=_payload(doc_types=None)).json()
        empty = c.post("/query", json=_payload(doc_types=[])).json()
    for body in (omitted, null, empty):
        assert body["is_cold_start"] is False
        assert len(body["citations"]) == 1
    assert omitted == null == empty
    assert chain.call_count == 3


# ---------------------------------------------------------------------------
# DTF-11 / BR2 (filter before build_retriever)
# ---------------------------------------------------------------------------


def test_BR2_filter_before_build_retriever_coverage_note() -> None:
    """DTF-11/BR2: filtro por subconjunto ANTES de ``build_retriever``.

    COBERTURA INDIRETA/UNTESTABLE: ``build_retriever`` é um detalhe de
    implementação dentro de ``run_query``; não é observável no contrato HTTP
    com a chain mockada. A observação externa disponível é que o ``doc_types``
    chega à chain (assert em call_args) e que respostas/citações refletem o
    tipo pedido (HP3/HP7). A comprovação de que o slicing precede o
    ``build_retriever`` pertence a testes unitários de ``run_query``
    (tests/generation/test_chain.py). Aqui provamos que o parâmetro atravessa
    a API intacto."""
    chain = _fixed_chain(
        _make_result(DocType.PROTOCOLO, "Resp protocolo [1].", include_citation=True)
    )
    with _client(chain) as c:
        resp = c.post("/query", json=_payload(doc_types=["protocolo"]))
    assert resp.status_code == 200
    chain.assert_called_once_with(_QUESTION, [DocType.PROTOCOLO])


# ---------------------------------------------------------------------------
# DTF-12 / BR3 (shared stores dict not mutated)
# ---------------------------------------------------------------------------


def test_BR3_shared_stores_not_mutated_coverage_note() -> None:
    """DTF-12/BR3: dict compartilhado de stores não é mutado; requests isolados.

    COBERTURA INDIRETA/UNTESTABLE: a não-mutação do dict de stores acontece
    dentro de ``run_query`` (dict comprehension). Pela interface HTTP com chain
    mockada não dá para inspecionar o dict. Substituto: HP7 + FR2 provam que
    requests sequenciais na MESMA chain com doc_types distintos não se
    contaminam — comportamento que seria quebrado se houvesse mutação em
    lugar. A prova direta da não-mutação está nos testes unitários de
    ``run_query``."""
    chain = _doc_type_aware_chain()
    with _client(chain) as c:
        r1 = c.post("/query", json=_payload(doc_types=["bula"]))
        r2 = c.post("/query", json=_payload(doc_types=["protocolo"]))
    assert {cit["source"] for cit in r1.json()["citations"]} == {"bula_amoxicilina.pdf"}
    assert {cit["source"] for cit in r2.json()["citations"]} == {"protocolo_hpv.pdf"}


# ---------------------------------------------------------------------------
# DTF-13 / BR4 + BR5
# ---------------------------------------------------------------------------


def test_BR4_cold_start_preserved_on_empty_filtered_subset_llm_note() -> None:
    """DTF-13/BR4: cold start preservado em subconjunto filtrado vazio.

    PARCIAL/COBERTURA INDIRETA: a preservação do cold start é observável via
    HTTP (subconjunto sem chunks → resposta fixa sem citações) e coberta por
    HP1/HP2. A garantia de que o LLM NÃO é chamado é interna ao ``run_query`` e
    não comprovável por HTTP com chain mockada (a decisão ocorre antes da
    montagem do LLM). Aqui reafirmamos o contrato externo de cold start."""
    chain = _fixed_chain(
        _make_result(DocType.MANUAL, COLD_START_MESSAGE, is_cold_start=True)
    )
    with _client(chain) as c:
        resp = c.post("/query", json=_payload(doc_types=["manual"]))
    body = resp.json()
    assert resp.status_code == 200
    assert body["is_cold_start"] is True
    assert body["citations"] == []


def test_BR5_disclaimer_and_valid_citation_always_present() -> None:
    """DTF-13/BR5: disclaimer obrigatório e ao menos uma citação ``[N]`` válida
    sempre presentes em respostas não-cold-start."""
    answer = "Dose recomendada de 500 mg [1]."
    chain = _fixed_chain(_make_result(DocType.BULA, answer, include_citation=True))
    with _client(chain) as c:
        resp = c.post("/query", json=_payload(doc_types=["bula"]))
    body = resp.json()
    assert resp.status_code == 200
    assert body["disclaimer"]
    assert len(body["citations"]) >= 1
    assert body["citations"][0]["index"] == 1
    assert f"[{body['citations'][0]['index']}]" in body["answer"]
    assert body["answer"] == answer
