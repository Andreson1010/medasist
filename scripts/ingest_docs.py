from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import chromadb

from medasist.config import get_settings
from medasist.ingestion.pipeline import build_embed_fn, ingest_directory
from medasist.ingestion.schemas import DocType

logger = logging.getLogger(__name__)

_VALID_DOC_TYPES = [dt.value for dt in DocType]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parseia argumentos da linha de comando.

    Parameters
    ----------
    argv : list[str] | None
        Lista de argumentos (None usa sys.argv).

    Returns
    -------
    argparse.Namespace
        Argumentos parseados.
    """
    parser = argparse.ArgumentParser(
        description="Ingere PDFs no ChromaDB do MedAssist.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--dir",
        required=True,
        type=Path,
        metavar="DIRETÓRIO",
        help="Diretório contendo os arquivos PDF.",
    )
    parser.add_argument(
        "--doc-type",
        required=True,
        choices=_VALID_DOC_TYPES,
        metavar="TIPO",
        help=f"Tipo do documento. Opções: {_VALID_DOC_TYPES}",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Lista os PDFs encontrados sem processar.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Ponto de entrada do script de ingestão.

    Parameters
    ----------
    argv : list[str] | None
        Argumentos CLI (None usa sys.argv).

    Returns
    -------
    int
        0 em sucesso, 1 em caso de erro.
    """
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parse_args(argv)

    pdf_dir = args.dir
    if not pdf_dir.is_dir():
        logger.error("Diretório não encontrado: %s", pdf_dir)
        return 1

    pdf_files = sorted(pdf_dir.glob("*.pdf"))
    if not pdf_files:
        logger.warning("Nenhum PDF encontrado em %s", pdf_dir)
        return 0

    if args.dry_run:
        logger.info("Dry-run: %d PDF(s) encontrado(s) em %s", len(pdf_files), pdf_dir)
        for f in pdf_files:
            logger.info("  %s", f.name)
        return 0

    settings = get_settings()
    doc_type = DocType(args.doc_type)
    chroma_client = chromadb.PersistentClient(path=str(settings.chroma_dir))
    embed_fn = build_embed_fn(settings)

    results = ingest_directory(pdf_dir, doc_type, chroma_client, settings, embed_fn)

    processed = sum(
        1 for r in results if not r.skipped and not r.error and r.chunks_indexed > 0
    )
    skipped = sum(1 for r in results if r.skipped)
    errors = [r for r in results if r.error]

    logger.info(
        "Resultado: %d processado(s), %d pulado(s), %d erro(s)",
        processed,
        skipped,
        len(errors),
    )
    for err in errors:
        logger.error("ERRO %s: %s", err.path.name, err.error)

    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
