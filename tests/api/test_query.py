from __future__ import annotations

import inspect
import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from limits import parse as parse_limit
from slowapi.errors import RateLimitExceeded
from slowapi.wrappers import Limit

from medasist.api.deps import limiter
from medasist.api.routers.query import (
    _stream_events,
)
from medasist.api.routers.query import (
    query as query_handler,
)
from medasist.api.routers.query import (
    query_stream as query_stream_handler,
)
from medasist.api.schemas import QueryRequest
from medasist.generation.citations import CitationItem
from medasist.ingestion.schemas import DocType
from medasist.profiles.schemas import UserProfile

VALID_PAYLOAD = {"question": "qual a dose de amoxicilina?", "profile": "medico"}


def _parse_sse(text: str) -> list[dict[str, Any]]:
    """Parseia o corpo SSE em uma lista de eventos JSON (linhas ``data:``)."""
    events: list[dict[str, Any]] = []
    for line in text.splitlines():
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))
    return events


def _streaming_chains_for(mock_chain: MagicMock) -> dict:
    """Mapeamento ``UserProfile → chain streamada`` para todos os perfis."""
    return dict.fromkeys(UserProfile, mock_chain)


def _make_stream_chain(
    deltas: tuple[str, ...] = ("Olá", " ", "mundo [1]."),
    citations: list[CitationItem] | None = None,
    is_cold_start: bool = False,
) -> MagicMock:
    """Chain streamada mockada que yield deltas e retorna o estado terminal."""
    chain = MagicMock()
    valid_citations = (
        citations
        if citations is not None
        else [
            CitationItem(
                index=1, source="bula_amoxicilina.pdf", section="Posologia", page="3"
            )
        ]
    )

    def _call(question: str, doc_types=None):  # type: ignore[no-untyped-def]
        def gen() -> Any:
            yield from deltas
            return valid_citations, is_cold_start

        return gen()

    chain.side_effect = _call
    return chain


def _make_error_stream_chain() -> MagicMock:
    """Chain streamada que yield um token e então falha a meio."""
    chain = MagicMock()

    def _call(question: str, doc_types=None):  # type: ignore[no-untyped-def]
        def gen() -> Any:
            yield "a"
            raise RuntimeError("LM Studio indisponível")

        return gen()

    chain.side_effect = _call
    return chain


class TestQueryIsSync:
    def test_query_endpoint_is_sync(self) -> None:
        """O endpoint /query deve ser síncrono (def), não async.

        Endpoint async chamando a chain síncrona bloqueia o event loop durante
        a geração do LLM. FastAPI roda rotas ``def`` numa threadpool.
        """
        assert not hasattr(query_handler, "__await__")
        assert not inspect.iscoroutinefunction(query_handler)


class TestQueryHappyPath:
    def test_returns_200(self, client: TestClient) -> None:
        response = client.post("/query", json=VALID_PAYLOAD)
        assert response.status_code == 200

    def test_answer_present(self, client: TestClient) -> None:
        response = client.post("/query", json=VALID_PAYLOAD)
        assert response.json()["answer"]

    def test_disclaimer_always_present(self, client: TestClient) -> None:
        response = client.post("/query", json=VALID_PAYLOAD)
        assert response.json()["disclaimer"]

    def test_profile_echoed_in_response(self, client: TestClient) -> None:
        response = client.post("/query", json=VALID_PAYLOAD)
        assert response.json()["profile"] == "medico"

    def test_citations_list_returned(self, client: TestClient) -> None:
        response = client.post("/query", json=VALID_PAYLOAD)
        citations = response.json()["citations"]
        assert isinstance(citations, list)
        assert len(citations) == 1
        assert citations[0]["source"] == "bula_amoxicilina.pdf"

    def test_is_cold_start_false_on_normal_response(self, client: TestClient) -> None:
        response = client.post("/query", json=VALID_PAYLOAD)
        assert response.json()["is_cold_start"] is False


class TestQueryColdStart:
    def test_cold_start_flag_propagated(self, cold_start_chain: MagicMock) -> None:
        chains = dict.fromkeys(UserProfile, cold_start_chain)

        with (
            patch("medasist.api.main.get_all_vectorstores", return_value={}),
            patch(
                "medasist.api.main.build_chain",
                side_effect=lambda stores, profile, settings: chains[profile],
            ),
        ):
            from medasist.api.main import app

            with TestClient(app) as c:
                response = c.post("/query", json=VALID_PAYLOAD)

        assert response.status_code == 200
        assert response.json()["is_cold_start"] is True
        assert response.json()["citations"] == []

    def test_disclaimer_present_on_cold_start(
        self, cold_start_chain: MagicMock
    ) -> None:
        chains = dict.fromkeys(UserProfile, cold_start_chain)

        with (
            patch("medasist.api.main.get_all_vectorstores", return_value={}),
            patch(
                "medasist.api.main.build_chain",
                side_effect=lambda stores, profile, settings: chains[profile],
            ),
        ):
            from medasist.api.main import app

            with TestClient(app) as c:
                response = c.post("/query", json=VALID_PAYLOAD)

        assert response.json()["disclaimer"]


