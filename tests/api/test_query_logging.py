from __future__ import annotations

import logging

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
