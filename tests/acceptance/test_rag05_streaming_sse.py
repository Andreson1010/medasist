"""Acceptance tests for RAG-05 (streaming de respostas via SSE).

Exercita a feature pelo ponto de entrada consumível ``POST /query/stream``
através do ``TestClient`` (lendo o ``text/event-stream`` linha a linha),
com ChromaDB real em ``tmp_path``, retriever/``stream_answer`` reais e o LLM
sempre mockado (nunca rede real). Nenhum arquivo de ``src/`` é modificado.
Dados sintéticos (amoxicilina fictícia).

Cobertura por critério de aceitação do spec RAG-05 (RQ-05-01..RQ-05-12):

- AC1/RQ-05-01: flag on + consulta normal → ``text/event-stream`` com eventos
  ``token`` cuja concatenação de ``delta`` é a resposta do LLM.
- AC2/RQ-05-02: sucesso → terminais ``citations`` + ``disclaimer`` + ``done``.
- AC3/RQ-05-03: perfil respeitado (ChatOpenAI recebe temperature/max_tokens do
  perfil solicitado).
- AC4/RQ-05-04: doc_types limita a recuperação antes de qualquer token.
- AC5/RQ-05-05: UI reconstroi ``QueryResult`` no histórico via ``st.write_stream``.
- AC6/RQ-05-06: flag off (default) → ``/query/stream`` retorna 404.
- AC7/RQ-05-07: cold start por retrieval vazio → sem token, sem LLM, emite
  ``cold_start`` + ``disclaimer`` + ``done``.
- AC8/RQ-05-08: resposta acumulada sem citação válida → ``cold_start`` terminal
  e a UI descarta o texto streamado mostrando ``cold_start_message``.
- AC9/RQ-05-09: LM Studio falha a meio → evento terminal ``error`` (sem ``done``)
  e a UI não persiste o parcial.
- AC10/RQ-05-10: cliente desconectado interrompe o gerador (sem terminais).
- AC11/RQ-05-11: rate limit dispara 429 antes de qualquer byte SSE.
- AC12/RQ-05-12: pergunta vazia → 422 (mesma validação do ``/query``).
"""

from __future__ import annotations

import json
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock, patch

import chromadb
import pytest
from fastapi.testclient import TestClient
from langchain_core.embeddings import Embeddings
from langchain_core.runnables import RunnableLambda
from pydantic import SecretStr

from medasist.config import Settings
from medasist.generation.citations import CitationItem
from medasist.ingestion.schemas import DocType
from medasist.profiles.schemas import UserProfile
from medasist.vectorstore.store import get_vectorstore

_ADMIN_KEY = "very-strong-key-0123456789"

_ANSWER_DELTAS = ("A dose de amoxicilina é 500 mg ", "a cada 8 horas [1].")


class _MatchingEmbeddings(Embeddings):
    """Embeddings que casa consulta com docs (L2 = 0 < threshold)."""

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[1.0, 1.0, 1.0, 1.0] for _ in texts]

    def embed_query(self, text: str) -> list[float]:
        return [1.0, 1.0, 1.0, 1.0]


class _EmptyEmbeddings(Embeddings):
    """Embeddings que retornam [] por padrão (coleção vazia → cold start)."""

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return []

    def embed_query(self, text: str) -> list[float]:
        return [1.0, 1.0, 1.0, 1.0]


def _make_runnable_llm(deltas: tuple[str, ...]) -> RunnableLambda:
    """LLM fake (Runnable) que ``stream`` yield os deltas informados.

    ``RunnableLambda`` é necessário para a composição
    ``prompt | llm | StrOutputParser`` funcionar — o ChatOpenAI real é um
    Runnable. O gerador interno simula o ``.stream`` do LLM.
    """

    def fake_stream(input, config=None, **kwargs):  # type: ignore[no-untyped-def]
        yield from deltas

    return RunnableLambda(fake_stream)


def _make_error_llm() -> RunnableLambda:
    """LLM fake (Runnable) que yield um delta e então falha a meio do stream."""

    def fake_stream(input, config=None, **kwargs):  # type: ignore[no-untyped-def]
        yield "a"
        raise RuntimeError("LM Studio indisponível")

    return RunnableLambda(fake_stream)