class TestQueryValidation:
    def test_empty_question_returns_422(self, client: TestClient) -> None:
        response = client.post("/query", json={"question": "", "profile": "medico"})
        assert response.status_code == 422

    def test_question_too_long_returns_422(self, client: TestClient) -> None:
        response = client.post(
            "/query", json={"question": "x" * 501, "profile": "medico"}
        )
        assert response.status_code == 422

    def test_invalid_profile_returns_422(self, client: TestClient) -> None:
        response = client.post(
            "/query", json={"question": "qual a dose?", "profile": "invalido"}
        )
        assert response.status_code == 422

    def test_missing_profile_returns_422(self, client: TestClient) -> None:
        response = client.post("/query", json={"question": "qual a dose?"})
        assert response.status_code == 422

    def test_all_profiles_accepted(self, client: TestClient) -> None:
        for profile in ("medico", "enfermeiro", "assistente", "paciente"):
            response = client.post(
                "/query", json={"question": "qual a dose?", "profile": profile}
            )
            assert (
                response.status_code == 200
            ), f"perfil '{profile}' retornou {response.status_code}"


class TestQueryDocTypes:
    def _chain_recording(
        self,
        mock_chain: MagicMock,
        calls: list,
    ) -> MagicMock:
        "MagicMock que registra os argumentos e devolve um GenerationResult."
        mock_chain.side_effect = lambda *args, **kwargs: (
            calls.append((args, kwargs)) or mock_chain.return_value
        )
        return mock_chain

    def test_doc_types_passed_into_chain(self, mock_chain: MagicMock) -> None:
        calls: list = []
        self._chain_recording(mock_chain, calls)
        chains = dict.fromkeys(UserProfile, mock_chain)

        with (
            patch("medasist.api.main.get_all_vectorstores", return_value={}),
            patch(
                "medasist.api.main.build_chain",
                side_effect=lambda stores, profile, settings: chains[profile],
            ),
        ):
            from medasist.api.main import app

            with TestClient(app) as c:
                response = c.post(
                    "/query",
                    json={
                        "question": "qual a dose?",
                        "profile": "medico",
                        "doc_types": ["bula", "protocolo"],
                    },
                )

        assert response.status_code == 200
        assert len(calls) == 1
        args, kwargs = calls[0]
        assert "qual a dose?" in args
        assert DocType.BULA in args[1]
        assert DocType.PROTOCOLO in args[1]

    def test_full_doc_types_set_accepted(self, client: TestClient) -> None:
        response = client.post(
            "/query",
            json={
                "question": "qual a dose?",
                "profile": "medico",
                "doc_types": ["bula", "diretriz", "protocolo", "manual"],
            },
        )
        assert response.status_code == 200

    def test_omitted_doc_types_returns_200(self, client: TestClient) -> None:
        response = client.post("/query", json=VALID_PAYLOAD)
        assert response.status_code == 200

    def test_empty_doc_types_returns_200(self, client: TestClient) -> None:
        response = client.post(
            "/query",
            json={"question": "qual a dose?", "profile": "medico", "doc_types": []},
        )
        assert response.status_code == 200

    def test_lowercase_valid_value_accepted(self, client: TestClient) -> None:
        response = client.post(
            "/query",
            json={
                "question": "qual a dose?",
                "profile": "medico",
                "doc_types": ["bula"],
            },
        )
        assert response.status_code == 200

    def test_invalid_non_doctype_value_returns_422(self, client: TestClient) -> None:
        response = client.post(
            "/query",
            json={
                "question": "qual a dose?",
                "profile": "medico",
                "doc_types": ["PACIENTE"],
            },
        )
        assert response.status_code == 422


class TestQueryStreamIsSync:
    def test_query_stream_endpoint_is_sync(self) -> None:
        """O endpoint /query/stream deve ser síncrono (def), não async.

        Endpoint async chamando a chain síncrona bloquearia o event loop
        durante a geração do LLM (precedente L-004/FIX-02).
        """
        assert not hasattr(query_stream_handler, "__await__")
        assert not inspect.iscoroutinefunction(query_stream_handler)


