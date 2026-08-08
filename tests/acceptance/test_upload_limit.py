from __future__ import annotations

import io
from collections.abc import Generator
from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from medasist.config import Settings

MB = 1024 * 1024
ADMIN_KEY = "test-admin-key"

# Testes de aceitação do FIX-05 — limite de upload no ``POST /ingest``.
#
# Verifica o feature de ponta a ponta pela API (HTTP), cobrindo os critérios
# de aceite UPL-01..08: happy path, limite excedido, boundary inclusivo,
# falhas, precedência de auth, configurabilidade e não-escrita de bytes
# excedentes no pipeline de ingestão.


@dataclass
class _IngestResult:
    """Resultado sintético de ingestão (dados fictícios)."""

    sha256: str = "abc123"
    chunks_indexed: int = 5
    skipped: bool = False
    error: str | None = None


def _make_upload(size: int, filename: str = "bula_teste.pdf") -> dict:
    """Monta payload multipart de upload em memória com ``size`` bytes."""
    content = b"%PDF-1.4 fake content" if size == 0 else b"x" * size
    return {"file": (filename, io.BytesIO(content), "application/pdf")}


def _patch_settings(mb: int) -> patch:
    """Patch de ``get_settings`` com um Settings real de pequeno limite."""
    settings = Settings(
        max_upload_mb=mb,
        admin_api_key=SecretStr(ADMIN_KEY),
    )
    return patch("medasist.api.routers.ingest.get_settings", return_value=settings)


@pytest.fixture()
def client() -> Generator[TestClient, None, None]:
    """TestClient com lifespan mockado (sem ChromaDB nem LLM reais)."""
    mock_chain = MagicMock()
    chains: dict[object, MagicMock] = {}

    with (
        patch("medasist.api.main.get_all_vectorstores") as mock_stores,
        patch("medasist.api.main.build_chain") as mock_build,
    ):
        mock_stores.return_value = {}
        mock_build.side_effect = lambda stores, profile, settings: chains.setdefault(
            profile, mock_chain
        )

        from medasist.api.main import app

        with TestClient(app) as c:
            yield c


