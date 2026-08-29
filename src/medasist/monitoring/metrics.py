from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    GC_COLLECTOR,
    PLATFORM_COLLECTOR,
    PROCESS_COLLECTOR,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

logger = logging.getLogger(__name__)

_registry = CollectorRegistry()
_registry.register(PROCESS_COLLECTOR)
_registry.register(PLATFORM_COLLECTOR)
_registry.register(GC_COLLECTOR)

REQUESTS_TOTAL = Counter(
    "medasist_http_requests_total",
    "Total de requisições HTTP recebidas pela API.",
    labelnames=("method", "path", "status"),
    registry=_registry,
)

REQUEST_DURATION_SECONDS = Histogram(
    "medasist_http_request_duration_seconds",
    "Latência das requisições HTTP em segundos.",
    labelnames=("method", "path"),
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0),
    registry=_registry,
)

QUERIES_TOTAL = Counter(
    "medasist_queries_total",
    "Total de consultas RAG executadas, por perfil e cold start.",
    labelnames=("profile", "cold_start"),
    registry=_registry,
)

QUERY_DURATION_SECONDS = Histogram(
    "medasist_query_duration_seconds",
    "Latência das consultas RAG em segundos, por perfil.",
    labelnames=("profile",),
    buckets=(0.1, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0),
    registry=_registry,
)

CITATIONS_TOTAL = Counter(
    "medasist_citations_total",
    "Total de citações emitidas nas respostas, por perfil.",
    labelnames=("profile",),
    registry=_registry,
)

DEPENDENCY_UP = Gauge(
    "medasist_dependency_up",
    "1 se a dependência está ok no último probe de saúde, 0 caso contrário.",
    labelnames=("dependency",),
    registry=_registry,
)

DEPENDENCY_LATENCY_MS = Gauge(
    "medasist_dependency_latency_ms",
    "Latência do último probe de saúde em ms, por dependência.",
    labelnames=("dependency",),
    registry=_registry,
)

_metrics_path = "/metrics"


def get_sample_value(name: str, labels: dict[str, str] | None = None) -> float:
    """Lê o valor atual de uma amostra de métrica do registro interno.

    Uso em testes/verificações manuais: retorna 0.0 quando a amostra ainda
    não existe (evita ``None`` para valores não observados).

    Parameters
    ----------
    name : str
        Nome da métrica (ex: ``medasist_queries_total``).
    labels : dict[str, str] | None
        Labels exatos da amostra, quando a métrica tem labels.

    Returns
    -------
    float
        Valor da amostra, ou 0.0 se a amostra não existe.
    """
    return _registry.get_sample_value(name, labels) or 0.0


def record_query(
    profile: str, cold_start: bool, citations: int, latency_ms: int
) -> None:
    """Registra as métricas de negócio de uma consulta RAG.

    Incrementa o contador de consultas (por perfil e cold start), observa a
    latência no histograma e soma as citações emitidas.

    Parameters
    ----------
    profile : str
        Valor do enum ``UserProfile`` (ex: ``medico``).
    cold_start : bool
        Se a consulta foi resolvida por cold start (sem retrieval).
    citations : int
        Quantidade de citações retornadas na resposta.
    latency_ms : int
        Latência total da consulta em milissegundos.
    """
    cs = "true" if cold_start else "false"
    QUERIES_TOTAL.labels(profile=profile, cold_start=cs).inc()
    QUERY_DURATION_SECONDS.labels(profile=profile).observe(max(latency_ms, 0) / 1000.0)
    CITATIONS_TOTAL.labels(profile=profile).inc(citations)


def record_dependency_health(dependency: str, status: str, latency_ms: int) -> None:
    """Registra o resultado de um probe de saúde de dependência.

    Parameters
    ----------
    dependency : str
        Identificador da dependência (ex: ``chromadb``, ``lm_studio``).
    status : str
        Valor de ``DependencyStatus`` (``ok``/``degraded``/``unavailable``).
    latency_ms : int
        Latência do probe em milissegundos.
    """
    up = 1 if status == "ok" else 0
    DEPENDENCY_UP.labels(dependency=dependency).set(up)
    DEPENDENCY_LATENCY_MS.labels(dependency=dependency).set(latency_ms)


def _metrics() -> Response:
    """Expoe as métricas no formato texto do Prometheus.

    Returns
    -------
    Response
        Resposta HTTP com o corpo no formato de exposição do Prometheus.
    """
    return Response(content=generate_latest(_registry), media_type=CONTENT_TYPE_LATEST)


async def _http_metrics_middleware(
    request: Request, call_next: Callable[..., Any]
) -> Response:
    """Mede requisições HTTP, exceto a própria rota de métricas.

    A rota de métricas é excluída para não inflar o contador com o
    self-scraping periódico do Prometheus.

    Parameters
    ----------
    request : Request
        Requisição recebida.
    call_next : Callable
        Próximo handler da cadeia ASGI.

    Returns
    -------
    Response
        Resposta original, apenas medindo latência e registrando o status.
    """
    start = time.perf_counter()
    response = await call_next(request)
    latency_seconds = time.perf_counter() - start
    if request.url.path != _metrics_path:
        REQUESTS_TOTAL.labels(
            method=request.method,
            path=request.url.path,
            status=str(response.status_code),
        ).inc()
        REQUEST_DURATION_SECONDS.labels(
            method=request.method, path=request.url.path
        ).observe(latency_seconds)
    return response


def install_monitoring(app: FastAPI, enabled: bool, metrics_path: str) -> None:
    """Instala o middleware de métricas HTTP e a rota de exposição.

    Opt-in via ``monitoring_enabled``: com a flag desligada a chamada é um
    no-op (sem middleware e sem rota). As métricas de negócio
    (``record_query``/``record_dependency_health``) são sempre coletadas —
    custo por chamada desprezível — e apenas a exposição é condicional.

    Parameters
    ----------
    app : FastAPI
        Instância da aplicação a instrumentar.
    enabled : bool
        Se a exposição está habilitada (``monitoring_enabled``).
    metrics_path : str
        Rota de exposição das métricas (ex: ``/metrics``).
    """
    global _metrics_path
    if not enabled:
        return
    _metrics_path = metrics_path
    app.middleware("http")(_http_metrics_middleware)
    app.add_api_route(metrics_path, _metrics, methods=["GET"], include_in_schema=False)
