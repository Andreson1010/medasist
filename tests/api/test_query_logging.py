from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient


class TestQueryLogging:
    def test_log_includes_latency_and_doc_types(
        self, client: TestClient, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Log do /query inclui latency_ms e doc_types (com cold_start e citations)."""
        with caplog.at_level(logging.INFO, logger="medasist.api.routers.query"):
            response = client.post(
                "/query",
                json={
                    "question": "qual a dose de Zolatril?",
                    "profile": "medico",
                    "doc_types": ["bula"],
                },
            )

        assert response.status_code == 200
        records = [r for r in caplog.records if r.getMessage().startswith("query:")]
        assert records, "nenhum record de query capturado"
        message = records[0].getMessage()
        assert "latency_ms=" in message
        assert "doc_types=['bula']" in message
        assert "cold_start=False" in message
        assert "citations=1" in message
        assert "profile='medico'" in message

    def test_log_doc_types_none_when_not_filtered(
        self, client: TestClient, caplog: pytest.LogCaptureFixture
    ) -> None:
        """doc_types ausente no request é refletido como None no log."""
        with caplog.at_level(logging.INFO, logger="medasist.api.routers.query"):
            response = client.post(
                "/query",
                json={"question": "qual a dose?", "profile": "medico"},
            )

        assert response.status_code == 200
        records = [r for r in caplog.records if r.getMessage().startswith("query:")]
        assert records, "nenhum record de query capturado"
        assert "doc_types=None" in records[0].getMessage()


class TestQueryStreamLogging:
    def test_stream_log_includes_profile_cold_start_citations_and_latency(
        self, streaming_client: TestClient, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Log do /query/stream inclui profile, cold_start, citations e latency."""
        with caplog.at_level(logging.INFO, logger="medasist.api.routers.query"):
            response = streaming_client.post(
                "/query/stream",
                json={
                    "question": "qual a dose de Zolatril?",
                    "profile": "medico",
                    "doc_types": ["bula"],
                },
            )

        assert response.status_code == 200
        records = [
            r for r in caplog.records if r.getMessage().startswith("query/stream:")
        ]
        assert records, "nenhum record de query/stream capturado"
        message = records[0].getMessage()
        assert "profile='medico'" in message
        assert "cold_start=False" in message
        assert "citations=1" in message
        assert "latency_ms=" in message
        assert "doc_types=['bula']" in message

    def test_stream_cold_start_log_reflects_flag(
        self,
        streaming_client_factory,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Cold start por retrieval vazio é refletido como cold_start=True no log."""
        from medasist.profiles.schemas import UserProfile

        def _cold(question: str, doc_types=None):  # type: ignore[no-untyped-def]
            def gen() -> None:
                yield from ()
                return [], True

            return gen()

        chains = {p: MagicMock(side_effect=_cold) for p in UserProfile}

        with (
            caplog.at_level(logging.INFO, logger="medasist.api.routers.query"),
            streaming_client_factory(chains) as c,
        ):
            response = c.post(
                "/query/stream",
                json={"question": "qual a dose?", "profile": "medico"},
            )

        assert response.status_code == 200
        records = [
            r for r in caplog.records if r.getMessage().startswith("query/stream:")
        ]
        assert records, "nenhum record de query/stream capturado"
        message = records[0].getMessage()
        assert "cold_start=True" in message
        assert "citations=0" in message