class TestQueryStream:
    def test_happy_path_returns_tokens_and_terminals(
        self, streaming_client: TestClient
    ) -> None:
        response = streaming_client.post("/query/stream", json=VALID_PAYLOAD)

        assert response.status_code == 200
        assert response.headers["content-type"] == "text/event-stream; charset=utf-8"

        events = _parse_sse(response.text)
        types = [e["type"] for e in events]
        assert types == ["token", "token", "token", "citations", "disclaimer", "done"]

        answer = "".join(e["delta"] for e in events if e["type"] == "token")
        assert answer == "Olá mundo [1]."

    def test_citations_payload_serialized(self, streaming_client: TestClient) -> None:
        response = streaming_client.post("/query/stream", json=VALID_PAYLOAD)
        events = _parse_sse(response.text)
        citations = next(e for e in events if e["type"] == "citations")
        assert citations["citations"] == [
            {
                "index": 1,
                "source": "bula_amoxicilina.pdf",
                "section": "Posologia",
                "page": "3",
            }
        ]

    def test_disclaimer_present(self, streaming_client: TestClient) -> None:
        response = streaming_client.post("/query/stream", json=VALID_PAYLOAD)
        events = _parse_sse(response.text)
        disclaimer = next(e for e in events if e["type"] == "disclaimer")
        assert disclaimer["text"] == "Este sistema é um auxiliar informativo."

    def test_profile_selects_correct_chain(self, streaming_client_factory) -> None:
        calls: dict[str, list] = {"medico": [], "enfermeiro": []}

        def _recording(call_list: list) -> MagicMock:
            chain = MagicMock()

            def _call(question: str, doc_types=None):  # type: ignore[no-untyped-def]
                call_list.append((question, doc_types))
                return _make_stream_chain()(question, doc_types)

            chain.side_effect = _call
            return chain

        chains = {
            UserProfile.MEDICO: _recording(calls["medico"]),
            UserProfile.ENFERMEIRO: _recording(calls["enfermeiro"]),
            UserProfile.ASSISTENTE: _recording([]),
            UserProfile.PACIENTE: _recording([]),
        }

        with streaming_client_factory(chains) as c:
            c.post(
                "/query/stream",
                json={"question": "qual a dose?", "profile": "enfermeiro"},
            )

        assert calls["enfermeiro"] == [("qual a dose?", None)]
        assert calls["medico"] == []

    def test_doc_types_passed_to_closure(self, streaming_client_factory) -> None:
        calls: list = []
        chain = MagicMock()

        def _call(question: str, doc_types=None):  # type: ignore[no-untyped-def]
            calls.append((question, doc_types))
            return _make_stream_chain()(question, doc_types)

        chain.side_effect = _call
        chains = _streaming_chains_for(chain)

        with streaming_client_factory(chains) as c:
            c.post(
                "/query/stream",
                json={
                    "question": "qual a dose?",
                    "profile": "medico",
                    "doc_types": ["bula", "protocolo"],
                },
            )

        assert calls == [("qual a dose?", [DocType.BULA, DocType.PROTOCOLO])]

    def test_cold_start_emits_no_tokens(self, streaming_client_factory) -> None:
        chains = _streaming_chains_for(
            MagicMock(side_effect=_make_stream_chain(deltas=(), is_cold_start=True))
        )

        with streaming_client_factory(chains) as c:
            response = c.post("/query/stream", json=VALID_PAYLOAD)

        events = _parse_sse(response.text)
        types = [e["type"] for e in events]
        assert types == ["cold_start", "disclaimer", "done"]
        assert not any(e["type"] == "token" for e in events)

    def test_no_valid_citations_emits_cold_start_terminal(
        self, streaming_client_factory
    ) -> None:
        chains = _streaming_chains_for(
            MagicMock(
                side_effect=_make_stream_chain(
                    deltas=("texto sem citação",), citations=[], is_cold_start=True
                )
            )
        )

        with streaming_client_factory(chains) as c:
            response = c.post("/query/stream", json=VALID_PAYLOAD)

        events = _parse_sse(response.text)
        types = [e["type"] for e in events]
        # Texto já streamado (tokens) + terminal cold_start (RQ-05-08): a UI
        # descarta o texto streamado ao receber o cold_start.
        assert types == ["token", "cold_start", "disclaimer", "done"]

    def test_mid_stream_error_emits_error_without_done(
        self, streaming_client_factory
    ) -> None:
        chains = _streaming_chains_for(_make_error_stream_chain())

        with streaming_client_factory(chains) as c:
            response = c.post("/query/stream", json=VALID_PAYLOAD)

        events = _parse_sse(response.text)
        types = [e["type"] for e in events]
        assert types == ["token", "error"]
        assert "done" not in types

    def test_empty_question_returns_422(self, streaming_client: TestClient) -> None:
        response = streaming_client.post(
            "/query/stream", json={"question": "", "profile": "medico"}
        )
        assert response.status_code == 422

    def test_disabled_flag_returns_404(self, client: TestClient) -> None:
        response = client.post("/query/stream", json=VALID_PAYLOAD)
        assert response.status_code == 404
        assert "text/event-stream" not in response.headers.get("content-type", "")

    def test_rate_limit_check_runs_before_streaming(self) -> None:
        """O wrapper do slowapi roda o rate limit antes de iniciar o stream.

        O ``@limiter.limit("10/minute")`` no ``query_stream`` invoca
        ``_check_request_limit`` antes do corpo do handler (que criaria o
        StreamingResponse). Quando o limite é excedido, ``RateLimitExceeded``
        é levantado antes de qualquer byte SSE (RQ-05-11).

        Teste unitário do wrapper (o caminho de produção via HTTP dispatch é
        coberto por ``TestQueryRateLimit``).
        """
        from starlette.requests import Request as StarletteRequest

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


