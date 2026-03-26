from __future__ import annotations

import hashlib
import logging
from pathlib import Path

import fitz  # PyMuPDF
import pdfplumber

from medasist.ingestion.schemas import DocType, LoadedDocument, PageContent

logger = logging.getLogger(__name__)

# Tamanho mínimo de texto por página para considerar extração bem-sucedida.
_MIN_PAGE_CHARS = 20

# Exceções que indicam PDF ilegível/corrompido (não erros de programação).
_PDFPLUMBER_ERRORS = (OSError, ValueError, KeyError, TypeError)


def load_pdf(path: Path, doc_type: DocType) -> LoadedDocument:
    """Carrega um PDF e extrai texto de todas as páginas.

    Tenta pdfplumber primeiro. Se uma página retornar texto insuficiente,
    tenta PyMuPDF como fallback. Nunca lança exceção por página vazia —
    registra aviso e continua.

    Parameters
    ----------
    path : Path
        Caminho do arquivo PDF.
    doc_type : DocType
        Tipo do documento, propagado para ``LoadedDocument``.

    Returns
    -------
    LoadedDocument
        Documento com páginas extraídas e hash SHA-256 do arquivo.

    Raises
    ------
    FileNotFoundError
        Se ``path`` não existir.
    ValueError
        Se o arquivo não for um PDF (extensão diferente de ``.pdf``).
    RuntimeError
        Se nenhuma das estratégias de extração conseguir abrir o arquivo.
    """
    path = Path(path).resolve()
    _validate_path(path)

    sha256 = _compute_sha256(path)
    logger.info(
        "Carregando PDF %s (doc_type=%s, sha256=%s…)",
        path.name,
        doc_type.value,
        sha256[:8],
    )

    pages = _extract_pages(path)

    pages_with_text = sum(1 for p in pages if p.text.strip())
    logger.info("PDF %s carregado: %d página(s) com texto.", path.name, pages_with_text)
    return LoadedDocument(path=path, doc_type=doc_type, pages=pages, sha256=sha256)


# ---------------------------------------------------------------------------
# Helpers privados
# ---------------------------------------------------------------------------


def _validate_path(path: Path) -> None:
    """Valida existência e extensão do arquivo.

    Parameters
    ----------
    path : Path
        Caminho já resolvido (``Path.resolve()`` aplicado pelo chamador).

    Raises
    ------
    FileNotFoundError
        Se o arquivo não existir.
    ValueError
        Se a extensão não for ``.pdf``.
    """
    if not path.exists():
        raise FileNotFoundError(f"Arquivo não encontrado: {path}")
    if path.suffix.lower() != ".pdf":
        raise ValueError(f"Arquivo não é um PDF: {path}")


def _compute_sha256(path: Path) -> str:
    """Calcula hash SHA-256 do arquivo em blocos de 64 KB.

    Parameters
    ----------
    path : Path
        Caminho do arquivo.

    Returns
    -------
    str
        Digest hexadecimal SHA-256.
    """
    hasher = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _extract_pages(path: Path) -> list[PageContent]:
    """Extrai texto de cada página usando pdfplumber com fallback PyMuPDF.

    Parameters
    ----------
    path : Path
        Caminho do PDF.

    Returns
    -------
    list[PageContent]
        Uma entrada por página, nunca vazia se o arquivo for válido.
    """
    try:
        pages = _extract_with_pdfplumber(path)
    except _PDFPLUMBER_ERRORS as exc:
        logger.warning(
            "pdfplumber falhou em %s (%s). Tentando PyMuPDF.", path.name, exc
        )
        return _extract_with_pymupdf(path)

    # Verifica páginas com texto insuficiente e tenta fallback por página.
    result: list[PageContent] = []
    for page in pages:
        if len(page.text.strip()) >= _MIN_PAGE_CHARS:
            result.append(page)
        else:
            logger.debug(
                "Página %d de %s sem texto via pdfplumber. Tentando PyMuPDF.",
                page.page_number,
                path.name,
            )
            fallback = _extract_page_with_pymupdf(path, page.page_number)
            result.append(fallback if fallback else page)

    return result


def _extract_with_pdfplumber(path: Path) -> list[PageContent]:
    """Extrai todas as páginas com pdfplumber.

    Parameters
    ----------
    path : Path
        Caminho do PDF.

    Returns
    -------
    list[PageContent]
        Uma entrada por página.
    """
    pages: list[PageContent] = []
    with pdfplumber.open(path) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            text = page.extract_text() or ""
            pages.append(PageContent(page_number=i, text=text))
    return pages


def _extract_with_pymupdf(path: Path) -> list[PageContent]:
    """Extrai todas as páginas com PyMuPDF (fitz).

    Parameters
    ----------
    path : Path
        Caminho do PDF.

    Returns
    -------
    list[PageContent]
        Uma entrada por página.

    Raises
    ------
    RuntimeError
        Se PyMuPDF também falhar ao abrir o arquivo.
    """
    try:
        doc = fitz.open(path)
    except Exception as exc:
        raise RuntimeError(f"PyMuPDF não conseguiu abrir {path}: {exc}") from exc

    pages: list[PageContent] = []
    with doc:
        for i, page in enumerate(doc, start=1):
            text = page.get_text("text") or ""
            pages.append(PageContent(page_number=i, text=text))
    return pages


def _extract_page_with_pymupdf(path: Path, page_number: int) -> PageContent | None:
    """Extrai uma única página com PyMuPDF.

    Parameters
    ----------
    path : Path
        Caminho do PDF.
    page_number : int
        Número da página (1-based).

    Returns
    -------
    PageContent | None
        Conteúdo da página, ou ``None`` se a página não puder ser extraída.
    """
    try:
        doc = fitz.open(path)
        with doc:
            page = doc[page_number - 1]
            text = page.get_text("text") or ""
            return PageContent(page_number=page_number, text=text)
    except Exception as exc:
        logger.warning(
            "PyMuPDF falhou na página %d de %s: %s", page_number, path.name, exc
        )
        return None
