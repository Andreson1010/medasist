from __future__ import annotations

import logging
import secrets
import tempfile
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Header, HTTPException, Request, UploadFile, status

from medasist.api.deps import limiter
from medasist.api.schemas import IngestResponse
from medasist.config import get_settings
from medasist.ingestion.pipeline import ingest_document
from medasist.ingestion.schemas import DocType
from medasist.vectorstore.store import get_client

logger = logging.getLogger(__name__)

router = APIRouter()


def verify_admin_key(x_admin_key: str = Header(...)) -> None:
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
    settings = get_settings()
    expected = settings.admin_api_key.get_secret_value()
    if not secrets.compare_digest(x_admin_key, expected):
        logger.warning("ingest: tentativa com chave de admin inválida.")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Chave de admin inválida.")


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
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    try:
        content = await file.read()
        tmp_path.write_bytes(content)

        result = ingest_document(
            path=tmp_path,
            doc_type=doc_type,
            chroma_client=get_client(),
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
