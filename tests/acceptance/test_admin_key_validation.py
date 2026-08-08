from __future__ import annotations

import io
from collections.abc import Generator
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from medasist.config import Settings, admin_key_is_weak

ADMIN_KEY = "test-admin-key-0123456789"

# Testes de aceitação do FIX-06 — validação da admin key no ``POST /ingest``.
#
# Verifica a feature de ponta a ponta: (a) validação da ``Settings`` na
# construção (chaves fracas/placeholder rejeitadas), (b) comportamento do
# helper ``admin_key_is_weak`` e (c) autenticação via header ``X-Admin-Key``
# sobre HTTP. Cobre os critérios ADK-01..09 do spec de segurança.
#
# ADK-03 (warning do lifespan) é coberto pela suíte unitária em
# ``tests/api/test_main_lifespan.py``; aqui apenas referencia-se a cobertura.


class TestConfigValidationAcceptance:
    """ADK-01/BR1 e ADK-02/FP1: validação da chave na construção da Settings."""

    def test_ADK01_br1_settings_rejects_weak_and_accepts_strong(self) -> None:
        """ADK-01/BR1: Settings rejeita <16 chars ou placeholders; aceita forte.

        ``dev-only`` é < 16 e placeholder; ``troque-por-chave-segura`` é
        placeholder explícito; ``0123456789abcdef`` tem exatamente 16 e deve
        ser aceita; chave forte longa também.
        """
        weak_keys = [
            "short",
            "dev-only",
            "troque-por-chave-segura",
            "abcdefghij"[:14],
        ]
        for key in weak_keys:
            with pytest.raises(ValidationError, match="admin_api_key"):
                Settings(admin_api_key=SecretStr(key))

        Settings(admin_api_key=SecretStr("0123456789abcdef"))
        Settings(admin_api_key=SecretStr(ADMIN_KEY))

    def test_ADK02_fp1_weak_key_raises_validation_error_without_leaking_key(
        self,
    ) -> None:
        """ADK-02/FP1: construir Settings com chave fraca → ValidationError.

        O texto do erro não deve conter o valor da chave (ADK-08/BR4).
        """
        weak = "dev-only"
        with pytest.raises(ValidationError) as excinfo:
            Settings(admin_api_key=SecretStr(weak))

        message = str(excinfo.value)
        assert "admin_api_key" in message
        assert weak not in message


class TestAdminKeyHelperAcceptance:
    """ADK-04/BR3: helper ``admin_key_is_weak`` e guard booleano."""

    def test_ADK04_br3_helper_flags_weak_and_accepts_strong(self) -> None:
        """ADK-04/BR3: ``admin_key_is_weak`` classifica corretamente.

        Placeholder e chave curta → ``True``; chave forte → ``False``.
        O guard do endpoint usa ``secrets.compare_digest`` e um teste
        booleano de vazios (sem oracle de comprimento) — o comportamento
        observável (ausente 422 / vazia 401) é verificado via HTTP.
        """
        assert admin_key_is_weak("dev-only") is True
        assert admin_key_is_weak("short") is True
        assert admin_key_is_weak("troque-por-chave-segura") is True
        assert admin_key_is_weak("0123456789abcdef") is False
        assert admin_key_is_weak(ADMIN_KEY) is False


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


def _make_pdf_upload() -> dict:
    """Monta payload multipart de upload com um PDF sintético mínimo."""
    content = b"%PDF-1.4 fake content"
    return {"file": ("bula_teste.pdf", io.BytesIO(content), "application/pdf")}


def _patch_settings(key: str) -> patch:
    """Patch de ``get_settings`` com uma chave de admin dada."""
    mock_settings = MagicMock()
    mock_settings.admin_api_key.get_secret_value.return_value = key
    mock_settings.max_upload_mb = 25
    return patch("medasist.api.routers.ingest.get_settings", return_value=mock_settings)


class TestIngestAuthAcceptance:
    """ADK-05..09: autenticação da admin key via HTTP no ``POST /ingest``."""

    def test_ADK05_fp2_wrong_key_returns_401(self, client: TestClient) -> None:
        """ADK-05/FP2: chave incorreta → 401 (autenticação rejeitada)."""
        with (
            _patch_settings(ADMIN_KEY),
            patch("medasist.api.routers.ingest.ingest_document") as mock_ingest,
            patch("medasist.api.routers.ingest.get_client"),
        ):
            response = client.post(
                "/ingest?doc_type=bula",
                files=_make_pdf_upload(),
                headers={"X-Admin-Key": "wrong-key-9876543210"},
            )

        assert response.status_code == 401
        mock_ingest.assert_not_called()

    def test_ADK06_fp3_edge_empty_and_whitespace_header_returns_401(
        self, client: TestClient
    ) -> None:
        """ADK-06/FP3+edge: header vazio ou só espaços → 401."""
        for bad in ("", "   "):
            with _patch_settings(ADMIN_KEY):
                response = client.post(
                    "/ingest?doc_type=bula",
                    files=_make_pdf_upload(),
                    headers={"X-Admin-Key": bad},
                )
            assert response.status_code == 401

    def test_ADK07_fp4_missing_header_returns_422(self, client: TestClient) -> None:
        """ADK-07/FP4: header ausente → 422 (obrigatório, validação FastAPI)."""
        with (
            _patch_settings(ADMIN_KEY),
            patch("medasist.api.routers.ingest.ingest_document"),
            patch("medasist.api.routers.ingest.get_client"),
        ):
            response = client.post(
                "/ingest?doc_type=bula",
                files=_make_pdf_upload(),
            )

        assert response.status_code == 422

    def test_ADK08_br4_key_never_echoed_in_error_responses(
        self, client: TestClient
    ) -> None:
        """ADK-08/BR4: chave não aparece em ``detail`` nem no corpo do erro.

        Cobre 401 (chave errada, vazia, espaços) e 422 (ausente).
        """
        bad_key = "wrong-key-9876543210"
        for header_value in (bad_key, "", "   "):
            with _patch_settings(ADMIN_KEY):
                response = client.post(
                    "/ingest?doc_type=bula",
                    files=_make_pdf_upload(),
                    headers={"X-Admin-Key": header_value} if header_value != "" else "",
                )
            body = response.text
            assert bad_key not in body
            detail = response.json().get("detail")
            if isinstance(detail, str):
                assert bad_key not in detail

    def test_ADK09_hp1_correct_strong_key_returns_200(self, client: TestClient) -> None:
        """ADK-09/HP1: chave forte correta → 200 e pipeline executado."""
        with (
            _patch_settings(ADMIN_KEY),
            patch("medasist.api.routers.ingest.ingest_document") as mock_ingest,
            patch("medasist.api.routers.ingest.get_client"),
        ):
            mock_ingest.return_value = MagicMock(
                sha256="deadbeef", chunks_indexed=3, skipped=False, error=None
            )
            response = client.post(
                "/ingest?doc_type=bula",
                files=_make_pdf_upload(),
                headers={"X-Admin-Key": ADMIN_KEY},
            )

        assert response.status_code == 200
        assert response.json()["sha256"] == "deadbeef"
        mock_ingest.assert_called_once()
