from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from medasist.ingestion.loader import _MIN_PAGE_CHARS, load_pdf
from medasist.ingestion.schemas import DocType, LoadedDocument, PageContent

# Módulo alvo para patches (imports são top-level em loader.py).
_PLUMBER = "medasist.ingestion.loader.pdfplumber"
_FITZ = "medasist.ingestion.loader.fitz"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def fake_pdf(tmp_path: Path) -> Path:
    """Cria um arquivo .pdf sintético (não-válido — usado apenas com mocks)."""
    p = tmp_path / "medicamento_xyz.pdf"
    p.write_bytes(b"%PDF-1.4 fake content")
    return p


@pytest.fixture()
def non_pdf(tmp_path: Path) -> Path:
    p = tmp_path / "documento.txt"
    p.write_text("conteúdo qualquer")
    return p


def _make_pdfplumber_mock(pages_text: list[str]) -> MagicMock:
    """Constrói mock de pdfplumber.open retornando páginas sintéticas."""
    mock_pages = []
    for text in pages_text:
        page = MagicMock()
        page.extract_text.return_value = text
        mock_pages.append(page)

    mock_pdf = MagicMock()
    mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
    mock_pdf.__exit__ = MagicMock(return_value=False)
    mock_pdf.pages = mock_pages
    return mock_pdf


def _make_fitz_mock(pages_text: list[str]) -> MagicMock:
    """Constrói mock de fitz.open retornando páginas sintéticas."""
    mock_pages = []
    for text in pages_text:
        page = MagicMock()
        page.get_text.return_value = text
        mock_pages.append(page)

    mock_doc = MagicMock()
    mock_doc.__enter__ = MagicMock(return_value=mock_doc)
    mock_doc.__exit__ = MagicMock(return_value=False)
    mock_doc.__iter__ = MagicMock(return_value=iter(mock_pages))
    return mock_doc


# ---------------------------------------------------------------------------
# Validação de entrada
# ---------------------------------------------------------------------------


def test_load_pdf_raises_when_file_not_found(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="Arquivo não encontrado"):
        load_pdf(tmp_path / "inexistente.pdf", DocType.BULA)


def test_load_pdf_raises_when_not_pdf(non_pdf: Path) -> None:
    with pytest.raises(ValueError, match="não é um PDF"):
        load_pdf(non_pdf, DocType.BULA)


# ---------------------------------------------------------------------------
# Extração via pdfplumber (caminho feliz)
# ---------------------------------------------------------------------------


def test_load_pdf_returns_loaded_document(fake_pdf: Path) -> None:
    mock_pdf = _make_pdfplumber_mock(
        [
            "Bula do medicamento XYZ. Indicações: febre e dor.",
            "Posologia: 1 comprimido a cada 8 horas.",
        ]
    )
    with patch(f"{_PLUMBER}.open", return_value=mock_pdf):
        doc = load_pdf(fake_pdf, DocType.BULA)

    assert isinstance(doc, LoadedDocument)
    assert doc.doc_type == DocType.BULA
    assert doc.path == fake_pdf.resolve()


def test_load_pdf_extracts_all_pages(fake_pdf: Path) -> None:
    texts = [
        "Página um com conteúdo médico suficiente.",
        "Página dois com mais informações clínicas.",
    ]
    mock_pdf = _make_pdfplumber_mock(texts)
    with patch(f"{_PLUMBER}.open", return_value=mock_pdf):
        doc = load_pdf(fake_pdf, DocType.DIRETRIZ)

    assert len(doc.pages) == 2
    assert doc.pages[0] == PageContent(page_number=1, text=texts[0])
    assert doc.pages[1] == PageContent(page_number=2, text=texts[1])