def _settings(**overrides: object) -> Settings:
    """Settings com streaming habilitado e overrides por critério."""
    defaults: dict[str, object] = {
        "generation_streaming_enabled": True,
        "retrieval_top_k": 10,
        "retrieval_score_threshold": 0.4,
    }
    defaults.update(overrides)
    return Settings(admin_api_key=SecretStr(_ADMIN_KEY), **defaults)


@pytest.fixture
def chroma_client(tmp_path) -> chromadb.ClientAPI:
    return chromadb.PersistentClient(path=str(tmp_path / "chroma"))


def _bula_store(
    client: chromadb.ClientAPI,
    embeddings: Embeddings,
    settings: Settings,
) -> object:
    """Store BULA com um chunk de amoxicilina, usando embeddings dados."""
    store = get_vectorstore(DocType.BULA, client, embeddings, settings)
    store.add_texts(
        texts=["Bula de amoxicilina 500 mg para infecções."],
        metadatas=[
            {
                "doc_type": "bula",
                "source_path": "bula_amoxicilina.pdf",
                "page": 3,
                "section": "Posologia",
            }
        ],
        ids=["bula_001"],
    )
    return store


def _parse_sse(text: str) -> list[dict[str, Any]]:
    """Parseia o corpo SSE em uma lista de eventos JSON (linhas ``data:``)."""
    events: list[dict[str, Any]] = []
    for line in text.splitlines():
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))
    return events


@contextmanager
def _streaming_api(
    stores: dict[DocType, object],
    settings: Settings,
    llm_factory: Any = None,
    empty_retrieval: bool = False,
) -> Generator[TestClient, None, None]:
    """TestClient com streaming habilitado, stores reais e LLM mockado.

    Patcheia ``get_all_vectorstores`` do main para usar as stores reais e o
    ``ChatOpenAI`` do chain para o LLM fake — o retriever e o ``stream_answer``
    reais rodam de ponta a ponta. Nunca há rede real.
    """
    if llm_factory is None:

        def _default_factory(**kwargs: Any) -> object:
            return _make_runnable_llm(_ANSWER_DELTAS)

        llm_factory = _default_factory

    def _fake_stores(client, embeddings, settings_):  # type: ignore[no-untyped-def]
        return stores

    with (
        patch("medasist.api.main.get_all_vectorstores", side_effect=_fake_stores),
        patch("medasist.api.main.get_settings", return_value=settings),
        patch("medasist.api.routers.query.get_settings", return_value=settings),
        patch("medasist.generation.chain.ChatOpenAI", side_effect=llm_factory),
        patch(
            "medasist.api.health.check_chromadb",
            return_value=MagicMock(status="ok"),
        ),
        patch(
            "medasist.api.health.check_lm_studio",
            return_value=MagicMock(status="ok"),
        ),
    ):
        from medasist.api.main import app

        with TestClient(app) as c:
            yield c


