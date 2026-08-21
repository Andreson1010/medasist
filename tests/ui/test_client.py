from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import httpx
import pytest

from medasist.ui.client import (
    APIError,
    CitationResult,
    QueryResult,
    RateLimitError,
    RequestTimeoutError,
    ServerError,
    check_health,
    get_health,
    query,
    query_stream,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FULL_RESPONSE = {
    "answer": "A posologia recomendada é 500mg.",
    "citations": [
        {
            "index": 1,
            "source": "bula_dipirona.pdf",
            "section": "Posologia",
            "page": "2",
        }
    ],
    "profile": "medico",
    "disclaimer": "Este sistema é um auxiliar informativo.",
    "is_cold_start": False,
}

_COLD_START_RESPONSE = {
    **_FULL_RESPONSE,
    "citations": [],
    "is_cold_start": True,
}


def _mock_client(status_code: int, json_data: dict | None = None):
    """Retorna mock do httpx.Client configurado como context manager."""
    mock_response = MagicMock()
    mock_response.status_code = status_code
    mock_response.is_success = 200 <= status_code < 300
    if json_data is not None:
        mock_response.json.return_value = json_data

    mock_instance = MagicMock()
    mock_instance.get.return_value = mock_response
    mock_instance.post.return_value = mock_response

    mock_cls = MagicMock()
    mock_cls.return_value.__enter__.return_value = mock_instance
    mock_cls.return_value.__exit__.return_value = False
    return mock_cls, mock_instance


def _sse_data(payload: dict) -> str:
    """Serializa um payload como linha SSE ``data: {json}``."""
    return "data: " + json.dumps(payload, ensure_ascii=False)


def _mock_stream_client(status_code: int, lines: list[str]):
    """Retorna mock do httpx.Client.stream como context manager.

    ``lines`` representa o retorno de ``response.iter_lines()``.
    """
    mock_response = MagicMock()
    mock_response.status_code = status_code
    mock_response.is_success = 200 <= status_code < 300
    mock_response.iter_lines.return_value = lines

    mock_stream_cm = MagicMock()
    mock_stream_cm.__enter__.return_value = mock_response
    mock_stream_cm.__exit__.return_value = False

    mock_instance = MagicMock()
    mock_instance.stream.return_value = mock_stream_cm

    mock_cls = MagicMock()
    mock_cls.return_value.__enter__.return_value = mock_instance
    mock_cls.return_value.__exit__.return_value = False
    return mock_cls, mock_instance


# ---------------------------------------------------------------------------
# TestCheckHealth
# ---------------------------------------------------------------------------


class TestCheckHealth:
    def test_returns_true_when_ok(self, base_url: str) -> None:
        mock_cls, _ = _mock_client(200, {"status": "ok"})
        with patch("medasist.ui.client.httpx.Client", mock_cls):
            assert check_health(base_url) is True

    def test_returns_true_when_degraded(self, base_url: str) -> None:
        mock_cls, _ = _mock_client(200, {"status": "degraded"})
        with patch("medasist.ui.client.httpx.Client", mock_cls):
            assert check_health(base_url) is True

    def test_returns_false_on_connection_error(self, base_url: str) -> None:
        mock_cls = MagicMock()
        mock_cls.return_value.__enter__.side_effect = httpx.ConnectError("refused")
        with patch("medasist.ui.client.httpx.Client", mock_cls):
            assert check_health(base_url) is False

    def test_returns_false_on_timeout(self, base_url: str) -> None:
        mock_cls = MagicMock()
        mock_instance = MagicMock()
        mock_instance.get.side_effect = httpx.TimeoutException("timeout")
        mock_cls.return_value.__enter__.return_value = mock_instance
        mock_cls.return_value.__exit__.return_value = False
        with patch("medasist.ui.client.httpx.Client", mock_cls):
            assert check_health(base_url) is False

    def test_returns_false_on_500(self, base_url: str) -> None:
        mock_cls, _ = _mock_client(500)
        with patch("medasist.ui.client.httpx.Client", mock_cls):
            assert check_health(base_url) is False


# ---------------------------------------------------------------------------
# TestGetHealth
# ---------------------------------------------------------------------------


class TestGetHealth:
    def test_returns_body_when_ok(self, base_url: str) -> None:
        body = {
            "status": "ok",
            "chromadb": {"status": "ok", "details": "ok", "latency_ms": 1},
            "lm_studio": {"status": "ok", "details": "ok", "latency_ms": 1},
        }
        mock_cls, _ = _mock_client(200, body)
        with patch("medasist.ui.client.httpx.Client", mock_cls):
            assert get_health(base_url) == body

    def test_returns_body_when_degraded(self, base_url: str) -> None:
        body = {"status": "degraded"}
        mock_cls, _ = _mock_client(200, body)
        with patch("medasist.ui.client.httpx.Client", mock_cls):
            assert get_health(base_url) == body

    def test_returns_none_on_connection_error(self, base_url: str) -> None:
        mock_cls = MagicMock()
        mock_cls.return_value.__enter__.side_effect = httpx.ConnectError("refused")
        with patch("medasist.ui.client.httpx.Client", mock_cls):
            assert get_health(base_url) is None

    def test_returns_none_on_timeout(self, base_url: str) -> None:
        mock_cls = MagicMock()
        mock_instance = MagicMock()
        mock_instance.get.side_effect = httpx.TimeoutException("timeout")
        mock_cls.return_value.__enter__.return_value = mock_instance
        mock_cls.return_value.__exit__.return_value = False
        with patch("medasist.ui.client.httpx.Client", mock_cls):
            assert get_health(base_url) is None

    def test_returns_none_on_500(self, base_url: str) -> None:
        mock_cls, _ = _mock_client(500)
        with patch("medasist.ui.client.httpx.Client", mock_cls):
            assert get_health(base_url) is None


# ---------------------------------------------------------------------------
# TestQuery
# ---------------------------------------------------------------------------


class TestQuery:
    def test_returns_query_result(self, base_url: str) -> None:
        mock_cls, _ = _mock_client(200, _FULL_RESPONSE)
        with patch("medasist.ui.client.httpx.Client", mock_cls):
            result = query("Qual a posologia?", "medico", base_url=base_url)
        assert isinstance(result, QueryResult)
        assert result.answer == _FULL_RESPONSE["answer"]
        assert result.profile == "medico"
        assert result.is_cold_start is False

    def test_citations_parsed_correctly(self, base_url: str) -> None:
        response = {
            **_FULL_RESPONSE,
            "citations": [
                {
                    "index": 1,
                    "source": "doc_a.pdf",
                    "section": "Introdução",
                    "page": "1",
                },
                {
                    "index": 2,
                    "source": "doc_b.pdf",
                    "section": "Conclusão",
                    "page": "10",
                },
            ],
        }
        mock_cls, _ = _mock_client(200, response)
        with patch("medasist.ui.client.httpx.Client", mock_cls):
            result = query("Pergunta?", "enfermeiro", base_url=base_url)
        assert len(result.citations) == 2
        assert result.citations[0] == CitationResult(1, "doc_a.pdf", "Introdução", "1")
        assert result.citations[1] == CitationResult(2, "doc_b.pdf", "Conclusão", "10")

    def test_cold_start_flag_propagated(self, base_url: str) -> None:
        mock_cls, _ = _mock_client(200, _COLD_START_RESPONSE)
        with patch("medasist.ui.client.httpx.Client", mock_cls):
            result = query("Pergunta sem resultado?", "paciente", base_url=base_url)
        assert result.is_cold_start is True
        assert result.citations == []

    def test_sends_doc_types_when_provided(self, base_url: str) -> None:
        mock_cls, mock_instance = _mock_client(200, _FULL_RESPONSE)
        with patch("medasist.ui.client.httpx.Client", mock_cls):
            query("Pergunta?", "medico", doc_types=["bula"], base_url=base_url)
        body = mock_instance.post.call_args.kwargs["json"]
        assert body["doc_types"] == ["bula"]

    def test_sends_null_doc_types_when_none(self, base_url: str) -> None:
        mock_cls, mock_instance = _mock_client(200, _FULL_RESPONSE)
        with patch("medasist.ui.client.httpx.Client", mock_cls):
            query("Pergunta?", "medico", doc_types=None, base_url=base_url)
        body = mock_instance.post.call_args.kwargs["json"]
        assert body["doc_types"] is None

    def test_profile_sent_correctly(self, base_url: str) -> None:
        mock_cls, mock_instance = _mock_client(200, _FULL_RESPONSE)
        with patch("medasist.ui.client.httpx.Client", mock_cls):
            query("Pergunta?", "enfermeiro", base_url=base_url)
        body = mock_instance.post.call_args.kwargs["json"]
        assert body["profile"] == "enfermeiro"


# ---------------------------------------------------------------------------
# TestQueryErrors
# ---------------------------------------------------------------------------


class TestQueryErrors:
    def test_raises_rate_limit_error_on_429(self, base_url: str) -> None:
        mock_cls, _ = _mock_client(429)
        with (
            patch("medasist.ui.client.httpx.Client", mock_cls),
            pytest.raises(RateLimitError),
        ):
            query("Pergunta?", "medico", base_url=base_url)

    def test_raises_server_error_on_500(self, base_url: str) -> None:
        mock_cls, _ = _mock_client(500)
        with (
            patch("medasist.ui.client.httpx.Client", mock_cls),
            pytest.raises(ServerError),
        ):
            query("Pergunta?", "medico", base_url=base_url)

    def test_raises_server_error_on_503(self, base_url: str) -> None:
        mock_cls, _ = _mock_client(503)
        with (
            patch("medasist.ui.client.httpx.Client", mock_cls),
            pytest.raises(ServerError),
        ):
            query("Pergunta?", "medico", base_url=base_url)

    def test_raises_api_error_on_400(self, base_url: str) -> None:
        mock_cls, _ = _mock_client(400)
        with (
            patch("medasist.ui.client.httpx.Client", mock_cls),
            pytest.raises(APIError),
        ):
            query("Pergunta?", "medico", base_url=base_url)

    def test_raises_timeout_error_on_timeout(self, base_url: str) -> None:
        mock_cls = MagicMock()
        mock_instance = MagicMock()
        mock_instance.post.side_effect = httpx.TimeoutException("timeout")
        mock_cls.return_value.__enter__.return_value = mock_instance
        mock_cls.return_value.__exit__.return_value = False
        with (
            patch("medasist.ui.client.httpx.Client", mock_cls),
            pytest.raises(RequestTimeoutError),
        ):
            query("Pergunta?", "medico", base_url=base_url)


# ---------------------------------------------------------------------------
# TestQueryStream
# ---------------------------------------------------------------------------


class TestQueryStream:
    def test_yields_token_events_and_accumulates_deltas(self, base_url: str) -> None:
        lines = [
            _sse_data({"type": "token", "delta": "Olá"}),
            "",
            _sse_data({"type": "token", "delta": " mundo"}),
            "",
            _sse_data({"type": "citations", "citations": []}),
            "",
            _sse_data({"type": "disclaimer", "text": "Aviso médico"}),
            "",
            _sse_data({"type": "done"}),
            "",
        ]
        mock_cls, _ = _mock_stream_client(200, lines)
        with patch("medasist.ui.client.httpx.Client", mock_cls):
            events = list(query_stream("Pergunta?", "medico", base_url=base_url))

        tokens = [e.delta for e in events if e.type == "token"]
        assert tokens == ["Olá", " mundo"]
        assert "".join(tokens) == "Olá mundo"
        assert events[-1].type == "done"

    def test_parses_citations_event(self, base_url: str) -> None:
        lines = [
            _sse_data(
                {
                    "type": "citations",
                    "citations": [
                        {
                            "index": 1,
                            "source": "bula_dipirona.pdf",
                            "section": "Posologia",
                            "page": "2",
                        }
                    ],
                }
            ),
            "",
        ]
        mock_cls, _ = _mock_stream_client(200, lines)
        with patch("medasist.ui.client.httpx.Client", mock_cls):
            events = list(query_stream("Pergunta?", "medico", base_url=base_url))

        citations_event = next(e for e in events if e.type == "citations")
        assert citations_event.citations == [
            CitationResult(1, "bula_dipirona.pdf", "Posologia", "2")
        ]

    def test_parses_disclaimer_text(self, base_url: str) -> None:
        lines = [
            _sse_data({"type": "disclaimer", "text": "Este é o aviso."}),
            "",
        ]
        mock_cls, _ = _mock_stream_client(200, lines)
        with patch("medasist.ui.client.httpx.Client", mock_cls):
            events = list(query_stream("Pergunta?", "medico", base_url=base_url))

        disclaimer = next(e for e in events if e.type == "disclaimer")
        assert disclaimer.text == "Este é o aviso."

    def test_parses_cold_start_message(self, base_url: str) -> None:
        lines = [
            _sse_data({"type": "cold_start", "message": "Nenhum documento relevante."}),
            "",
            _sse_data({"type": "disclaimer", "text": "Aviso"}),
            "",
            _sse_data({"type": "done"}),
            "",
        ]
        mock_cls, _ = _mock_stream_client(200, lines)
        with patch("medasist.ui.client.httpx.Client", mock_cls):
            events = list(query_stream("Pergunta?", "medico", base_url=base_url))

        cold = next(e for e in events if e.type == "cold_start")
        assert cold.message == "Nenhum documento relevante."

    def test_parses_error_message(self, base_url: str) -> None:
        lines = [
            _sse_data({"type": "token", "delta": "parcial"}),
            "",
            _sse_data({"type": "error", "message": "Erro ao gerar a resposta."}),
            "",
        ]
        mock_cls, _ = _mock_stream_client(200, lines)
        with patch("medasist.ui.client.httpx.Client", mock_cls):
            events = list(query_stream("Pergunta?", "medico", base_url=base_url))

        error = next(e for e in events if e.type == "error")
        assert error.message == "Erro ao gerar a resposta."

    def test_ignores_blank_and_non_data_lines(self, base_url: str) -> None:
        lines = [
            "",
            ": keep-alive comment",
            _sse_data({"type": "token", "delta": "x"}),
            "",
        ]
        mock_cls, _ = _mock_stream_client(200, lines)
        with patch("medasist.ui.client.httpx.Client", mock_cls):
            events = list(query_stream("Pergunta?", "medico", base_url=base_url))

        assert [e.type for e in events] == ["token"]
        assert events[0].delta == "x"

    def test_sends_payload_to_stream_endpoint(self, base_url: str) -> None:
        mock_cls, mock_instance = _mock_stream_client(200, [])
        with patch("medasist.ui.client.httpx.Client", mock_cls):
            list(query_stream("Pergunta?", "enfermeiro", ["bula"], base_url=base_url))

        args, kwargs = mock_instance.stream.call_args
        method, url = args
        assert method == "POST"
        assert url.endswith("/query/stream")
        assert kwargs["json"] == {
            "question": "Pergunta?",
            "profile": "enfermeiro",
            "doc_types": ["bula"],
        }

    def test_raises_rate_limit_error_on_429(self, base_url: str) -> None:
        mock_cls, _ = _mock_stream_client(429, [])
        with (
            patch("medasist.ui.client.httpx.Client", mock_cls),
            pytest.raises(RateLimitError),
        ):
            list(query_stream("Pergunta?", "medico", base_url=base_url))

    def test_raises_server_error_on_500(self, base_url: str) -> None:
        mock_cls, _ = _mock_stream_client(500, [])
        with (
            patch("medasist.ui.client.httpx.Client", mock_cls),
            pytest.raises(ServerError),
        ):
            list(query_stream("Pergunta?", "medico", base_url=base_url))

    def test_raises_timeout_error_on_timeout(self, base_url: str) -> None:
        mock_cls = MagicMock()
        mock_stream_cm = MagicMock()
        mock_stream_cm.__enter__.side_effect = httpx.TimeoutException("timeout")
        mock_stream_cm.__exit__.return_value = False
        mock_instance = MagicMock()
        mock_instance.stream.return_value = mock_stream_cm
        mock_cls.return_value.__enter__.return_value = mock_instance
        mock_cls.return_value.__exit__.return_value = False
        with (
            patch("medasist.ui.client.httpx.Client", mock_cls),
            pytest.raises(RequestTimeoutError),
        ):
            list(query_stream("Pergunta?", "medico", base_url=base_url))