def test_load_pdf_computes_sha256(fake_pdf: Path) -> None:
    expected = hashlib.sha256(fake_pdf.read_bytes()).hexdigest()
    mock_pdf = _make_pdfplumber_mock(["Conteúdo sintético suficiente para o teste."])
    with patch(f"{_PLUMBER}.open", return_value=mock_pdf):
        doc = load_pdf(fake_pdf, DocType.BULA)

    assert doc.sha256 == expected


def test_full_text_concatenates_pages(fake_pdf: Path) -> None:
    texts = [
        "Primeira seção do protocolo clínico.",
        "Segunda seção com orientações detalhadas.",
    ]
    mock_pdf = _make_pdfplumber_mock(texts)
    with patch(f"{_PLUMBER}.open", return_value=mock_pdf):
        doc = load_pdf(fake_pdf, DocType.PROTOCOLO)

    assert texts[0] in doc.full_text
    assert texts[1] in doc.full_text


# ---------------------------------------------------------------------------
# Fallback para PyMuPDF
# ---------------------------------------------------------------------------


def test_load_pdf_falls_back_to_pymupdf_when_pdfplumber_raises(fake_pdf: Path) -> None:
    fitz_text = "Texto extraído via PyMuPDF com conteúdo suficiente."
    mock_doc = _make_fitz_mock([fitz_text])

    with (
        patch(f"{_PLUMBER}.open", side_effect=OSError("PDF corrompido")),
        patch(f"{_FITZ}.open", return_value=mock_doc),
    ):
        doc = load_pdf(fake_pdf, DocType.MANUAL)

    assert len(doc.pages) == 1
    assert doc.pages[0].text == fitz_text


def test_load_pdf_uses_pymupdf_for_pages_with_insufficient_text(fake_pdf: Path) -> None:
    short_text = "."  # abaixo do _MIN_PAGE_CHARS
    full_text = "Conteúdo completo da página via PyMuPDF para teste de fallback."

    plumber_mock = _make_pdfplumber_mock([short_text])
    fitz_page = MagicMock()
    fitz_page.get_text.return_value = full_text
    fitz_doc = MagicMock()
    fitz_doc.__enter__ = MagicMock(return_value=fitz_doc)
    fitz_doc.__exit__ = MagicMock(return_value=False)
    fitz_doc.__getitem__ = MagicMock(return_value=fitz_page)

    with (
        patch(f"{_PLUMBER}.open", return_value=plumber_mock),
        patch(f"{_FITZ}.open", return_value=fitz_doc),
    ):
        doc = load_pdf(fake_pdf, DocType.BULA)

    assert doc.pages[0].text == full_text


def test_load_pdf_raises_runtime_error_when_both_extractors_fail(
    fake_pdf: Path,
) -> None:
    with (
        patch(f"{_PLUMBER}.open", side_effect=OSError("corrompido")),
        patch(f"{_FITZ}.open", side_effect=Exception("fitz falhou")),
        pytest.raises(RuntimeError, match="PyMuPDF não conseguiu abrir"),
    ):
        load_pdf(fake_pdf, DocType.BULA)


# ---------------------------------------------------------------------------
# full_text ignora páginas vazias — testado diretamente no dataclass
# ---------------------------------------------------------------------------


def test_full_text_skips_whitespace_only_pages() -> None:
    """Testa full_text diretamente no LoadedDocument sem passar por load_pdf."""
    doc = LoadedDocument(
        path=Path("/tmp/teste.pdf"),
        doc_type=DocType.BULA,
        sha256="abc123",
        pages=[
            PageContent(page_number=1, text="Conteúdo real da bula com informações."),
            PageContent(page_number=2, text="   "),
            PageContent(page_number=3, text="Outra página com dados relevantes."),
        ],
    )

    assert "   " not in doc.full_text
    assert "Conteúdo real" in doc.full_text
    assert "Outra página" in doc.full_text


# ---------------------------------------------------------------------------
# Constante MIN_PAGE_CHARS
# ---------------------------------------------------------------------------


def test_min_page_chars_is_positive() -> None:
    assert _MIN_PAGE_CHARS > 0
