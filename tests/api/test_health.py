from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import chromadb
import httpx
import pytest
from fastapi.testclient import TestClient

from medasist.api.health import check_chromadb, check_dependencies, check_lm_studio
from medasist.api.schemas import DependencyHealth, DependencyStatus
from medasist.config import Settings


def _health(
    status: str = "ok",
    details: str = "saudável",
    latency_ms: int = 1,
) -> DependencyHealth:
    """Constrói DependencyHealth sintético para probes mockados."""
    return DependencyHealth(
        status=DependencyStatus(status),
        details=details,
        latency_ms=latency_ms,
    )


def _chroma_collection(name: str) -> SimpleNamespace:
    """Fake de coleção ChromaDB com apenas o atributo ``name``."""
    return SimpleNamespace(name=name)


def _all_collections(settings: Settings) -> list[SimpleNamespace]:
    """Retorna as 4 coleções esperadas de Settings."""
    return [
        _chroma_collection(settings.collection_bulas),
        _chroma_collection(settings.collection_diretrizes),
        _chroma_collection(settings.collection_protocolos),
        _chroma_collection(settings.collection_manuais),
    ]


class TestHealthEndpoint:
    def test_health_returns_200_ok(self, client: TestClient) -> None:
        response = client.get("/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["chromadb"]["status"] == "ok"
        assert body["lm_studio"]["status"] == "ok"

    def test_health_includes_latency_ms_integers(self, client: TestClient) -> None:
        response = client.get("/health")
        body = response.json()
        assert isinstance(body["chromadb"]["latency_ms"], int)
        assert isinstance(body["lm_studio"]["latency_ms"], int)
        assert body["chromadb"]["details"]
        assert body["lm_studio"]["details"]

    def test_health_lm_studio_down_is_degraded(self, client: TestClient) -> None:
        with patch(
            "medasist.api.health.check_lm_studio",
            return_value=_health("unavailable", "LM Studio fora do ar"),
        ):
            response = client.get("/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "degraded"
        assert body["chromadb"]["status"] == "ok"
        assert body["lm_studio"]["status"] == "unavailable"
        assert body["lm_studio"]["details"] == "LM Studio fora do ar"

    def test_health_chromadb_down_is_degraded(self, client: TestClient) -> None:
        with patch(
            "medasist.api.health.check_chromadb",
            return_value=_health("unavailable", "ChromaDB fora do ar"),
        ):
            response = client.get("/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "degraded"
        assert body["chromadb"]["status"] == "unavailable"
        assert body["lm_studio"]["status"] == "ok"

    def test_health_both_down_is_degraded(self, client: TestClient) -> None:
        with (
            patch(
                "medasist.api.health.check_chromadb",
                return_value=_health("unavailable", "ChromaDB fora do ar"),
            ),
            patch(
                "medasist.api.health.check_lm_studio",
                return_value=_health("unavailable", "LM Studio fora do ar"),
            ),
        ):
            response = client.get("/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "degraded"
        assert body["chromadb"]["status"] == "unavailable"
        assert body["lm_studio"]["status"] == "unavailable"


class TestCheckChromaDB:
    def test_ok_when_all_collections_present(self, settings: Settings) -> None:
        client = MagicMock()
        client.heartbeat.return_value = 123456789
        client.list_collections.return_value = _all_collections(settings)

        with patch("medasist.api.health.get_client", return_value=client):
            health = check_chromadb(settings, timeout=3.0)

        assert health.status is DependencyStatus.OK
        assert isinstance(health.latency_ms, int)

    def test_degraded_when_collections_missing(self, settings: Settings) -> None:
        client = MagicMock()
        client.heartbeat.return_value = 123456789
        client.list_collections.return_value = [
            _chroma_collection(settings.collection_bulas)
        ]

        with patch("medasist.api.health.get_client", return_value=client):
            health = check_chromadb(settings, timeout=3.0)

        assert health.status is DependencyStatus.DEGRADED
        assert "coleções ausentes" in health.details
        assert settings.collection_manuais in health.details
        assert isinstance(health.latency_ms, int)

    def test_degraded_when_no_collections(self, settings: Settings) -> None:
        client = MagicMock()
        client.heartbeat.return_value = 123456789
        client.list_collections.return_value = []

        with patch("medasist.api.health.get_client", return_value=client):
            health = check_chromadb(settings, timeout=3.0)

        assert health.status is DependencyStatus.DEGRADED
        assert health.details.count("coleções ausentes") == 1

    def test_unavailable_when_heartbeat_raises(self, settings: Settings) -> None:
        client = MagicMock()
        client.heartbeat.side_effect = RuntimeError("conexão recusada")

        with patch("medasist.api.health.get_client", return_value=client):
            health = check_chromadb(settings, timeout=3.0)

        assert health.status is DependencyStatus.UNAVAILABLE
        assert "conexão recusada" in health.details

    def test_unavailable_when_list_collections_raises(self, settings: Settings) -> None:
        client = MagicMock()
        client.heartbeat.return_value = 123456789
        client.list_collections.side_effect = RuntimeError("lock perdido")

        with patch("medasist.api.health.get_client", return_value=client):
            health = check_chromadb(settings, timeout=3.0)

        assert health.status is DependencyStatus.UNAVAILABLE
        assert "lock perdido" in health.details

    def test_degraded_with_real_chroma_when_collections_missing(
        self, tmp_path, settings: Settings
    ) -> None:
        """Probe contra ChromaDB persistente real sem coleções → degraded."""
        client = chromadb.PersistentClient(path=str(tmp_path / "chroma"))

        with patch("medasist.api.health.get_client", return_value=client):
            health = check_chromadb(settings, timeout=3.0)

        assert health.status is DependencyStatus.DEGRADED
        assert "coleções ausentes" in health.details

    def test_failure_logged_with_percent_s(
        self, settings: Settings, caplog: pytest.LogCaptureFixture
    ) -> None:
        client = MagicMock()
        client.heartbeat.side_effect = RuntimeError("boom")

        with (
            patch("medasist.api.health.get_client", return_value=client),
            caplog.at_level(logging.ERROR, logger="medasist.api.health"),
        ):
            check_chromadb(settings, timeout=3.0)

        assert any("ChromaDB inacessível" in r.getMessage() for r in caplog.records)


class TestCheckLMStudio:
    def test_ok_when_http_200(self, settings: Settings) -> None:
        with patch("medasist.api.health.httpx.get") as mock_get:
            mock_get.return_value = MagicMock(status_code=200)
            health = check_lm_studio(settings, timeout=3.0)

        assert health.status is DependencyStatus.OK
        assert isinstance(health.latency_ms, int)

    def test_timeout_returns_unavailable(self, settings: Settings) -> None:
        with patch(
            "medasist.api.health.httpx.get",
            side_effect=httpx.TimeoutException("took too long"),
        ):
            health = check_lm_studio(settings, timeout=3.0)

        assert health.status is DependencyStatus.UNAVAILABLE
        assert "timeout" in health.details
        assert isinstance(health.latency_ms, int)

    def test_connect_error_returns_unavailable(self, settings: Settings) -> None:
        with patch(
            "medasist.api.health.httpx.get",
            side_effect=httpx.ConnectError("conexão recusada"),
        ):
            health = check_lm_studio(settings, timeout=3.0)

        assert health.status is DependencyStatus.UNAVAILABLE
        assert "conexão recusada" in health.details

    def test_non_2xx_returns_unavailable(self, settings: Settings) -> None:
        with patch("medasist.api.health.httpx.get") as mock_get:
            mock_get.return_value = MagicMock(status_code=503)
            health = check_lm_studio(settings, timeout=3.0)

        assert health.status is DependencyStatus.UNAVAILABLE
        assert "HTTP 503" in health.details

    def test_calls_models_endpoint_with_timeout(self, settings: Settings) -> None:
        with patch("medasist.api.health.httpx.get") as mock_get:
            mock_get.return_value = MagicMock(status_code=200)
            check_lm_studio(settings, timeout=2.5)

        mock_get.assert_called_once_with(
            f"{settings.lm_studio_base_url}/models", timeout=2.5
        )


class TestCheckDependencies:
    def test_ok_when_both_healthy(self, settings: Settings) -> None:
        with (
            patch(
                "medasist.api.health.check_chromadb",
                return_value=_health(),
            ),
            patch(
                "medasist.api.health.check_lm_studio",
                return_value=_health(),
            ),
        ):
            result = check_dependencies(settings)

        assert result.status == "ok"
        assert result.chromadb.status is DependencyStatus.OK
        assert result.lm_studio.status is DependencyStatus.OK

    def test_degraded_when_chromadb_down(self, settings: Settings) -> None:
        with (
            patch(
                "medasist.api.health.check_chromadb",
                return_value=_health("unavailable"),
            ),
            patch(
                "medasist.api.health.check_lm_studio",
                return_value=_health(),
            ),
        ):
            result = check_dependencies(settings)

        assert result.status == "degraded"
        assert result.chromadb.status is DependencyStatus.UNAVAILABLE
        assert result.lm_studio.status is DependencyStatus.OK

    def test_degraded_when_lm_studio_down(self, settings: Settings) -> None:
        with (
            patch(
                "medasist.api.health.check_chromadb",
                return_value=_health(),
            ),
            patch(
                "medasist.api.health.check_lm_studio",
                return_value=_health("unavailable"),
            ),
        ):
            result = check_dependencies(settings)

        assert result.status == "degraded"
        assert result.chromadb.status is DependencyStatus.OK
        assert result.lm_studio.status is DependencyStatus.UNAVAILABLE

    def test_passes_healthcheck_timeout_to_probes(self, settings: Settings) -> None:
        with (
            patch(
                "medasist.api.health.check_chromadb",
                return_value=_health(),
            ) as mock_chromadb,
            patch(
                "medasist.api.health.check_lm_studio",
                return_value=_health(),
            ) as mock_lm,
        ):
            check_dependencies(settings)

        mock_chromadb.assert_called_once_with(settings, settings.healthcheck_timeout)
        mock_lm.assert_called_once_with(settings, settings.healthcheck_timeout)
