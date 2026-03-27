from __future__ import annotations

from pathlib import Path

import pytest

from medasist.ingestion.chunker import TextChunk, chunk_document
from medasist.ingestion.schemas import DocType, LoadedDocument, PageContent


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FAKE_PATH = Path("data/raw/zolatril_250mg.pdf")
FAKE_SHA = "abc123def456"


def _make_doc(doc_type: DocType, text: str) -> LoadedDocument:
    return LoadedDocument(
        path=FAKE_PATH,
        doc_type=doc_type,
        sha256=FAKE_SHA,
        pages=(PageContent(page_number=1, text=text),),
    )


def _long_text(n_words: int = 300) -> str:
    """Gera texto sintético longo o suficiente para produzir múltiplos chunks."""
    word = "Zolatril "
    return (word * n_words).strip()


# ---------------------------------------------------------------------------
# Testes de metadados herdados
# ---------------------------------------------------------------------------


def test_chunk_inherits_metadata(settings):
    doc = _make_doc(DocType.BULA, _long_text())
    chunks = chunk_document(doc, settings)

    assert len(chunks) > 0
    for chunk in chunks:
        assert chunk.doc_type == DocType.BULA
        assert chunk.source_path == FAKE_PATH
        assert chunk.sha256 == FAKE_SHA


def test_chunk_index_is_sequential(settings):
    doc = _make_doc(DocType.BULA, _long_text())
    chunks = chunk_document(doc, settings)

    assert len(chunks) > 0
    for i, chunk in enumerate(chunks):
        assert chunk.chunk_index == i


# ---------------------------------------------------------------------------
# Testes de tamanho por DocType
# ---------------------------------------------------------------------------


def test_chunk_bula_uses_correct_size_and_overlap(settings):
    doc = _make_doc(DocType.BULA, _long_text(500))
    chunks = chunk_document(doc, settings)

    assert len(chunks) > 1
    for chunk in chunks[:-1]:  # último pode ser menor
        assert len(chunk.text) <= settings.chunk_size_bula + settings.chunk_overlap_bula


def test_chunk_diretriz_uses_correct_size_and_overlap(settings):
    doc = _make_doc(DocType.DIRETRIZ, _long_text(500))
    chunks = chunk_document(doc, settings)

    assert len(chunks) > 1
    for chunk in chunks[:-1]:
        assert (
            len(chunk.text)
            <= settings.chunk_size_diretriz + settings.chunk_overlap_diretriz
        )


def test_chunk_protocolo_uses_correct_size_and_overlap(settings):
    doc = _make_doc(DocType.PROTOCOLO, _long_text(500))
    chunks = chunk_document(doc, settings)

    assert len(chunks) > 1
    for chunk in chunks[:-1]:
        assert (
            len(chunk.text)
            <= settings.chunk_size_protocolo + settings.chunk_overlap_protocolo
        )


def test_chunk_manual_uses_correct_size_and_overlap(settings):
    doc = _make_doc(DocType.MANUAL, _long_text(500))
    chunks = chunk_document(doc, settings)

    assert len(chunks) > 1
    for chunk in chunks[:-1]:
        assert (
            len(chunk.text)
            <= settings.chunk_size_manual + settings.chunk_overlap_manual
        )


# ---------------------------------------------------------------------------
# Testes de edge cases
# ---------------------------------------------------------------------------


def test_chunk_empty_document_returns_empty_list(settings):
    doc = LoadedDocument(
        path=FAKE_PATH,
        doc_type=DocType.BULA,
        sha256=FAKE_SHA,
        pages=(),
    )
    chunks = chunk_document(doc, settings)
    assert chunks == []


def test_chunk_ignores_short_chunks(settings):
    # Texto curto que vai gerar apenas um chunk pequeno (< 50 chars)
    doc = _make_doc(DocType.BULA, "OK")
    chunks = chunk_document(doc, settings)
    assert chunks == []


def test_chunk_bula_respects_sections(settings):
    # Texto com seções bem definidas — cada parágrafo > chunk_size para forçar split
    section = "Zolatril 250mg é indicado para tratamento de infecções bacterianas. " * 15
    text = f"{section}\n\n{section}\n\n{section}"
    doc = _make_doc(DocType.BULA, text)
    chunks = chunk_document(doc, settings)

    assert len(chunks) > 1
    # Nenhum chunk deve ser vazio
    for chunk in chunks:
        assert chunk.text.strip() != ""