class TestUploadLimitAcceptance:
    """Critérios de aceite UPL-01..08 via HTTP (POST /ingest)."""

    def test_HP1_valid_admin_and_file_within_limit_returns_200(
        self, client: TestClient
    ) -> None:
        """HP1: admin válido + arquivo dentro do limite → 200 IngestResponse."""
        with (
            _patch_settings(mb=1),
            patch(
                "medasist.api.routers.ingest.ingest_document",
                return_value=_IngestResult(sha256="deadbeef", chunks_indexed=3),
            ),
            patch("medasist.api.routers.ingest.get_client"),
        ):
            response = client.post(
                "/ingest?doc_type=bula",
                files=_make_upload(1000),
                headers={"X-Admin-Key": ADMIN_KEY},
            )

        assert response.status_code == 200
        payload = response.json()
        assert payload["sha256"] == "deadbeef"
        assert payload["chunks_indexed"] == 3
        assert payload["doc_type"] == "bula"

    def test_HP2_file_exceeding_limit_returns_413_and_skips_ingest_document(
        self, client: TestClient
    ) -> None:
        """HP2: arquivo excedendo o limite → 413 com detalhe claro.

        ``ingest_document`` NÃO deve ser chamado no caminho de 413.
        """
        with (
            _patch_settings(mb=1),
            patch("medasist.api.routers.ingest.ingest_document") as mock_ingest,
            patch("medasist.api.routers.ingest.get_client"),
        ):
            response = client.post(
                "/ingest?doc_type=bula",
                files=_make_upload(MB + 1),
                headers={"X-Admin-Key": ADMIN_KEY},
            )

        assert response.status_code == 413
        assert "Arquivo excede o limite máximo de 1 MB." in response.json()["detail"]
        mock_ingest.assert_not_called()

    def test_HP3_file_exactly_at_limit_returns_200_boundary_inclusive(
        self, client: TestClient
    ) -> None:
        """HP3: arquivo exatamente no limite → 200 (boundary inclusivo)."""
        with (
            _patch_settings(mb=1),
            patch(
                "medasist.api.routers.ingest.ingest_document",
                return_value=_IngestResult(),
            ),
            patch("medasist.api.routers.ingest.get_client"),
        ):
            response = client.post(
                "/ingest?doc_type=bula",
                files=_make_upload(MB),
                headers={"X-Admin-Key": ADMIN_KEY},
            )

        assert response.status_code == 200

    def test_FP1_oversized_returns_413_without_processing(
        self, client: TestClient
    ) -> None:
        """FP1: arquivo excedente → 413, sem processamento de pipeline."""
        with (
            _patch_settings(mb=1),
            patch("medasist.api.routers.ingest.ingest_document") as mock_ingest,
            patch("medasist.api.routers.ingest.get_client") as mock_get_client,
        ):
            response = client.post(
                "/ingest?doc_type=bula",
                files=_make_upload(MB * 3),
                headers={"X-Admin-Key": ADMIN_KEY},
            )

        assert response.status_code == 413
        mock_ingest.assert_not_called()
        mock_get_client.assert_not_called()

    def test_FP2_invalid_admin_key_returns_401_auth_preserved(
        self, client: TestClient
    ) -> None:
        """FP2: chave de admin inválida → 401 (auth existente preservada).

        Chave ausente (header obrigatório) resulta em 422 pela validação
        FastAPI; chave incorreta → 401. O core verificado é o 401.
        """
        with (
            _patch_settings(mb=1),
            patch("medasist.api.routers.ingest.ingest_document") as mock_ingest,
            patch("medasist.api.routers.ingest.get_client"),
        ):
            response = client.post(
                "/ingest?doc_type=bula",
                files=_make_upload(1000),
                headers={"X-Admin-Key": "wrong-key"},
            )

        assert response.status_code == 401
        assert response.json()["detail"] == "Chave de admin inválida."
        mock_ingest.assert_not_called()

    def test_FP3_oversized_and_invalid_key_returns_401_auth_precedes_size(
        self, client: TestClient
    ) -> None:
        """FP3: excedente E chave inválida → 401 (auth precede o tamanho)."""
        with (
            _patch_settings(mb=1),
            patch("medasist.api.routers.ingest.ingest_document") as mock_ingest,
            patch("medasist.api.routers.ingest.get_client"),
        ):
            response = client.post(
                "/ingest?doc_type=bula",
                files=_make_upload(MB + 1),
                headers={"X-Admin-Key": "wrong-key"},
            )

        assert response.status_code == 401
        mock_ingest.assert_not_called()

    def test_BR1_limit_configurable_via_settings_env_default_25(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """BR1: limite configurável via Settings/env com default 25."""
        monkeypatch.delenv("MAX_UPLOAD_MB", raising=False)
        assert Settings().max_upload_mb == 25

        monkeypatch.setenv("MAX_UPLOAD_MB", "3")
        assert Settings().max_upload_mb == 3

        assert Settings(max_upload_mb=7).max_upload_mb == 7

    def test_BR2_exceeding_returns_413_not_fake_200_with_processing_error(
        self, client: TestClient
    ) -> None:
        """BR2: excedente → 413, nunca 200 falso com erro de processamento."""
        with (
            _patch_settings(mb=1),
            patch(
                "medasist.api.routers.ingest.ingest_document",
                return_value=_IngestResult(error="Nunca deve ser invocado."),
            ),
            patch("medasist.api.routers.ingest.get_client"),
        ):
            response = client.post(
                "/ingest?doc_type=bula",
                files=_make_upload(MB + 1),
                headers={"X-Admin-Key": ADMIN_KEY},
            )

        assert response.status_code == 413
        assert response.status_code != 200
        assert "Arquivo excede o limite máximo de 1 MB." in response.json()["detail"]

    def test_BR3_oversized_bytes_not_written_or_passed_to_pipeline(
        self, client: TestClient
    ) -> None:
        """BR3: verificação no boundary; bytes excedentes não vão ao pipeline.

        Verifica que, no caminho 413, o padrão é checado no stream e o
        ``ingest_document`` nunca recebe o conteúdo excedente.
        """
        written: list[int] = []

        def _record_and_return(*args, **kwargs) -> _IngestResult:
            written.append(1)
            return _IngestResult()

        with (
            _patch_settings(mb=1),
            patch(
                "medasist.api.routers.ingest.ingest_document",
                side_effect=_record_and_return,
            ) as mock_ingest,
            patch("medasist.api.routers.ingest.get_client"),
        ):
            oversized = client.post(
                "/ingest?doc_type=bula",
                files=_make_upload(MB * 2),
                headers={"X-Admin-Key": ADMIN_KEY},
            )
            within = client.post(
                "/ingest?doc_type=bula",
                files=_make_upload(1000),
                headers={"X-Admin-Key": ADMIN_KEY},
            )

        assert oversized.status_code == 413
        assert within.status_code == 200
        assert mock_ingest.call_count == 1
        assert len(written) == 1