class TestQueryRateLimit:
    """CRIT-01: a ordem dos decorators faz o router registrar o handler
    envolvido pelo slowapi, de modo que ``_check_request_limit`` roda através
    do dispatch HTTP (não apenas ao chamar o handler diretamente)."""

    @staticmethod
    def _limit() -> Limit:
        return Limit(
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

    @staticmethod
    def _reject(limit: Limit):
        def _raise(request, endpoint_func, in_middleware):  # type: ignore[no-untyped-def]
            # __evaluate_limits define view_rate_limit antes de levantar
            request.state.view_rate_limit = None
            raise RateLimitExceeded(limit)

        return _raise

    def test_query_rate_limit_enforced_via_http_dispatch(
        self, client: TestClient
    ) -> None:
        """O POST /query passa pelo checador de rate limit no dispatch."""
        with patch.object(
            limiter, "_check_request_limit", side_effect=self._reject(self._limit())
        ):
            response = client.post("/query", json=VALID_PAYLOAD)
        assert response.status_code == 429

    def test_stream_rate_limit_enforced_via_http_dispatch(
        self, streaming_client: TestClient
    ) -> None:
        """O POST /query/stream passa pelo checador de rate limit no dispatch,
        respondendo 429 antes de qualquer byte SSE."""
        with patch.object(
            limiter, "_check_request_limit", side_effect=self._reject(self._limit())
        ):
            response = streaming_client.post("/query/stream", json=VALID_PAYLOAD)
        assert response.status_code == 429
        assert "text/event-stream" not in response.headers.get("content-type", "")


class TestStreamEventsDisconnect:
    """IMP-01: a desconexão é tratada pelo Starlette, que fecha o gerador
    (GeneratorExit) ao detectar o cliente ausente; o wrapper então encerra o
    stream interno sem emitir terminais."""

    def _body(self) -> QueryRequest:
        return QueryRequest(question="qual a dose?", profile=UserProfile.MEDICO)

    def _stream(self, deltas: tuple[str, ...] = ("a", "b", "c"), closed=None):
        def _call(question: str, doc_types=None):  # type: ignore[no-untyped-def]
            def gen() -> Any:
                try:
                    yield from deltas
                    return (
                        [
                            CitationItem(
                                index=1,
                                source="bula.pdf",
                                section="Posologia",
                                page="3",
                            )
                        ],
                        False,
                    )
                finally:
                    if closed is not None:
                        closed.append(True)

            return gen()

        return _call

    def test_closing_generator_stops_without_terminals(self) -> None:
        """Fechar o gerador (desconexão) interrompe o stream sem emitir
        tokens/terminais adicionais."""
        events = iter(_stream_events(self._body(), self._stream()))
        first = next(events)
        assert json.loads(first[6:]) == {"type": "token", "delta": "a"}

        events.close()

        with pytest.raises(StopIteration):
            next(events)

    def test_closing_generator_closes_inner_stream(self) -> None:
        """Fechar o wrapper propaga o encerramento ao gerador interno de
        ``stream_answer``, encerrando o stream do LLM."""
        closed: list[bool] = []
        events = iter(_stream_events(self._body(), self._stream(closed=closed)))
        next(events)
        events.close()
        assert closed == [True]
