"""Acceptance tests for OBS-02 (health check de dependências).

Verifica ``GET /health`` de ponta a ponta pela camada HTTP (TestClient).
Diferente de ``tests/api/conftest.py`` (que já patcheia ``check_chromadb`` e
``check_lm_studio`` como saudáveis), estes testes executam os probes REAIS de
``src/medasist/api/health.py``, patcheando apenas o boundary: ``get_client``
(ChromaDB) e ``httpx.get`` (LM Studio), com valores de retorno controlados.
Nenhum arquivo de ``src/`` é modificado. Dados sintéticos — sem dado real de
paciente.

Cobertura por critério de aceitação:
- CA-01: deps saudáveis (patch ok) → HTTP 200, ``status=="ok"``,
  ``chromadb.status=="ok"``, ``lm_studio.status=="ok"``, ``latency_ms`` int.
- CA-02: resposta inclui entradas por dependência (status+details+latency_ms)
  e valida contra o schema Pydantic ``HealthResponse``.
- CA-03: LM Studio indisponível → HTTP 200 (NÃO 503), ``status=="degraded"``,
  ``lm_studio.status=="unavailable"``, ``chromadb.status=="ok"``.
- CA-04: falha no ``heartbeat()`` do ChromaDB → ``status=="degraded"``,
  ``chromadb.status=="unavailable"``, sem traceback vazado ao cliente.
- CA-05: timeout do LM Studio → ``lm_studio.status=="unavailable"`` com
  detalhe mencionando timeout.
- CA-06: ``healthcheck_timeout`` é respeitado (timeout repassado ao
  ``httpx.get``; endpoint responde dentro de tempo razoável).
- CA-07: ``status`` top-level restrito a ``"ok"``/``"degraded"`` em ambos os
  cenários; HTTP 200 (mesmo com ``"degraded"``) mantém a UI no ar
  (``ui/client.py`` `check_health`/`get_health`).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
from fastapi.testclient import TestClient

from medasist.api.schemas import HealthResponse
from medasist.config import Settings
from medasist.profiles.schemas import UserProfile
from medasist.ui.client import check_health, get_health

logger = logging.getLogger(__name__)

_ADMIN_KEY = "test-admin-key-0123456789"
_FAST_TIMEOUT = 0.5


def _settings(**overrides: object) -> Settings:
    """Settings de teste com admin key forte e overrides pontuais.

    Parameters
    ----------
    **overrides
        Campos de Settings a sobrescrever (ex: ``healthcheck_timeout``).

    Returns
    -------
    Settings
        Configurações isoladas para o teste.
    """
    return Settings(admin_api_key=_ADMIN_KEY, **overrides)


def _healthy_chroma(settings: Settings) -> MagicMock:
    """Client ChromaDB fake saudável (heartbeat ok + 4 coleções presentes).

    Parameters
    ----------
    settings : Settings
        Configurações com os nomes das coleções esperadas.

    Returns
    -------
    MagicMock
        Client com ``heartbeat`` retornando ns e ``list_collections``
        retornando as 4 coleções de DocType.
    """
    client = MagicMock()
    client.heartbeat.return_value = 123456789
    client.list_collections.return_value = [
        SimpleNamespace(name=settings.collection_bulas),
        SimpleNamespace(name=settings.collection_diretrizes),
        SimpleNamespace(name=settings.collection_protocolos),
        SimpleNamespace(name=settings.collection_manuais),
    ]
    return client


def _broken_chroma() -> MagicMock:
    """Client ChromaDB fake cujo heartbeat falha (indisponível).

    Returns
    -------
    MagicMock
        Client cujo ``heartbeat`` lança ``RuntimeError``.
    """
    client = MagicMock()
    client.heartbeat.side_effect = RuntimeError("conexão recusada no socket")
    return client


def _ok_lm() -> MagicMock:
    """Resposta fake 200 do ``GET {base_url}/models``.

    Returns
    -------
    MagicMock
        Objeto com ``status_code == 200``.
    """
    return MagicMock(status_code=200)


@contextmanager
def _client(settings: Settings) -> Iterator[TestClient]:
    """TestClient com lifespan mockado e probes REAIS no /health.

    Patcheia o lifespan (get_client/build_embeddings/get_all_vectorstores/
    build_chain) e o ``get_settings`` do módulo main para controlar
    ``healthcheck_timeout``. NÃO patcheia ``check_chromadb``/``check_lm_studio``
    — os probes executam de verdade contra os boundaries mockados.

    Parameters
    ----------
    settings : Settings
        Configurações retornadas por ``get_settings`` dentro da app.

    Yields
    ------
    TestClient
        Cliente de teste com a app já inicializada.
    """
    chains = dict.fromkeys(UserProfile, MagicMock())
    with (
        patch("medasist.api.main.get_settings", return_value=settings),
        patch("medasist.api.main.get_client"),
        patch("medasist.api.main.build_embeddings"),
        patch("medasist.api.main.get_all_vectorstores", return_value={}),
        patch(
            "medasist.api.main.build_chain",
            side_effect=lambda stores, profile, settings: chains[profile],
        ),
    ):
        from medasist.api.main import app

        with TestClient(app) as c:
            yield c


class TestHealthCheckAcceptance:
    """Critérios de aceite CA-01..CA-07 via HTTP (GET /health)."""

    def test_CA01_healthy_dependencies_return_200_ok(self) -> None:
        """CA-01: deps saudáveis → 200, status ok, latency_ms int."""
        settings = _settings()
        with (
            _client(settings) as c,
            patch(
                "medasist.api.health.get_client",
                return_value=_healthy_chroma(settings),
            ),
            patch("medasist.api.health.httpx.get", return_value=_ok_lm()),
        ):
            response = c.get("/health")

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["chromadb"]["status"] == "ok"
        assert body["lm_studio"]["status"] == "ok"
        assert isinstance(body["chromadb"]["latency_ms"], int)
        assert isinstance(body["lm_studio"]["latency_ms"], int)

    def test_CA02_response_validates_against_healthresponse_schema(self) -> None:
        """CA-02: shape Pydantic com status+details+latency_ms por dependência."""
        settings = _settings()
        with (
            _client(settings) as c,
            patch(
                "medasist.api.health.get_client",
                return_value=_healthy_chroma(settings),
            ),
            patch("medasist.api.health.httpx.get", return_value=_ok_lm()),
        ):
            response = c.get("/health")

        assert response.status_code == 200
        body = response.json()
        assert set(body) == {"status", "chromadb", "lm_studio"}
        for dep in ("chromadb", "lm_studio"):
            assert set(body[dep]) == {"status", "details", "latency_ms"}
            assert isinstance(body[dep]["details"], str)
            assert isinstance(body[dep]["latency_ms"], int)
            assert body[dep]["status"] in {"ok", "degraded", "unavailable"}

        validated = HealthResponse.model_validate(body)
        assert validated.status == "ok"
        assert validated.chromadb.status.value == "ok"
        assert validated.lm_studio.status.value == "ok"

    def test_CA03_lm_studio_unavailable_returns_200_degraded(self) -> None:
        """CA-03: LM Studio fora → 200 (não 503), degraded, chromadb ok."""
        settings = _settings()
        with (
            _client(settings) as c,
            patch(
                "medasist.api.health.get_client",
                return_value=_healthy_chroma(settings),
            ),
            patch(
                "medasist.api.health.httpx.get",
                side_effect=httpx.ConnectError("LM Studio fora do ar"),
            ),
        ):
            response = c.get("/health")

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "degraded"
        assert body["lm_studio"]["status"] == "unavailable"
        assert body["chromadb"]["status"] == "ok"

    def test_CA04_chromadb_heartbeat_failure_degrades_without_traceback(
        self,
    ) -> None:
        """CA-04: heartbeat falha → degraded, sem traceback vazado."""
        settings = _settings()
        with (
            _client(settings) as c,
            patch(
                "medasist.api.health.get_client",
                return_value=_broken_chroma(),
            ),
            patch("medasist.api.health.httpx.get", return_value=_ok_lm()),
        ):
            response = c.get("/health")

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "degraded"
        assert body["chromadb"]["status"] == "unavailable"
        assert body["lm_studio"]["status"] == "ok"
        assert "Traceback (most recent call last)" not in response.text
        assert "conexão recusada no socket" in body["chromadb"]["details"]

    def test_CA05_lm_studio_timeout_marks_unavailable_with_details(self) -> None:
        """CA-05: timeout do LM Studio → unavailable com detalhe de timeout."""
        settings = _settings()
        with (
            _client(settings) as c,
            patch(
                "medasist.api.health.get_client",
                return_value=_healthy_chroma(settings),
            ),
            patch(
                "medasist.api.health.httpx.get",
                side_effect=httpx.TimeoutException("took too long"),
            ),
        ):
            response = c.get("/health")

        assert response.status_code == 200
        body = response.json()
        assert body["lm_studio"]["status"] == "unavailable"
        assert "timeout" in body["lm_studio"]["details"].lower()
        assert body["chromadb"]["status"] == "ok"

    def test_CA06_healthcheck_timeout_is_honored(self) -> None:
        """CA-06: timeout configurado é repassado ao probe do LM Studio."""
        settings = _settings(healthcheck_timeout=_FAST_TIMEOUT)
        captured: dict[str, object] = {}

        def _timed_out(*args: object, **kwargs: object) -> None:
            captured["timeout"] = kwargs["timeout"]
            raise httpx.TimeoutException("probe lento")

        with (
            _client(settings) as c,
            patch(
                "medasist.api.health.get_client",
                return_value=_healthy_chroma(settings),
            ),
            patch("medasist.api.health.httpx.get", side_effect=_timed_out),
        ):
            started = time.perf_counter()
            response = c.get("/health")
            elapsed = time.perf_counter() - started

        assert response.status_code == 200
        assert captured["timeout"] == _FAST_TIMEOUT
        assert elapsed < 2.0, f"endpoint levou {elapsed:.2f}s além do tolerável"
        body = response.json()
        assert body["lm_studio"]["status"] == "unavailable"

    def test_CA07_top_level_status_only_ok_or_degraded(self) -> None:
        """CA-07: status top-level restrito a ok/degraded (UI compatível)."""
        settings = _settings()
        with (
            _client(settings) as c,
            patch(
                "medasist.api.health.get_client",
                return_value=_healthy_chroma(settings),
            ),
            patch("medasist.api.health.httpx.get", return_value=_ok_lm()),
        ):
            ok_response = c.get("/health")
        with (
            _client(settings) as c,
            patch(
                "medasist.api.health.get_client",
                return_value=_broken_chroma(),
            ),
            patch(
                "medasist.api.health.httpx.get",
                side_effect=httpx.ConnectError("fora do ar"),
            ),
        ):
            degraded_response = c.get("/health")

        for response in (ok_response, degraded_response):
            assert response.status_code == 200
            assert response.json()["status"] in {"ok", "degraded"}
        assert ok_response.json()["status"] == "ok"
        assert degraded_response.json()["status"] == "degraded"

    def test_CA07_ui_treats_degraded_http_200_as_api_up(self) -> None:
        """CA-07 UI-compat: /health degraded (HTTP 200) não derruba a UI."""
        settings = _settings()
        with (
            _client(settings) as c,
            patch(
                "medasist.api.health.get_client",
                return_value=_broken_chroma(),
            ),
            patch(
                "medasist.api.health.httpx.get",
                side_effect=httpx.ConnectError("fora do ar"),
            ),
        ):
            response = c.get("/health")

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "degraded"

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = body
        mock_instance = MagicMock()
        mock_instance.get.return_value = mock_response
        mock_cls = MagicMock()
        mock_cls.return_value.__enter__.return_value = mock_instance
        mock_cls.return_value.__exit__.return_value = False
        with patch("medasist.ui.client.httpx.Client", mock_cls):
            assert get_health("http://ui-test") == body
            assert check_health("http://ui-test") is True
