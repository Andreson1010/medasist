from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from medasist.api.deps import limiter
from medasist.api.routers.ingest import router as ingest_router
from medasist.api.routers.query import router as query_router
from medasist.config import get_settings
from medasist.generation.chain import build_chain
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

    No startup, aquece todas as chains RAG (uma por UserProfile) e
    armazena em ``app.state.chains``. No shutdown, não há cleanup necessário
    pois ChromaDB usa persistência em disco.

    Parameters
    ----------
    app : FastAPI
        Instância da aplicação.
    """
    settings = get_settings()
    client = get_client(settings)
    embeddings = build_embeddings(settings)
    stores = get_all_vectorstores(client, embeddings, settings)

    app.state.chains = {
        profile: build_chain(stores, profile, settings) for profile in UserProfile
    }

    logger.info("Lifespan: %d chains aquecidas.", len(app.state.chains))
    yield


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

app.include_router(query_router)
app.include_router(ingest_router)


@app.get("/health", summary="Health check")
def health() -> dict[str, str]:
    """Verifica se a API está em execução.

    Returns
    -------
    dict[str, str]
        ``{"status": "ok"}``
    """
    return {"status": "ok"}
