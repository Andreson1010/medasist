"""Testes unitários dos helpers de UI em ``medasist.ui.app``.

Sem spawnar a aplicação Streamlit: mocka ``st.session_state`` e as chamadas
de ``st.warning``, além de ``get_health`` do client. Dados sintéticos.
"""

from __future__ import annotations

from unittest.mock import patch

from medasist.ui.app import _check_and_warn_health

_BASE_URL = "http://test-api"

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
