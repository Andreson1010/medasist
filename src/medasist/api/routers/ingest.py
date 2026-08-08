from __future__ import annotations

import logging
import secrets
import tempfile
from pathlib import Path
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    File,
    Header,
    HTTPException,
    Request,
    UploadFile,
    status,
)

from medasist.api.deps import limiter
from medasist.api.schemas import IngestResponse
from medasist.config import get_settings
from medasist.ingestion.pipeline import ingest_document
from medasist.ingestion.schemas import DocType
from medasist.vectorstore.store import get_client

logger = logging.getLogger(__name__)

router = APIRouter()

STREAM_CHUNK_SIZE: int = 1024 * 1024


async def _stream_upload_with_limit(
    file: UploadFile, path: Path, max_bytes: int
) -> None:
    """Escreve o upload em chunks, abortando com 413 quando excede o limite.

    Parameters
    ----------
    file : UploadFile
        Arquivo enviado pelo cliente.
    path : Path
        Caminho do arquivo temporário de destino.
    max_bytes : int
        Tamanho máximo aceito em bytes (inclusivo).

    Raises
    ------
    HTTPException
        413 se o total de bytes lidos ultrapassar ``max_bytes``.
    """
    total = 0
    with path.open("wb") as tmp:
        while chunk := await file.read(STREAM_CHUNK_SIZE):
            total += len(chunk)
            if total > max_bytes:
                logger.warning(
                    "ingest: upload excede o limite de %s MB.",
                    max_bytes // (1024 * 1024),
                )
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=(
                        f"Arquivo excede o limite máximo de "
                        f"{max_bytes // (1024 * 1024)} MB."
                    ),
                )
            tmp.write(chunk)


def verify_admin_key(x_admin_key: Annotated[str, Header()]) -> None:
    """Valida o header X-Admin-Key contra a chave configurada.

    Usa ``secrets.compare_digest`` para comparação timing-safe.

    Parameters
    ----------
    x_admin_key : str
        Valor do header ``X-Admin-Key`` enviado pelo cliente.

    Raises
    ------
    HTTPException
        401 se a chave for inválida.
    """
    stripped = x_admin_key.strip()
    if not stripped:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Chave de admin inválida."
        )

    settings = get_settings()
    expected = settings.admin_api_key.get_secret_value()
    if not secrets.compare_digest(stripped, expected):
        logger.warning("ingest: tentativa com chave de admin inválida.")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Chave de admin inválida."
        )


@limiter.limit("5/minute")
@router.post(
    "/ingest",
    response_model=IngestResponse,
    dependencies=[Depends(verify_admin_key)],
    summary="Ingestão de documento PDF",
    description="Requer header X-Admin-Key. Aceita PDF e doc_type como query param.",
)
async def ingest(
    request: Request,
    file: Annotated[UploadFile, File()],
    doc_type: DocType,
) -> IngestResponse:
    """Ingere um documento PDF no vectorstore.

    Parameters
    ----------
    request : Request
        Objeto de request do FastAPI (exigido pelo slowapi).
    file : UploadFile
        Arquivo PDF enviado pelo cliente.
    doc_type : DocType
        Tipo do documento (query param).

    Returns
    -------
    IngestResponse
        Resultado da ingestão com sha256, chunks_indexed e flag skipped.
    """
    settings = get_settings()
    max_bytes = settings.max_upload_mb * 1024 * 1024
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    try:
        await _stream_upload_with_limit(file, tmp_path, max_bytes)

        result = ingest_document(
            path=tmp_path,
            doc_type=doc_type,
            chroma_client=get_client(settings),
            settings=settings,
        )

        logger.info(
            "ingest: arquivo='%s' doc_type='%s' chunks=%d skipped=%s",
            file.filename,
            doc_type.value,
            result.chunks_indexed,
            result.skipped,
        )

        if result.error:
            logger.error(
                "ingest: erro no pipeline para '%s': %s", file.filename, result.error
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Falha ao processar o documento.",
            )

        return IngestResponse(
            filename=file.filename or "",
            doc_type=doc_type,
            sha256=result.sha256,
            chunks_indexed=result.chunks_indexed,
            skipped=result.skipped,
        )

    finally:
        tmp_path.unlink(missing_ok=True)
