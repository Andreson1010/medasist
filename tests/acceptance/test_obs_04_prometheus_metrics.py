"""Acceptance tests for OBS-04 (métricas Prometheus + monitoramento visual).

Verifica a integração do monitoramento pela camada HTTP (TestClient): a app é
construída via ``create_app(settings)`` com ``MONITORING_ENABLED=true``,
cobrindo exposição de ``/metrics``, middleware de métricas HTTP e registro das
métricas de negócio (consulta RAG e probes de saúde). O pipeline Chroma/LLM é
substituído por uma chain mockada (como em ``tests/api/conftest.py``).
Nenhum arquivo de ``src/`` é modificado. Dados sintéticos (Zolatril/Alphazol)
— sem dado real de paciente.

Cobertura por critério de aceitação:
- CA-01: ``GET /metrics`` retorna 200 com formato Prometheus e as métricas
  ``medasist_*`` presentes (HTTP, queries, dependências, processo).
- CA-02: ``POST /query`` incrementa ``medasist_queries_total`` por perfil e
  cold start, e ``medasist_citations_total``.
- CA-03: ``GET /health`` (deps saudáveis) preenche
  ``medasist_dependency_up{chromadb,lm_studio}=1`` e a latência em ms.
- CA-04: ``GET /health`` com LM Studio fora → ``medasist_dependency_up{lm_studio}=0``.
- CA-05: o middleware registra a consulta ``POST /query`` como requisição HTTP
  (status 200) no contador de requests.
- CA-06: ``monitoring_enabled=False`` (padrão) → ``GET /metrics`` responde 404
  (exposição é opt-in; API permanece byte-identical).
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from medasist.api.schemas import DependencyHealth, DependencyStatus
from medasist.config import Settings
from medasist.generation.chain import GenerationResult
from medasist.generation.citations import CitationItem
from medasist.monitoring.metrics import get_sample_value
from medasist.profiles.schemas import UserProfile

logger = logging.getLogger(__name__)

_ADMIN_KEY = "test-admin-key-0123456789"

_QUESTION = "Qual a dose recomendada do Zolatril?"
_PERFIL = UserProfile.MEDICO


def _make_result(
    answer: str = "A dose recomendada do Zolatril e 500 mg [1].",
    *,
    is_cold_start: bool = False,
) -> GenerationResult:
    """Constrói GenerationResult sintético coerente com o contrato da API.

    Parameters
    ----------
    answer : str
        Texto da resposta.
    is_cold_start : bool
        Se ``True`` devolve mensagem fixa sem citações.

    Returns
    -------
    GenerationResult
        Resultado sintético para a chain mockada.
    """
    citations = (
        []
        if is_cold_start
        else [
            CitationItem(
                index=1,
                source="bula_zolatril.pdf",
                section="Posologia",
                page="3",
            )
        ]
    )
    return GenerationResult(
        answer=answer,
        citations=citations,
        profile=_PERFIL,
        disclaimer=(
            "Este sistema é um auxiliar informativo e não substitui "
            "avaliação médica presencial."
        ),
        is_cold_start=is_cold_start,
    )


def _fixed_chain(result: GenerationResult) -> MagicMock:
    """Retorna chain mock que sempre devolve o mesmo resultado.

    Parameters
    ----------
    result : GenerationResult
        Resultado fixo a retornar em toda chamada.

    Returns
    -------
    MagicMock
        Chain cujo retorno é fixado em ``result``.
    """
    chain = MagicMock()
    chain.return_value = result
    return chain


def _settings(
    log_dir: Path,
    monitoring_enabled: bool,
    monitoring_metrics_path: str = "/metrics",
) -> Settings:
    """Settings de teste com admin key forte e monitoramento controlado.

    Parameters
    ----------
    log_dir : Path
        Diretório de logs isolado (``tmp_path``).
    monitoring_enabled : bool
        Valor da flag ``monitoring_enabled``.
    monitoring_metrics_path : str
        Rota de exposição das métricas.

    Returns
    -------
    Settings
        Configurações isoladas para o teste.
    """
    return Settings(
        admin_api_key=_ADMIN_KEY,
        log_dir=log_dir,
        monitoring_enabled=monitoring_enabled,
        monitoring_metrics_path=monitoring_metrics_path,
    )


def _healthy_dependency() -> DependencyHealth:
    """DependencyHealth padrão para probes mockados no /health."""
    return DependencyHealth(
        status=DependencyStatus.OK,
        details="saudável",
        latency_ms=1,
    )


@contextmanager
def _client(settings: Settings, chain: MagicMock) -> Iterator[TestClient]:
    """TestClient com lifespan mockado e monitoramento sob controle.

    Patching espelha ``tests/api/conftest.py``; a app é construída via
    ``create_app(settings)`` para que o monitoramento (CORS/middleware/rota)
    seja montado com as settings do teste, sem depender do estado de import.

    Parameters
    ----------
    settings : Settings
        Configurações com ``monitoring_*`` sob controle do teste.
    chain : MagicMock
        Chain a injetar para todos os perfis.

    Yields
    ------
    TestClient
        Cliente de teste com a app já inicializada.
    """
    chains = dict.fromkeys(UserProfile, chain)
    with (
        patch("medasist.api.main.get_settings", return_value=settings),
        patch("medasist.api.main.get_client"),
        patch("medasist.api.main.build_embeddings"),
        patch("medasist.api.main.get_all_vectorstores", return_value={}),
        patch(
            "medasist.api.main.build_chain",
            side_effect=lambda stores, profile, settings: chains[profile],
        ),
        patch(
            "medasist.api.health.check_chromadb",
            return_value=_healthy_dependency(),
        ),
        patch(
            "medasist.api.health.check_lm_studio",
            return_value=_healthy_dependency(),
        ),
    ):
        from medasist.api.main import create_app

        with TestClient(create_app(settings)) as c:
            yield c


def _payload(question: str = _QUESTION, profile: str = "medico") -> dict:
    """Monta payload JSON para ``POST /query``.

    Parameters
    ----------
    question : str
        Pergunta sintética.
    profile : str
        Perfil (valor string do enum).

    Returns
    -------
    dict
        Payload para ``POST /query``.
    """
    return {"question": question, "profile": profile}


class TestObs04MetricsEndpoint:
    def test_CA01_metrics_endpoint_returns_prometheus_text(
        self, tmp_path: Path
    ) -> None:
        """CA-01: ``/metrics`` 200 com formato e métricas ``medasist_*``."""
        settings = _settings(tmp_path / "logs", monitoring_enabled=True)
        chain = _fixed_chain(_make_result())

        with _client(settings, chain) as c:
            response = c.get("/metrics")

        assert response.status_code == 200
        assert "text/plain" in response.headers["content-type"]
        body = response.text
        assert "medasist_http_requests_total" in body
        assert "medasist_queries_total" in body
        assert "medasist_dependency_up" in body
        assert "python_info" in body

    def test_CA06_metrics_disabled_returns_404(self, tmp_path: Path) -> None:
        """CA-06: flag off (padrão) → ``/metrics`` responde 404."""
        settings = _settings(tmp_path / "logs", monitoring_enabled=False)
        chain = _fixed_chain(_make_result())

        with _client(settings, chain) as c:
            response = c.get("/metrics")

        assert response.status_code == 404


class TestObs04QueryMetrics:
    def test_CA02_query_increments_queries_and_citations(self, tmp_path: Path) -> None:
        """CA-02: ``POST /query`` incrementa queries_total e citations_total."""
        settings = _settings(tmp_path / "logs", monitoring_enabled=True)
        chain = _fixed_chain(_make_result())

        labels_queries = {"profile": _PERFIL.value, "cold_start": "false"}
        labels_citations = {"profile": _PERFIL.value}
        before_queries = get_sample_value("medasist_queries_total", labels_queries)
        before_citations = get_sample_value(
            "medasist_citations_total", labels_citations
        )

        with _client(settings, chain) as c:
            response = c.post("/query", json=_payload())

        assert response.status_code == 200
        assert (
            get_sample_value("medasist_queries_total", labels_queries)
            == before_queries + 1
        )
        assert (
            get_sample_value("medasist_citations_total", labels_citations)
            == before_citations + 1
        )

    def test_CA05_middleware_records_query_http_request(self, tmp_path: Path) -> None:
        """CA-05: middleware registra ``POST /query`` como request HTTP 200."""
        settings = _settings(tmp_path / "logs", monitoring_enabled=True)
        chain = _fixed_chain(_make_result())

        labels = {
            "method": "POST",
            "path": "/query",
            "status": "200",
        }
        before = get_sample_value("medasist_http_requests_total", labels)

        with _client(settings, chain) as c:
            response = c.post("/query", json=_payload())

        assert response.status_code == 200
        assert get_sample_value("medasist_http_requests_total", labels) == before + 1


class TestObs04DependencyMetrics:
    def test_CA03_healthy_health_sets_dependency_up(self, tmp_path: Path) -> None:
        """CA-03: deps saudáveis → ``dependency_up`` = 1 para ambas."""
        settings = _settings(tmp_path / "logs", monitoring_enabled=True)
        chain = _fixed_chain(_make_result())

        with _client(settings, chain) as c:
            response = c.get("/health")

        assert response.status_code == 200
        assert response.json()["status"] == "ok"
        assert (
            get_sample_value("medasist_dependency_up", {"dependency": "chromadb"}) == 1
        )
        assert (
            get_sample_value("medasist_dependency_up", {"dependency": "lm_studio"}) == 1
        )
        assert (
            get_sample_value(
                "medasist_dependency_latency_ms", {"dependency": "chromadb"}
            )
            >= 0
        )

    def test_CA04_lm_studio_down_sets_dependency_up_zero(self, tmp_path: Path) -> None:
        """CA-04: LM Studio fora → ``dependency_up{lm_studio}`` = 0."""
        settings = _settings(tmp_path / "logs", monitoring_enabled=True)
        chain = _fixed_chain(_make_result())

        chains = dict.fromkeys(UserProfile, chain)
        with (
            patch("medasist.api.main.get_settings", return_value=settings),
            patch("medasist.api.main.get_client"),
            patch("medasist.api.main.build_embeddings"),
            patch("medasist.api.main.get_all_vectorstores", return_value={}),
            patch(
                "medasist.api.main.build_chain",
                side_effect=lambda stores, profile, settings: chains[profile],
            ),
            patch(
                "medasist.api.health.check_chromadb",
                return_value=_healthy_dependency(),
            ),
            patch(
                "medasist.api.health.check_lm_studio",
                return_value=DependencyHealth(
                    status=DependencyStatus.UNAVAILABLE,
                    details="LM Studio fora do ar",
                    latency_ms=2,
                ),
            ),
        ):
            from medasist.api.main import create_app

            with TestClient(create_app(settings)) as c:
                response = c.get("/health")

        assert response.status_code == 200
        assert response.json()["status"] == "degraded"
        assert (
            get_sample_value("medasist_dependency_up", {"dependency": "lm_studio"}) == 0
        )
        assert (
            get_sample_value("medasist_dependency_up", {"dependency": "chromadb"}) == 1
        )
