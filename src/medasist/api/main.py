from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from medasist.api.deps import limiter
from medasist.api.health import check_dependencies
from medasist.api.routers.ingest import router as ingest_router
from medasist.api.routers.query import router as query_router
from medasist.api.schemas import HealthResponse
from medasist.config import (
    ADMIN_KEY_MIN_LENGTH,
    Settings,
    admin_key_is_weak,
    csv_list,
    get_settings,
)
from medasist.generation.chain import build_chain, build_stream_chain
from medasist.logging_setup import configure_logging
from medasist.monitoring.metrics import install_monitoring
from medasist.profiles.schemas import UserProfile
from medasist.vectorstore.store import (
    build_embeddings,
    get_all_vectorstores,
    get_client,
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Gerencia o ciclo de vida da aplicação FastAPI.

    No startup, configura o logging estruturado, aquece todas as chains RAG
    (uma por UserProfile) e armazena em ``app.state.chains``. No shutdown,
    não há cleanup necessário pois ChromaDB usa persistência em disco.

    Parameters
    ----------
    app : FastAPI
        Instância da aplicação.
    """
    settings = get_settings()
    configure_logging(settings, "api")
    if admin_key_is_weak(settings.admin_api_key.get_secret_value()):
        logger.warning(
            "ADMIN_API_KEY está usando um valor padrão/placeholder; configure "
            "uma chave forte de pelo menos %d caracteres para o endpoint /ingest.",
            ADMIN_KEY_MIN_LENGTH,
        )
    client = get_client(settings)
    embeddings = build_embeddings(settings)
    stores = get_all_vectorstores(client, embeddings, settings)

    app.state.chains = {
        profile: build_chain(stores, profile, settings) for profile in UserProfile
    }
    app.state.streaming_chains = {
        profile: build_stream_chain(stores, profile, settings)
        for profile in UserProfile
    }

    logger.info(
        "Lifespan: %d chains e %d streaming chains aquecidas.",
        len(app.state.chains),
        len(app.state.streaming_chains),
    )
    yield


def create_app(settings: Settings | None = None) -> FastAPI:
    """Constrói a aplicação FastAPI com CORS, rate limit e monitoramento.

    Fábrica explícita que recebe ``Settings`` opcional — usada para construir
    a app com settings controladas em testes (monitoramento ligado/desligado)
    sem depender do estado global de import. ``app = create_app()`` no final
    deste módulo usa as settings reais. O ``lifespan`` segue resolvendo
    ``get_settings()`` em runtime (mesmo comportamento de antes).

    Parameters
    ----------
    settings : Settings | None
        Configurações usadas na montagem (CORS e monitoramento). Se ``None``,
        usa o singleton ``get_settings()``.

    Returns
    -------
    FastAPI
        Aplicação configurada e instrumentada.
    """
    cfg = settings if settings is not None else get_settings()

    app = FastAPI(
        title="MedAssist RAG API",
        description=(
            "API de suporte à decisão médica baseada em RAG. "
            "Este sistema é um auxiliar informativo e não substitui "
            "avaliação médica presencial."
        ),
        version="0.1.0",
        lifespan=lifespan,
    )

    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=csv_list(cfg.cors_allow_origins),
        allow_methods=csv_list(cfg.cors_allow_methods),
        allow_headers=csv_list(cfg.cors_allow_headers),
        allow_credentials=cfg.cors_allow_credentials,
    )

    install_monitoring(app, cfg.monitoring_enabled, cfg.monitoring_metrics_path)

    app.include_router(query_router)
    app.include_router(ingest_router)

    @app.get("/health", summary="Health check", response_model=HealthResponse)
    def health() -> HealthResponse:
        """Verifica a saúde das dependências (ChromaDB e LM Studio).

        Returns
        -------
        HealthResponse
            Estado geral e saúde por dependência, com latência em ms.
        """
        settings = get_settings()
        return check_dependencies(settings)

    return app


app = create_app()
