from __future__ import annotations

import logging
import time

import httpx

from medasist.api.schemas import DependencyHealth, DependencyStatus, HealthResponse
from medasist.config import Settings
from medasist.vectorstore.store import get_client

logger = logging.getLogger(__name__)

_CHROMADB_OK_DETAILS = "ChromaDB respondendo"
_LM_STUDIO_OK_DETAILS = "LM Studio respondendo"


def _error_details(exc: BaseException, limit: int = 200) -> str:
    """Retorna mensagem de erro legível, com fallback para o nome da classe.

    Evita detalhes vazios (``str(exc) == ""``) e respostas de health check
    excessivamente longas.

    Parameters
    ----------
    exc : BaseException
        Exceção capturada no probe.
    limit : int
        Tamanho máximo da string retornada.

    Returns
    -------
    str
        ``str(exc)`` truncado a ``limit`` caracteres; quando vazio, o nome da
        classe da exceção.
    """
    message = str(exc) or exc.__class__.__name__
    return message[:limit]


def _result(status: DependencyStatus, details: str, start: float) -> DependencyHealth:
    """Constrói DependencyHealth medindo a latência desde ``start``.

    Parameters
    ----------
    status : DependencyStatus
        Estado da dependência.
    details : str
        Detalhe do resultado do probe.
    start : float
        Timestamp de início do probe (``time.perf_counter``).

    Returns
    -------
    DependencyHealth
        Resultado do probe com latência em milissegundos.
    """
    latency_ms = int((time.perf_counter() - start) * 1000)
    return DependencyHealth(status=status, details=details, latency_ms=latency_ms)


def _expected_collections(settings: Settings) -> set[str]:
    """Retorna os nomes das coleções ChromaDB esperadas.

    Parameters
    ----------
    settings : Settings
        Configurações com os nomes de coleções.

    Returns
    -------
    set[str]
        Conjunto com os nomes das 4 coleções por DocType.
    """
    return {
        settings.collection_bulas,
        settings.collection_diretrizes,
        settings.collection_protocolos,
        settings.collection_manuais,
    }


def check_chromadb(settings: Settings) -> DependencyHealth:
    """Verifica a saúde do ChromaDB via heartbeat e coleções.

    O probe usa o cliente persistente local (in-process): ``heartbeat`` e
    ``list_collections`` não expõem timeout próprio, portanto este probe não
    possui limite de tempo. ``healthcheck_timeout`` aplica-se apenas ao probe
    do LM Studio — tradeoff aceito para desenvolvimento local.

    Parameters
    ----------
    settings : Settings
        Configurações com ``chroma_dir`` e nomes de coleções.

    Returns
    -------
    DependencyHealth
        ``ok`` se o heartbeat responde e as 4 coleções existem;
        ``degraded`` se o ChromaDB responde mas faltam coleções;
        ``unavailable`` se o cliente falha no heartbeat ou na listagem.
    """
    start = time.perf_counter()
    try:
        client = get_client(settings)
        client.heartbeat()
    except Exception as exc:
        logger.error("ChromaDB inacessível: %s", exc)
        return _result(DependencyStatus.UNAVAILABLE, _error_details(exc), start)

    try:
        actual = {collection.name for collection in client.list_collections()}
    except Exception as exc:
        logger.error("ChromaDB falhou ao listar coleções: %s", exc)
        return _result(DependencyStatus.UNAVAILABLE, _error_details(exc), start)

    missing = _expected_collections(settings) - actual
    if missing:
        details = "coleções ausentes: " + ", ".join(sorted(missing))
        return _result(DependencyStatus.DEGRADED, details, start)
    return _result(DependencyStatus.OK, _CHROMADB_OK_DETAILS, start)


def check_lm_studio(settings: Settings, timeout: float) -> DependencyHealth:
    """Verifica a saúde do LM Studio via ``GET {base_url}/models``.

    Parameters
    ----------
    settings : Settings
        Configurações com ``lm_studio_base_url``.
    timeout : float
        Tempo limite (segundos) da requisição.

    Returns
    -------
    DependencyHealth
        ``ok`` se a resposta for 2xx; ``unavailable`` em timeout, erro de
        conexão ou status não-2xx.
    """
    start = time.perf_counter()
    url = f"{settings.lm_studio_base_url}/models"
    try:
        response = httpx.get(url, timeout=timeout)
    except httpx.TimeoutException as exc:
        logger.error("LM Studio timeout após %.1fs: %s", timeout, exc)
        return _result(
            DependencyStatus.UNAVAILABLE, f"timeout após {timeout:g}s", start
        )
    except httpx.HTTPError as exc:
        logger.error("LM Studio inacessível: %s", exc)
        return _result(DependencyStatus.UNAVAILABLE, _error_details(exc), start)

    if response.status_code >= 300:
        details = f"HTTP {response.status_code}"
        logger.error("LM Studio respondeu %s", details)
        return _result(DependencyStatus.UNAVAILABLE, details, start)
    return _result(DependencyStatus.OK, _LM_STUDIO_OK_DETAILS, start)


def check_dependencies(settings: Settings) -> HealthResponse:
    """Executa os probes de ChromaDB e LM Studio e monta a resposta.

    Roda os probes sequencialmente. O probe do LM Studio é limitado por
    ``settings.healthcheck_timeout``; o do ChromaDB usa o cliente persistente
    local in-process, sem tempo limite próprio (tradeoff aceito). O estado
    geral é ``ok`` apenas quando ambas as dependências estão ``ok``; caso
    contrário, ``degraded``.

    Parameters
    ----------
    settings : Settings
        Configurações centrais da aplicação.

    Returns
    -------
    HealthResponse
        Estado geral e saúde por dependência com latência em ms.
    """
    timeout = settings.healthcheck_timeout
    chromadb_health = check_chromadb(settings)
    lm_health = check_lm_studio(settings, timeout)
    overall = DependencyStatus.DEGRADED
    if (
        chromadb_health.status is DependencyStatus.OK
        and lm_health.status is DependencyStatus.OK
    ):
        overall = DependencyStatus.OK
    return HealthResponse(
        status=overall.value,
        chromadb=chromadb_health,
        lm_studio=lm_health,
    )
