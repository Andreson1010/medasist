from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from medasist.monitoring import metrics as m

_HTTP = "medasist_http_requests_total"
_DURATION = "medasist_http_request_duration_seconds"
_QUERIES = "medasist_queries_total"
_QUERY_DURATION = "medasist_query_duration_seconds"
_CITATIONS = "medasist_citations_total"


def _sample(name: str, labels: dict[str, str] | None = None) -> float:
    """Valor atual de uma amostra de métrica (0 se ainda não observada).

    Parameters
    ----------
    name : str
        Nome da métrica.
    labels : dict[str, str] | None
        Labels exatos da amostra.

    Returns
    -------
    float
        Valor da amostra via ``metrics.get_sample_value``.
    """
    return m.get_sample_value(name, labels)


def _app(metrics_path: str = "/metrics") -> FastAPI:
    """App FastAPI mínimo com o monitoramento instalado e uma rota ``/ping``.

    Parameters
    ----------
    metrics_path : str
        Rota de exposição das métricas.

    Returns
    -------
    FastAPI
        App instrumentado com ``install_monitoring``.
    """

    app = FastAPI()

    @app.get("/ping")
    def ping() -> dict[str, bool]:
        return {"ok": True}

    m.install_monitoring(app, True, metrics_path)
    return app


class TestRecordQuery:
    def test_increments_counters_and_histogram(self) -> None:
        labels_queries = {"profile": "medico", "cold_start": "false"}
        labels_duration = {"profile": "medico"}
        labels_citations = {"profile": "medico"}

        before_queries = _sample(_QUERIES, labels_queries)
        before_count = _sample(f"{_QUERY_DURATION}_count", labels_duration)
        before_sum = _sample(f"{_QUERY_DURATION}_sum", labels_duration)
        before_citations = _sample(_CITATIONS, labels_citations)

        m.record_query("medico", cold_start=False, citations=3, latency_ms=500)

        assert _sample(_QUERIES, labels_queries) == before_queries + 1
        assert _sample(f"{_QUERY_DURATION}_count", labels_duration) == before_count + 1
        assert _sample(f"{_QUERY_DURATION}_sum", labels_duration) == before_sum + 0.5
        assert _sample(_CITATIONS, labels_citations) == before_citations + 3

    def test_cold_start_uses_true_label(self) -> None:
        labels_true = {"profile": "enfermeiro", "cold_start": "true"}
        labels_false = {"profile": "enfermeiro", "cold_start": "false"}

        before_true = _sample(_QUERIES, labels_true)
        before_false = _sample(_QUERIES, labels_false)

        m.record_query("enfermeiro", cold_start=True, citations=0, latency_ms=10)

        assert _sample(_QUERIES, labels_true) == before_true + 1
        assert _sample(_QUERIES, labels_false) == before_false

    def test_negative_latency_is_clamped_to_zero(self) -> None:
        labels_duration = {"profile": "paciente"}

        before_count = _sample(f"{_QUERY_DURATION}_count", labels_duration)
        before_sum = _sample(f"{_QUERY_DURATION}_sum", labels_duration)

        m.record_query("paciente", cold_start=False, citations=0, latency_ms=-1)

        assert _sample(f"{_QUERY_DURATION}_count", labels_duration) == before_count + 1
        assert _sample(f"{_QUERY_DURATION}_sum", labels_duration) == before_sum


class TestRecordDependencyHealth:
    def test_ok_sets_gauge_to_one(self) -> None:
        m.record_dependency_health("chromadb", "ok", 5)
        assert _sample("medasist_dependency_up", {"dependency": "chromadb"}) == 1
        assert (
            _sample("medasist_dependency_latency_ms", {"dependency": "chromadb"}) == 5
        )

    def test_unavailable_sets_gauge_to_zero(self) -> None:
        m.record_dependency_health("lm_studio", "unavailable", 9)
        assert _sample("medasist_dependency_up", {"dependency": "lm_studio"}) == 0
        assert (
            _sample("medasist_dependency_latency_ms", {"dependency": "lm_studio"}) == 9
        )

    def test_degraded_sets_gauge_to_zero(self) -> None:
        m.record_dependency_health("chromadb", "degraded", 3)
        assert _sample("medasist_dependency_up", {"dependency": "chromadb"}) == 0


class TestInstallMonitoring:
    def test_disabled_is_noop(self) -> None:
        app = FastAPI()
        m.install_monitoring(app, enabled=False, metrics_path="/metrics")

        with TestClient(app) as c:
            assert c.get("/metrics").status_code == 404

    def test_metrics_endpoint_returns_prometheus_text(self) -> None:
        with TestClient(_app()) as c:
            response = c.get("/metrics")

        assert response.status_code == 200
        assert "text/plain" in response.headers["content-type"]
        body = response.text
        assert _QUERIES in body
        assert _HTTP in body
        assert "python_info" in body

    def test_custom_metrics_path(self) -> None:
        with TestClient(_app(metrics_path="/prometheus")) as c:
            assert c.get("/prometheus").status_code == 200
            assert c.get("/metrics").status_code == 404

    def test_metrics_route_does_not_count_itself(self) -> None:
        labels = {"method": "GET", "path": "/metrics", "status": "200"}

        before = _sample(_HTTP, labels)
        with TestClient(_app()) as c:
            c.get("/metrics")
            c.get("/metrics")

        assert _sample(_HTTP, labels) == before


class TestHttpMiddleware:
    def test_records_request_and_duration(self) -> None:
        labels = {"method": "GET", "path": "/ping", "status": "200"}
        labels_duration = {"method": "GET", "path": "/ping"}

        before_count = _sample(_HTTP, labels)
        before_duration_count = _sample(f"{_DURATION}_count", labels_duration)
        before_duration_sum = _sample(f"{_DURATION}_sum", labels_duration)

        with TestClient(_app()) as c:
            c.get("/ping")

        assert _sample(_HTTP, labels) == before_count + 1
        assert (
            _sample(f"{_DURATION}_count", labels_duration) == before_duration_count + 1
        )
        assert _sample(f"{_DURATION}_sum", labels_duration) >= before_duration_sum

    def test_records_non_2xx_status(self) -> None:
        labels = {"method": "GET", "path": "/nao-existe", "status": "404"}

        before = _sample(_HTTP, labels)
        with TestClient(_app()) as c:
            c.get("/nao-existe")

        assert _sample(_HTTP, labels) == before + 1
