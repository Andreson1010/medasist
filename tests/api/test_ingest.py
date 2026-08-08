from __future__ import annotations

import io
from dataclasses import dataclass
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from pydantic import SecretStr

from medasist.config import Settings


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

    def test_empty_admin_key_returns_401(self, client: TestClient) -> None:
        response = client.post(
            "/ingest?doc_type=bula",
            files=_make_pdf_upload(),
            headers={"X-Admin-Key": ""},
        )
        assert response.status_code == 401

    def test_whitespace_admin_key_returns_401(self, client: TestClient) -> None:
        response = client.post(
            "/ingest?doc_type=bula",
            files=_make_pdf_upload(),
            headers={"X-Admin-Key": "   "},
        )
        assert response.status_code == 401

    def test_wrong_admin_key_returns_401(self, client: TestClient) -> None:
        mock_settings = MagicMock()
        mock_settings.admin_api_key.get_secret_value.return_value = (
            "correct-key-0123456789"
        )

        with patch(
            "medasist.api.routers.ingest.get_settings", return_value=mock_settings
        ):
            response = client.post(
                "/ingest?doc_type=bula",
                files=_make_pdf_upload(),
                headers={"X-Admin-Key": "wrong-key-9876543210"},
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
    def test_get_client_called_with_settings(
        self,
        ingest_client: tuple[TestClient, dict[str, str]],
    ) -> None:
        """get_client deve ser chamado com settings, não sem argumentos."""
        client, headers = ingest_client
        with (
            patch(
                "medasist.api.routers.ingest.ingest_document",
                return_value=_IngestResult(),
            ),
            patch("medasist.api.routers.ingest.get_client") as mock_get_client,
        ):
            response = client.post(
                "/ingest?doc_type=bula", files=_make_pdf_upload(), headers=headers
            )
        assert response.status_code == 200
        mock_get_client.assert_called_once()
        call_args = mock_get_client.call_args
        assert (
            call_args.args or call_args.kwargs
        ), "get_client foi chamado sem argumentos — bug FIX-01"

    def test_returns_200(
        self,
        ingest_client: tuple[TestClient, dict[str, str]],
    ) -> None:
        client, headers = ingest_client
        with (
            patch(
                "medasist.api.routers.ingest.ingest_document",
                return_value=_IngestResult(),
            ),
            patch("medasist.api.routers.ingest.get_client"),
        ):
            response = client.post(
                "/ingest?doc_type=bula", files=_make_pdf_upload(), headers=headers
            )
        assert response.status_code == 200

    def test_response_contains_sha256(
        self,
        ingest_client: tuple[TestClient, dict[str, str]],
    ) -> None:
        client, headers = ingest_client
        with (
            patch(
                "medasist.api.routers.ingest.ingest_document",
                return_value=_IngestResult(sha256="deadbeef"),
            ),
            patch("medasist.api.routers.ingest.get_client"),
        ):
            response = client.post(
                "/ingest?doc_type=bula", files=_make_pdf_upload(), headers=headers
            )
        assert response.json()["sha256"] == "deadbeef"

    def test_response_contains_chunks_indexed(
        self,
        ingest_client: tuple[TestClient, dict[str, str]],
    ) -> None:
        client, headers = ingest_client
        with (
            patch(
                "medasist.api.routers.ingest.ingest_document",
                return_value=_IngestResult(chunks_indexed=10),
            ),
            patch("medasist.api.routers.ingest.get_client"),
        ):
            response = client.post(
                "/ingest?doc_type=bula", files=_make_pdf_upload(), headers=headers
            )
        assert response.json()["chunks_indexed"] == 10

    def test_skipped_false_on_new_document(
        self,
        ingest_client: tuple[TestClient, dict[str, str]],
    ) -> None:
        client, headers = ingest_client
        with (
            patch(
                "medasist.api.routers.ingest.ingest_document",
                return_value=_IngestResult(),
            ),
            patch("medasist.api.routers.ingest.get_client"),
        ):
            response = client.post(
                "/ingest?doc_type=bula", files=_make_pdf_upload(), headers=headers
            )
        assert response.json()["skipped"] is False


class TestIngestSkipped:
    def test_skipped_true_for_duplicate(
        self,
        ingest_client: tuple[TestClient, dict[str, str]],
    ) -> None:
        client, headers = ingest_client
        with (
            patch(
                "medasist.api.routers.ingest.ingest_document",
                return_value=_IngestResult(skipped=True, chunks_indexed=0),
            ),
            patch("medasist.api.routers.ingest.get_client"),
        ):
            response = client.post(
                "/ingest?doc_type=bula", files=_make_pdf_upload(), headers=headers
            )
        assert response.status_code == 200
        assert response.json()["skipped"] is True


class TestIngestError:
    def test_pipeline_error_returns_500(
        self,
        ingest_client: tuple[TestClient, dict[str, str]],
    ) -> None:
        client, headers = ingest_client
        with (
            patch(
                "medasist.api.routers.ingest.ingest_document",
                return_value=_IngestResult(error="Falha ao processar PDF."),
            ),
            patch("medasist.api.routers.ingest.get_client"),
        ):
            response = client.post(
                "/ingest?doc_type=bula", files=_make_pdf_upload(), headers=headers
            )
        assert response.status_code == 500
        assert response.json()["detail"] == "Falha ao processar o documento."


class TestUploadLimit:
    """Testa o limite de upload (streaming early-abort) no POST /ingest.

    Define ``max_upload_mb=1`` via um Settings real para testar com
    payloads pequenos (1 MB = 1048576 bytes).
    """

    ADMIN_KEY = "test-admin-key-0123456789"
    MB = 1024 * 1024

    def _patch_settings(self, mb: int = 1) -> patch:
        settings = Settings(
            max_upload_mb=mb,
            admin_api_key=SecretStr(self.ADMIN_KEY),
        )
        return patch("medasist.api.routers.ingest.get_settings", return_value=settings)

    @staticmethod
    def _upload(size: int) -> dict:
        return {"file": ("bula_teste.pdf", io.BytesIO(b"x" * size), "application/pdf")}

    def test_oversized_returns_413_and_skips_ingest_document(
        self, client: TestClient
    ) -> None:
        with (
            self._patch_settings(mb=1),
            patch("medasist.api.routers.ingest.ingest_document") as mock_ingest,
            patch("medasist.api.routers.ingest.get_client"),
        ):
            response = client.post(
                "/ingest?doc_type=bula",
                files=self._upload(self.MB + 1),
                headers={"X-Admin-Key": self.ADMIN_KEY},
            )
        assert response.status_code == 413
        assert "Arquivo excede o limite máximo de 1 MB." in response.json()["detail"]
        mock_ingest.assert_not_called()

    def test_exactly_at_limit_is_accepted(self, client: TestClient) -> None:
        with (
            self._patch_settings(mb=1),
            patch(
                "medasist.api.routers.ingest.ingest_document",
                return_value=_IngestResult(),
            ),
            patch("medasist.api.routers.ingest.get_client"),
        ):
            response = client.post(
                "/ingest?doc_type=bula",
                files=self._upload(self.MB),
                headers={"X-Admin-Key": self.ADMIN_KEY},
            )
        assert response.status_code == 200

    def test_within_limit_returns_200_and_writes_full_file(
        self, client: TestClient
    ) -> None:
        written: list[bytes] = []

        def _capture(path, **kwargs):
            written.append(path.read_bytes())
            return _IngestResult()

        with (
            self._patch_settings(mb=1),
            patch(
                "medasist.api.routers.ingest.ingest_document",
                side_effect=_capture,
            ),
            patch("medasist.api.routers.ingest.get_client"),
        ):
            response = client.post(
                "/ingest?doc_type=bula",
                files=self._upload(1000),
                headers={"X-Admin-Key": self.ADMIN_KEY},
            )
        assert response.status_code == 200
        assert len(written) == 1
        assert len(written[0]) == 1000

    def test_auth_precedes_size_check(self, client: TestClient) -> None:
        settings = Settings(
            max_upload_mb=1,
            admin_api_key=SecretStr(self.ADMIN_KEY),
        )
        with (
            patch("medasist.api.routers.ingest.get_settings", return_value=settings),
            patch("medasist.api.routers.ingest.ingest_document") as mock_ingest,
            patch("medasist.api.routers.ingest.get_client"),
        ):
            response = client.post(
                "/ingest?doc_type=bula",
                files=self._upload(self.MB + 1),
                headers={"X-Admin-Key": "wrong-key"},
            )
        assert response.status_code == 401
        mock_ingest.assert_not_called()

    def test_empty_file_within_limit_is_processed(self, client: TestClient) -> None:
        with (
            self._patch_settings(mb=1),
            patch(
                "medasist.api.routers.ingest.ingest_document",
                return_value=_IngestResult(),
            ),
            patch("medasist.api.routers.ingest.get_client"),
        ):
            response = client.post(
                "/ingest?doc_type=bula",
                files=self._upload(0),
                headers={"X-Admin-Key": self.ADMIN_KEY},
            )
        assert response.status_code == 200
