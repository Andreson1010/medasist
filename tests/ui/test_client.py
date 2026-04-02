from __future__ import annotations

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
    query,
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


# ---------------------------------------------------------------------------
# TestCheckHealth
# ---------------------------------------------------------------------------


class TestCheckHealth:
    def test_returns_true_when_ok(self, base_url: str) -> None:
        mock_cls, _ = _mock_client(200, {"status": "ok"})
        with patch("medasist.ui.client.httpx.Client", mock_cls):
            assert check_health(base_url) is True

    def test_returns_false_when_unhealthy(self, base_url: str) -> None:
        mock_cls, _ = _mock_client(200, {"status": "degraded"})
        with patch("medasist.ui.client.httpx.Client", mock_cls):
            assert check_health(base_url) is False

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
        with patch("medasist.ui.client.httpx.Client", mock_cls):
            with pytest.raises(RateLimitError):
                query("Pergunta?", "medico", base_url=base_url)

    def test_raises_server_error_on_500(self, base_url: str) -> None:
        mock_cls, _ = _mock_client(500)
        with patch("medasist.ui.client.httpx.Client", mock_cls):
            with pytest.raises(ServerError):
                query("Pergunta?", "medico", base_url=base_url)

    def test_raises_server_error_on_503(self, base_url: str) -> None:
        mock_cls, _ = _mock_client(503)
        with patch("medasist.ui.client.httpx.Client", mock_cls):
            with pytest.raises(ServerError):
                query("Pergunta?", "medico", base_url=base_url)

    def test_raises_api_error_on_400(self, base_url: str) -> None:
        mock_cls, _ = _mock_client(400)
        with patch("medasist.ui.client.httpx.Client", mock_cls):
            with pytest.raises(APIError):
                query("Pergunta?", "medico", base_url=base_url)

    def test_raises_timeout_error_on_timeout(self, base_url: str) -> None:
        mock_cls = MagicMock()
        mock_instance = MagicMock()
        mock_instance.post.side_effect = httpx.TimeoutException("timeout")
        mock_cls.return_value.__enter__.return_value = mock_instance
        mock_cls.return_value.__exit__.return_value = False
        with patch("medasist.ui.client.httpx.Client", mock_cls):
            with pytest.raises(RequestTimeoutError):
                query("Pergunta?", "medico", base_url=base_url)