def _build_result(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Reconstrói o estado terminal a partir dos eventos SSE."""
    answer = "".join(e["delta"] for e in events if e["type"] == "token")
    citations = next((e["citations"] for e in events if e["type"] == "citations"), None)
    cold = any(e["type"] == "cold_start" for e in events)
    error = next((e["message"] for e in events if e["type"] == "error"), None)
    done = any(e["type"] == "done" for e in events)
    disclaimer = next((e["text"] for e in events if e["type"] == "disclaimer"), None)
    return {
        "answer": answer,
        "citations": citations,
        "cold_start": cold,
        "error": error,
        "done": done,
        "disclaimer": disclaimer,
    }


# ---------------------------------------------------------------------------
# AC1 / RQ-05-01 + AC2 / RQ-05-02 — happy path SSE
# ---------------------------------------------------------------------------


def test_ac01_streams_tokens_whose_deltas_concatenate_to_answer(
    chroma_client,
) -> None:
    """AC1: flag on + consulta normal → text/event-stream com tokens cuja
    concatenação de deltas é exatamente a resposta do LLM."""
    settings = _settings()
    store = _bula_store(chroma_client, _MatchingEmbeddings(), settings)
    stores = {DocType.BULA: store}

    with _streaming_api(stores, settings) as c:
        response = c.post(
            "/query/stream",
            json={"question": "qual a dose de amoxicilina?", "profile": "medico"},
        )

    assert response.status_code == 200
    assert response.headers["content-type"] == "text/event-stream; charset=utf-8"

    events = _parse_sse(response.text)
    types = [e["type"] for e in events]
    assert "token" in types
    answer = "".join(e["delta"] for e in events if e["type"] == "token")
    assert answer == "A dose de amoxicilina é 500 mg a cada 8 horas [1]."


def test_ac02_stream_ends_with_citations_disclaimer_done(chroma_client) -> None:
    """AC2: streaming completa com citações válidas → após os tokens, os
    terminais citations + disclaimer + done, nessa ordem."""
    settings = _settings()
    store = _bula_store(chroma_client, _MatchingEmbeddings(), settings)
    stores = {DocType.BULA: store}

    with _streaming_api(stores, settings) as c:
        response = c.post(
            "/query/stream",
            json={"question": "qual a dose?", "profile": "medico"},
        )

    events = _parse_sse(response.text)
    types = [e["type"] for e in events]
    assert types[-3:] == ["citations", "disclaimer", "done"]

    citations = events[-3]["citations"]
    assert citations == [
        {
            "index": 1,
            "source": "bula_amoxicilina.pdf",
            "section": "Posologia",
            "page": "3",
        }
    ]
    disclaimer = events[-2]
    assert disclaimer["type"] == "disclaimer"
    assert "auxiliar informativo" in disclaimer["text"]


# ---------------------------------------------------------------------------
# AC3 / RQ-05-03 — perfil respeitado
# ---------------------------------------------------------------------------


def test_ac03_profile_temperature_and_max_tokens_respected(chroma_client) -> None:
    """AC3: o perfil solicitado é respeitado — o ChatOpenAI recebe a
    temperature e o max_tokens do perfil (enfermeiro: 0.15 / 1024)."""
    settings = _settings()
    store = _bula_store(chroma_client, _MatchingEmbeddings(), settings)
    stores = {DocType.BULA: store}

    recorded: dict[str, Any] = {}

    def _recording_factory(**kwargs):
        recorded.update(kwargs)
        return _make_runnable_llm(_ANSWER_DELTAS)

    with _streaming_api(stores, settings, llm_factory=_recording_factory) as c:
        response = c.post(
            "/query/stream",
            json={
                "question": "qual a dose?",
                "profile": "enfermeiro",
            },
        )

    assert response.status_code == 200
    assert recorded["temperature"] == 0.15
    assert recorded["max_tokens"] == 1024


# ---------------------------------------------------------------------------
# AC4 / RQ-05-04 — doc_types limita a recuperação antes de qualquer token
# ---------------------------------------------------------------------------


def test_ac04_doc_types_limits_retrieval_before_tokens(chroma_client, mocker) -> None:
    """AC4: quando doc_types é informado, a recuperação é limitada às coleções
    selecionadas (via select_collections) antes de qualquer token. Uma coleção
    excluída não é consultada."""
    settings = _settings()
    store = _bula_store(chroma_client, _MatchingEmbeddings(), settings)
    stores = {DocType.BULA: store, DocType.DIRETRIZ: store}

    # Espiona select_collections para confirmar o filtro de doc_types
    import medasist.generation.chain as chain_mod

    with (
        patch.object(
            chain_mod, "select_collections", wraps=chain_mod.select_collections
        ) as spy,
        _streaming_api(stores, settings) as c,
    ):
        response = c.post(
            "/query/stream",
            json={
                "question": "qual a dose?",
                "profile": "medico",
                "doc_types": ["bula"],
            },
        )

    assert response.status_code == 200
    # select_collections foi chamado com o subconjunto contendo apenas BULA
    assert len(spy.call_args_list) >= 1
    first_args = spy.call_args_list[0].args
    assert first_args[1] == [DocType.BULA]
    assert "token" in [e["type"] for e in _parse_sse(response.text)]


# ---------------------------------------------------------------------------
# AC5 / RQ-05-05 — UI reconstrói QueryResult no histórico via st.write_stream
# ---------------------------------------------------------------------------


def test_ac05_ui_renders_incrementally_and_reconstructs_query_result(
    chroma_client, mocker
) -> None:
    """AC5: a UI consome o stream e reconstrói o QueryResult no histórico da
    sessão ao concluir (renderizando via st.write_stream)."""
    from medasist.ui.app import _render_streaming
    from medasist.ui.client import QueryResult

    settings = _settings()
    written: list[str] = []

    def _fake_query_stream(question, profile, doc_types, base_url, timeout):
        events = [
            {"type": "token", "delta": "Resposta "},
            {"type": "token", "delta": "completa."},
            {
                "type": "citations",
                "citations": [
                    {
                        "index": 1,
                        "source": "bula_amoxicilina.pdf",
                        "section": "Posologia",
                        "page": "3",
                    }
                ],
            },
            {"type": "disclaimer", "text": settings.disclaimer},
            {"type": "done"},
        ]
        from medasist.ui.client import StreamEvent

        for e in events:
            yield StreamEvent(
                type=e["type"],
                delta=e.get("delta"),
                citations=e.get("citations"),
                text=e.get("text"),
            )

    session = {"messages": []}
    with (
        patch("medasist.ui.app.query_stream", side_effect=_fake_query_stream),
        patch("medasist.ui.app.st.write_stream", side_effect=written.extend),
        patch("medasist.ui.app.st.session_state", session),
        patch("medasist.ui.app._render_response") as mock_render,
    ):
        _render_streaming("Pergunta?", "medico", None, settings)

    assert "".join(written) == "Resposta completa."
    assert len(session["messages"]) == 1
    result: QueryResult = session["messages"][0]["result"]
    assert result.answer == "Resposta completa."
    assert result.is_cold_start is False
    assert len(result.citations) == 1
    mock_render.assert_called_once()


# ---------------------------------------------------------------------------
# AC6 / RQ-05-06 — flag off (default) → 404
# ---------------------------------------------------------------------------


def test_ac06_disabled_flag_returns_404(chroma_client) -> None:
    """AC6: flag off (default) → /query/stream retorna 404, sem bytes SSE."""
    settings = _settings(generation_streaming_enabled=False)
    store = _bula_store(chroma_client, _MatchingEmbeddings(), settings)
    stores = {DocType.BULA: store}

    with _streaming_api(stores, settings) as c:
        response = c.post(
            "/query/stream",
            json={"question": "qual a dose?", "profile": "medico"},
        )

    assert response.status_code == 404
    assert "text/event-stream" not in response.headers.get("content-type", "")


def test_ac06_disabled_404_valid_body_422_invalid(chroma_client) -> None:
    """IMP-02: quando desabilitado, um corpo válido → 404; um corpo inválido →
    422 (a validação do body roda antes do guard da flag).

    Desvio deliberado e documentado: a validação Pydantic do corpo acontece no
    dispatch, antes de o handler avaliar ``generation_streaming_enabled``.
    Manter o corpo inválido como 422 (em vez de 404) é o comportamento mais
    simples e byte-idêntico ao ``/query`` — não vale a pena reestruturar para
    devolver 404 antes da validação.
    """
    settings = _settings(generation_streaming_enabled=False)
    store = _bula_store(chroma_client, _MatchingEmbeddings(), settings)
    stores = {DocType.BULA: store}

    with _streaming_api(stores, settings) as c:
        r_valid = c.post(
            "/query/stream", json={"question": "qual a dose?", "profile": "medico"}
        )
        r_invalid = c.post("/query/stream", json={"question": "", "profile": "medico"})

    assert r_valid.status_code == 404
    assert r_invalid.status_code == 422


# ---------------------------------------------------------------------------
# AC7 / RQ-05-07 — cold start por retrieval vazio
# ---------------------------------------------------------------------------


def test_ac07_empty_retrieval_emits_cold_start_no_tokens_no_llm(
    chroma_client, mocker
) -> None:
    """AC7: retrieval vazio (cold start) → nenhum token e nenhuma chamada ao
    LLM; emite cold_start + disclaimer + done."""
    settings = _settings()
    # store vazio: nenhum chunk acima do threshold
    store = get_vectorstore(DocType.BULA, chroma_client, _EmptyEmbeddings(), settings)
    stores = {DocType.BULA: store}

    called = {"n": 0}

    def _recording_factory(**kwargs):
        called["n"] += 1
        return _make_runnable_llm(_ANSWER_DELTAS)

    with _streaming_api(stores, settings, llm_factory=_recording_factory) as c:
        response = c.post(
            "/query/stream",
            json={"question": "qual a dose?", "profile": "medico"},
        )

    events = _parse_sse(response.text)
    types = [e["type"] for e in events]
    assert types == ["cold_start", "disclaimer", "done"]
    assert not any(e["type"] == "token" for e in events)
    assert called["n"] == 0


# ---------------------------------------------------------------------------
# AC8 / RQ-05-08 — resposta sem citação válida → cold_start terminal
# ---------------------------------------------------------------------------


def test_ac08_no_valid_citation_is_terminal_cold_start(chroma_client) -> None:
    """AC8: resposta acumulada sem citações válidas → o evento terminal
    cold_start é emitido (após os tokens já streamados)."""
    settings = _settings()
    store = _bula_store(chroma_client, _MatchingEmbeddings(), settings)
    stores = {DocType.BULA: store}

    with _streaming_api(
        stores,
        settings,
        llm_factory=lambda **kw: _make_runnable_llm(("Resposta sem marcador.",)),
    ) as c:
        response = c.post(
            "/query/stream",
            json={"question": "qual a dose?", "profile": "medico"},
        )

    events = _parse_sse(response.text)
    types = [e["type"] for e in events]
    # texto já streamado + terminal cold_start (a UI descarta o texto)
    assert types[0] == "token"
    assert "cold_start" in types
    assert types[-1] == "done"


def test_ac08_ui_discards_streamed_text_and_shows_cold_start(
    chroma_client, mocker
) -> None:
    """AC8 (UI): ao receber o cold_start terminal, a UI descarta o texto
    streamado e mostra a cold_start_message (sem persistir no histórico)."""
    from medasist.ui.app import _render_streaming

    settings = _settings()
    session = {"messages": []}

    def _fake_query_stream(question, profile, doc_types, base_url, timeout):
        from medasist.ui.client import StreamEvent

        yield StreamEvent(type="token", delta="Texto parcial")
        yield StreamEvent(type="cold_start", message=settings.cold_start_message)
        yield StreamEvent(type="disclaimer", text=settings.disclaimer)
        yield StreamEvent(type="done")

    with (
        patch("medasist.ui.app.query_stream", side_effect=_fake_query_stream),
        patch("medasist.ui.app.st.write_stream", side_effect=lambda g: list(g)),
        patch("medasist.ui.app.st.session_state", session),
        patch("medasist.ui.app.st.warning") as mock_warning,
        patch("medasist.ui.app.st.info") as mock_info,
    ):
        _render_streaming("Pergunta?", "medico", None, settings)

    mock_warning.assert_called_once()
    assert mock_warning.call_args.args[0] == settings.cold_start_message
    mock_info.assert_called_once()
    assert session["messages"] == []


# ---------------------------------------------------------------------------
# AC9 / RQ-05-09 — LM Studio falha a meio
# ---------------------------------------------------------------------------


def test_ac09_mid_stream_error_emits_error_without_done(chroma_client) -> None:
    """AC9: falha do LM Studio a meio do streaming → evento terminal error
    (sem done) e nenhum byte após o error."""
    settings = _settings()
    store = _bula_store(chroma_client, _MatchingEmbeddings(), settings)
    stores = {DocType.BULA: store}

    with _streaming_api(
        stores, settings, llm_factory=lambda **kw: _make_error_llm()
    ) as c:
        response = c.post(
            "/query/stream",
            json={"question": "qual a dose?", "profile": "medico"},
        )

    events = _parse_sse(response.text)
    types = [e["type"] for e in events]
    assert types == ["token", "error"]
    assert "done" not in types


def test_ac09_ui_does_not_persist_partial_on_error(chroma_client, mocker) -> None:
    """AC9 (UI): ao receber o erro terminal, a UI não persiste a resposta
    parcial no histórico."""
    from medasist.ui.app import _render_streaming

    settings = _settings()
    session = {"messages": []}

    def _fake_query_stream(question, profile, doc_types, base_url, timeout):
        from medasist.ui.client import StreamEvent

        yield StreamEvent(type="token", delta="parcial")
        yield StreamEvent(type="error", message="Erro ao gerar a resposta.")

    with (
        patch("medasist.ui.app.query_stream", side_effect=_fake_query_stream),
        patch("medasist.ui.app.st.write_stream", side_effect=lambda g: list(g)),
        patch("medasist.ui.app.st.session_state", session),
        patch("medasist.ui.app.st.error") as mock_error,
    ):
        _render_streaming("Pergunta?", "medico", None, settings)

    mock_error.assert_called_once()
    assert session["messages"] == []


# ---------------------------------------------------------------------------
# AC10 / RQ-05-10 — cliente desconecta a meio
# ---------------------------------------------------------------------------


def test_ac10_disconnect_stops_generator_without_terminals() -> None:
    """AC10: cliente desconectado a meio do stream → o gerador é interrompido
    sem emitir terminais (o Starlette fecha o gerador ao detectar a desconexão
    e o wrapper encerra o stream interno)."""
    from medasist.api.routers.query import _stream_events
    from medasist.api.schemas import QueryRequest

    body = QueryRequest(question="qual a dose?", profile=UserProfile.MEDICO)
    closed: list[bool] = []

    def _stream(question, doc_types=None):  # type: ignore[no-untyped-def]
        def gen():
            try:
                yield "a"
                yield "b"
                return [CitationItem(1, "bula.pdf", "Posologia", "3")], False
            finally:
                closed.append(True)

        return gen()

    events = iter(_stream_events(body, _stream))
    # apenas o token emitido antes da desconexão
    assert json.loads(next(events)[6:]) == {"type": "token", "delta": "a"}

    # desconexão: Starlette fecha o gerador (GeneratorExit)
    events.close()

    assert closed == [True]


# ---------------------------------------------------------------------------
# AC11 / RQ-05-11 — rate limit 429 antes de qualquer byte SSE
# ---------------------------------------------------------------------------


def test_ac11_rate_limit_429_before_any_sse_bytes(chroma_client, mocker) -> None:
    """AC11: quando o rate limit é excedido, o endpoint responde 429 antes de
    qualquer byte SSE (o slowapi roda antes do corpo do handler)."""
    from limits import parse as parse_limit
    from slowapi.errors import RateLimitExceeded
    from slowapi.wrappers import Limit
    from starlette.requests import Request as StarletteRequest

    from medasist.api.deps import limiter
    from medasist.api.routers.query import query_stream as query_stream_handler
    from medasist.api.schemas import QueryRequest

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/query/stream",
        "headers": [],
    }
    request = StarletteRequest(scope)
    body = QueryRequest(question="qual a dose?", profile=UserProfile.MEDICO)
    limit = Limit(
        limit=parse_limit("10/minute"),
        key_func=lambda *a: "testclient",
        scope=None,
        per_method=False,
        methods=None,
        error_message=None,
        exempt_when=None,
        cost=1,
        override_defaults=True,
    )

    with (
        patch.object(
            limiter,
            "_check_request_limit",
            side_effect=RateLimitExceeded(limit),
        ),
        pytest.raises(RateLimitExceeded),
    ):
        query_stream_handler(request, body)


def test_ac11_rate_limit_429_via_http_dispatch(chroma_client, mocker) -> None:
    """AC11: o rate limit é alcançado através do dispatch HTTP real — a rota
    registrada envolve o handler com o slowapi (CRIT-01). Quando o checador é
    excedido, o endpoint responde 429 sem bytes SSE."""
    from limits import parse as parse_limit
    from slowapi.errors import RateLimitExceeded
    from slowapi.wrappers import Limit

    from medasist.api.deps import limiter

    settings = _settings()
    store = _bula_store(chroma_client, _MatchingEmbeddings(), settings)
    stores = {DocType.BULA: store}
    limit = Limit(
        limit=parse_limit("10/minute"),
        key_func=lambda *a: "testclient",
        scope=None,
        per_method=False,
        methods=None,
        error_message=None,
        exempt_when=None,
        cost=1,
        override_defaults=True,
    )

    def _reject(request, endpoint_func, in_middleware):  # type: ignore[no-untyped-def]
        # __evaluate_limits define view_rate_limit antes de levantar
        request.state.view_rate_limit = None
        raise RateLimitExceeded(limit)

    with (
        patch.object(limiter, "_check_request_limit", side_effect=_reject),
        _streaming_api(stores, settings) as c,
    ):
        response = c.post(
            "/query/stream", json={"question": "qual a dose?", "profile": "medico"}
        )

    assert response.status_code == 429
    assert "text/event-stream" not in response.headers.get("content-type", "")


# ---------------------------------------------------------------------------
# AC12 / RQ-05-12 — pergunta vazia → 422
# ---------------------------------------------------------------------------


def test_ac12_empty_question_rejected_422(chroma_client) -> None:
    """AC12: pergunta vazia é rejeitada com a mesma validação do /query → 422."""
    settings = _settings()
    store = _bula_store(chroma_client, _MatchingEmbeddings(), settings)
    stores = {DocType.BULA: store}

    with _streaming_api(stores, settings) as c:
        response = c.post("/query/stream", json={"question": "", "profile": "medico"})

    assert response.status_code == 422
    assert "text/event-stream" not in response.headers.get("content-type", "")
