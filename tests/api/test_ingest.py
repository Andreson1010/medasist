from __future__ import annotations

import io
from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@dataclass
class _IngestResult:
    sha256: str = "abc123"
    chunks_indexed: int = 5
    skipped: bool = False
    error: str | None = None


def _make_pdf_upload(filename: str = "bula_teste.pdf") -> dict:
    """Cria payload de upload simulando um arquivo PDF."""
    return {"file": (filename, io.BytesIO(b"%PDF-1.4 fake content"), "application/pdf")}


class TestIngestAuth:
    def test_missing_admin_key_returns_422(self, client: TestClient) -> None:
        response = client.post(
            "/ingest?doc_type=bula",
            files=_make_pdf_upload(),
        )
        assert response.status_code == 422

    def test_wrong_admin_key_returns_401(self, client: TestClient) -> None:
        mock_settings = MagicMock()
        mock_settings.admin_api_key.get_secret_value.return_value = "correct-key"

        with patch("medasist.api.routers.ingest.get_settings", return_value=mock_settings):
            response = client.post(
                "/ingest?doc_type=bula",
                files=_make_pdf_upload(),
                headers={"X-Admin-Key": "wrong-key"},
            )

        assert response.status_code == 401

    def test_correct_admin_key_accepted(
        self,
        ingest_client: tuple[TestClient, dict[str, str]],
    ) -> None:
        client, headers = ingest_client

        with (
            patch("medasist.api.routers.ingest.ingest_document") as mock_ingest,
            patch("medasist.api.routers.ingest.get_client"),
        ):
            mock_ingest.return_value = _IngestResult()
            response = client.post(
                "/ingest?doc_type=bula",
                files=_make_pdf_upload(),
                headers=headers,
            )

        assert response.status_code == 200


class TestIngestHappyPath:
    def test_returns_200(
        self,
        ingest_client: tuple[TestClient, dict[str, str]],
    ) -> None:
        client, headers = ingest_client
        with (
            patch("medasist.api.routers.ingest.ingest_document", return_value=_IngestResult()),
            patch("medasist.api.routers.ingest.get_client"),
        ):
            response = client.post("/ingest?doc_type=bula", files=_make_pdf_upload(), headers=headers)
        assert response.status_code == 200

    def test_response_contains_sha256(
        self,
        ingest_client: tuple[TestClient, dict[str, str]],
    ) -> None:
        client, headers = ingest_client
        with (
            patch("medasist.api.routers.ingest.ingest_document", return_value=_IngestResult(sha256="deadbeef")),
            patch("medasist.api.routers.ingest.get_client"),
        ):
            response = client.post("/ingest?doc_type=bula", files=_make_pdf_upload(), headers=headers)
        assert response.json()["sha256"] == "deadbeef"

    def test_response_contains_chunks_indexed(
        self,
        ingest_client: tuple[TestClient, dict[str, str]],
    ) -> None:
        client, headers = ingest_client
        with (
            patch("medasist.api.routers.ingest.ingest_document", return_value=_IngestResult(chunks_indexed=10)),
            patch("medasist.api.routers.ingest.get_client"),
        ):
            response = client.post("/ingest?doc_type=bula", files=_make_pdf_upload(), headers=headers)
        assert response.json()["chunks_indexed"] == 10

    def test_skipped_false_on_new_document(
        self,
        ingest_client: tuple[TestClient, dict[str, str]],
    ) -> None:
        client, headers = ingest_client
        with (
            patch("medasist.api.routers.ingest.ingest_document", return_value=_IngestResult()),
            patch("medasist.api.routers.ingest.get_client"),
        ):
            response = client.post("/ingest?doc_type=bula", files=_make_pdf_upload(), headers=headers)
        assert response.json()["skipped"] is False


class TestIngestSkipped:
    def test_skipped_true_for_duplicate(
        self,
        ingest_client: tuple[TestClient, dict[str, str]],
    ) -> None:
        client, headers = ingest_client
        with (
            patch("medasist.api.routers.ingest.ingest_document", return_value=_IngestResult(skipped=True, chunks_indexed=0)),
            patch("medasist.api.routers.ingest.get_client"),
        ):
            response = client.post("/ingest?doc_type=bula", files=_make_pdf_upload(), headers=headers)
        assert response.status_code == 200
        assert response.json()["skipped"] is True


class TestIngestError:
    def test_pipeline_error_returns_500(
        self,
        ingest_client: tuple[TestClient, dict[str, str]],
    ) -> None:
        client, headers = ingest_client
        with (
            patch("medasist.api.routers.ingest.ingest_document", return_value=_IngestResult(error="Falha ao processar PDF.")),
            patch("medasist.api.routers.ingest.get_client"),
        ):
            response = client.post("/ingest?doc_type=bula", files=_make_pdf_upload(), headers=headers)
        assert response.status_code == 500
        assert response.json()["detail"] == "Falha ao processar o documento."
