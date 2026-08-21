"""Testes unitários dos helpers de UI em ``medasist.ui.app``.

Sem spawnar a aplicação Streamlit: mocka ``st.session_state`` e as chamadas
de ``st.warning``, além de ``get_health`` do client. Dados sintéticos.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from medasist.ui.app import (
    _build_stream_result,
    _check_and_warn_health,
    _delta_generator,
    _render_streaming,
    _StreamState,
)
from medasist.ui.client import CitationResult, QueryResult, StreamEvent

_BASE_URL = "http://test-api"

_SETTINGS = SimpleNamespace(
    api_base_url=_BASE_URL,
    ui_request_timeout=30.0,
    cold_start_message="Nenhum documento relevante encontrado.",
    disclaimer=(
        "Este sistema é um auxiliar informativo e não substitui "
        "avaliação médica presencial."
    ),
)

_DISCLAIMER = _SETTINGS.disclaimer
_COLD_START_MSG = _SETTINGS.cold_start_message

_HEALTH_OK = {
    "status": "ok",
    "chromadb": {"status": "ok", "details": "ok", "latency_ms": 1},
    "lm_studio": {"status": "ok", "details": "ok", "latency_ms": 1},
}

_HEALTH_DEGRADED = {
    "status": "degraded",
    "chromadb": {"status": "ok", "details": "ok", "latency_ms": 1},
    "lm_studio": {"status": "unavailable", "details": "fora do ar", "latency_ms": 1},
}


def _consume(generator):
    """Simula ``st.write_stream``: itera o gerador e retorna o texto acumulado."""
    return "".join(chunk for chunk in generator)


class TestCheckAndWarnHealth:
    def test_warns_when_api_unreachable(self) -> None:
        with (
            patch("medasist.ui.app.get_health", return_value=None),
            patch("medasist.ui.app.st.warning") as mock_warning,
            patch("medasist.ui.app.st.session_state", {}),
        ):
            _check_and_warn_health(_BASE_URL)

        mock_warning.assert_called_once()
        assert "indisponível" in mock_warning.call_args.args[0]

    def test_warns_about_degraded_dependencies(self) -> None:
        with (
            patch("medasist.ui.app.get_health", return_value=_HEALTH_DEGRADED),
            patch("medasist.ui.app.st.warning") as mock_warning,
            patch("medasist.ui.app.st.session_state", {}),
        ):
            _check_and_warn_health(_BASE_URL)

        mock_warning.assert_called_once()
        message = mock_warning.call_args.args[0]
        assert "dependências degradadas" in message
        assert "LM Studio (unavailable)" in message
        assert "ChromaDB (ok)" not in message

    def test_no_warning_when_api_ok(self) -> None:
        with (
            patch("medasist.ui.app.get_health", return_value=_HEALTH_OK),
            patch("medasist.ui.app.st.warning") as mock_warning,
            patch("medasist.ui.app.st.session_state", {}),
        ):
            _check_and_warn_health(_BASE_URL)

        mock_warning.assert_not_called()

    def test_checks_once_per_session(self) -> None:
        with (
            patch("medasist.ui.app.get_health") as mock_get_health,
            patch("medasist.ui.app.st.warning"),
            patch("medasist.ui.app.st.session_state", {}),
        ):
            _check_and_warn_health(_BASE_URL)
            _check_and_warn_health(_BASE_URL)

        mock_get_health.assert_called_once()


# ---------------------------------------------------------------------------
# TestDeltaGenerator
# ---------------------------------------------------------------------------


class TestDeltaGenerator:
    def test_accumulates_answer_and_captures_terminals(self) -> None:
        events = iter(
            [
                StreamEvent(type="token", delta="Olá"),
                StreamEvent(type="token", delta=" mundo"),
                StreamEvent(
                    type="citations",
                    citations=[CitationResult(1, "doc_a.pdf", "Posologia", "2")],
                ),
                StreamEvent(type="disclaimer", text=_DISCLAIMER),
                StreamEvent(type="done"),
            ]
        )
        state = _StreamState()

        deltas = list(_delta_generator(events, state))

        assert deltas == ["Olá", " mundo"]
        assert state.answer == "Olá mundo"
        assert state.citations == [CitationResult(1, "doc_a.pdf", "Posologia", "2")]
        assert state.disclaimer == _DISCLAIMER
        assert state.done is True
        assert state.is_cold_start is False
        assert state.error is None

    def test_captures_cold_start(self) -> None:
        events = iter(
            [
                StreamEvent(type="cold_start", message=_COLD_START_MSG),
                StreamEvent(type="disclaimer", text=_DISCLAIMER),
                StreamEvent(type="done"),
            ]
        )
        state = _StreamState()

        list(_delta_generator(events, state))

        assert state.is_cold_start is True
        assert state.done is True
        assert state.answer == ""

    def test_captures_error_without_done(self) -> None:
        events = iter(
            [
                StreamEvent(type="token", delta="parcial"),
                StreamEvent(type="error", message="Erro ao gerar a resposta."),
            ]
        )
        state = _StreamState()

        list(_delta_generator(events, state))

        assert state.error == "Erro ao gerar a resposta."
        assert state.answer == "parcial"
        assert state.done is False


# ---------------------------------------------------------------------------
# TestBuildStreamResult
# ---------------------------------------------------------------------------


class TestBuildStreamResult:
    def test_success_builds_query_result(self) -> None:
        state = _StreamState(
            answer="Resposta completa.",
            citations=[CitationResult(1, "doc_a.pdf", "Seção", "1")],
            disclaimer=_DISCLAIMER,
            done=True,
        )

        result = _build_stream_result(state, "medico", _SETTINGS)

        assert isinstance(result, QueryResult)
        assert result.answer == "Resposta completa."
        assert result.profile == "medico"
        assert result.disclaimer == _DISCLAIMER
        assert result.is_cold_start is False
        assert len(result.citations) == 1

    def test_uses_settings_disclaimer_when_missing(self) -> None:
        state = _StreamState(answer="Resposta.", done=True)

        result = _build_stream_result(state, "medico", _SETTINGS)

        assert result is not None
        assert result.disclaimer == _DISCLAIMER

    def test_error_returns_none(self) -> None:
        state = _StreamState(answer="parcial", error="Erro ao gerar a resposta.")

        assert _build_stream_result(state, "medico", _SETTINGS) is None

    def test_cold_start_returns_none(self) -> None:
        state = _StreamState(answer="", is_cold_start=True, done=True)

        assert _build_stream_result(state, "medico", _SETTINGS) is None

    def test_not_done_returns_none(self) -> None:
        state = _StreamState(answer="incompleto")

        assert _build_stream_result(state, "medico", _SETTINGS) is None


# ---------------------------------------------------------------------------
# TestRenderStreaming
# ---------------------------------------------------------------------------


def _success_events():
    yield StreamEvent(type="token", delta="Resposta ")
    yield StreamEvent(type="token", delta="completa.")
    yield StreamEvent(
        type="citations",
        citations=[CitationResult(1, "doc_a.pdf", "Posologia", "2")],
    )
    yield StreamEvent(type="disclaimer", text=_DISCLAIMER)
    yield StreamEvent(type="done")


def _cold_start_events():
    yield StreamEvent(type="cold_start", message=_COLD_START_MSG)
    yield StreamEvent(type="disclaimer", text=_DISCLAIMER)
    yield StreamEvent(type="done")


def _error_events():
    yield StreamEvent(type="token", delta="parcial")
    yield StreamEvent(type="error", message="Erro ao gerar a resposta.")


class TestRenderStreaming:
    def test_success_persists_query_result_to_history(self) -> None:
        session = {"messages": []}
        with (
            patch("medasist.ui.app.query_stream", return_value=_success_events()),
            patch("medasist.ui.app.st.write_stream", side_effect=_consume),
            patch("medasist.ui.app.st.session_state", session),
            patch("medasist.ui.app._render_response") as mock_render,
        ):
            _render_streaming("Pergunta?", "medico", None, _SETTINGS)

        assert len(session["messages"]) == 1
        message = session["messages"][0]
        assert message["role"] == "assistant"
        assert message["content"] == "Resposta completa."
        assert message["result"].answer == "Resposta completa."
        assert message["result"].is_cold_start is False
        mock_render.assert_called_once()
        assert mock_render.call_args.args[0].answer == "Resposta completa."

    def test_cold_start_discards_and_shows_message(self) -> None:
        session = {"messages": []}
        with (
            patch("medasist.ui.app.query_stream", return_value=_cold_start_events()),
            patch("medasist.ui.app.st.write_stream", side_effect=_consume),
            patch("medasist.ui.app.st.session_state", session),
            patch("medasist.ui.app.st.warning") as mock_warning,
            patch("medasist.ui.app.st.info") as mock_info,
        ):
            _render_streaming("Pergunta?", "medico", None, _SETTINGS)

        mock_warning.assert_called_once()
        assert mock_warning.call_args.args[0] == _COLD_START_MSG
        mock_info.assert_called_once()
        assert session["messages"] == []

    def test_error_does_not_persist_partial(self) -> None:
        session = {"messages": []}
        with (
            patch("medasist.ui.app.query_stream", return_value=_error_events()),
            patch("medasist.ui.app.st.write_stream", side_effect=_consume),
            patch("medasist.ui.app.st.session_state", session),
            patch("medasist.ui.app.st.error") as mock_error,
        ):
            _render_streaming("Pergunta?", "medico", None, _SETTINGS)

        mock_error.assert_called_once()
        assert session["messages"] == []

    def test_404_falls_back_to_non_streaming_query(self) -> None:
        from medasist.ui.client import NotFoundError

        session = {"messages": []}
        fallback_result = QueryResult(
            answer="Resposta via /query.",
            citations=[CitationResult(1, "doc_b.pdf", "Seção", "3")],
            profile="medico",
            disclaimer=_DISCLAIMER,
            is_cold_start=False,
        )
        with (
            patch(
                "medasist.ui.app.query_stream",
                side_effect=NotFoundError("Streaming desabilitado."),
            ),
            patch("medasist.ui.app.query", return_value=fallback_result) as mock_query,
            patch("medasist.ui.app.st.session_state", session),
            patch("medasist.ui.app._render_response") as mock_render,
        ):
            _render_streaming("Pergunta?", "medico", None, _SETTINGS)

        mock_query.assert_called_once()
        mock_render.assert_called_once()
        assert mock_render.call_args.args[0] is fallback_result
        assert len(session["messages"]) == 1
        assert session["messages"][0]["result"] is fallback_result

    def test_rate_limit_error_is_handled(self) -> None:
        from medasist.ui.client import RateLimitError

        session = {"messages": []}
        with (
            patch(
                "medasist.ui.app.query_stream",
                side_effect=RateLimitError("Limite atingido."),
            ),
            patch("medasist.ui.app.st.session_state", session),
            patch("medasist.ui.app._handle_error") as mock_handle,
        ):
            _render_streaming("Pergunta?", "medico", None, _SETTINGS)

        mock_handle.assert_called_once()
        assert session["messages"] == []
