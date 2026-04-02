from __future__ import annotations

from pathlib import Path

from medasist.ingestion.chunker import TextChunk
from medasist.ingestion.metadata import (
    ChunkMetadata,
    build_metadata,
    build_metadata_batch,
)
from medasist.ingestion.schemas import DocType

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

FAKE_PATH = Path("data/raw/zolatril_250mg.pdf")
FAKE_SHA = "abc123def456"


def _make_chunk(
    text: str = "Zolatril 250mg é indicado para infecções bacterianas sintéticas.",
    doc_type: DocType = DocType.BULA,
    chunk_index: int = 0,
) -> TextChunk:
    return TextChunk(
        text=text,
        doc_type=doc_type,
        source_path=FAKE_PATH,
        sha256=FAKE_SHA,
        chunk_index=chunk_index,
    )


# ---------------------------------------------------------------------------
# build_metadata — testes unitários
# ---------------------------------------------------------------------------


def test_build_metadata_returns_chunk_metadata():
    chunk = _make_chunk()
    result = build_metadata(chunk)
    assert isinstance(result, ChunkMetadata)


def test_build_metadata_doc_type_is_string():
    chunk = _make_chunk(doc_type=DocType.BULA)
    result = build_metadata(chunk)
    assert result.doc_type == "bula"
    assert isinstance(result.doc_type, str)


def test_build_metadata_source_path_is_string():
    chunk = _make_chunk()
    result = build_metadata(chunk)
    assert result.source_path == FAKE_PATH.name
    assert isinstance(result.source_path, str)


def test_build_metadata_sha256():
    chunk = _make_chunk()
    result = build_metadata(chunk)
    assert result.sha256 == FAKE_SHA


def test_build_metadata_chunk_index():
    chunk = _make_chunk(chunk_index=3)
    result = build_metadata(chunk)
    assert result.chunk_index == 3


def test_build_metadata_char_count():
    text = "Protocolo Sintético de Triagem v2 para uso em testes automatizados."
    chunk = _make_chunk(text=text)
    result = build_metadata(chunk)
    assert result.char_count == len(text)


def test_build_metadata_all_doc_types():
    for doc_type in DocType:
        chunk = _make_chunk(doc_type=doc_type)
        result = build_metadata(chunk)
        assert result.doc_type == doc_type.value


# ---------------------------------------------------------------------------
# build_metadata_batch — testes de lote
# ---------------------------------------------------------------------------


def test_build_metadata_batch_empty_list():
    result = build_metadata_batch([])
    assert result == []


def test_build_metadata_batch_preserves_order():
    chunks = [_make_chunk(chunk_index=i) for i in range(5)]
    results = build_metadata_batch(chunks)
    assert len(results) == 5
    for i, meta in enumerate(results):
        assert meta.chunk_index == i


def test_build_metadata_batch_returns_list_of_chunk_metadata():
    chunks = [_make_chunk(), _make_chunk(doc_type=DocType.DIRETRIZ, chunk_index=1)]
    results = build_metadata_batch(chunks)
    assert all(isinstance(m, ChunkMetadata) for m in results)


def test_build_metadata_batch_different_doc_types():
    chunks = [
        _make_chunk(doc_type=DocType.BULA, chunk_index=0),
        _make_chunk(doc_type=DocType.PROTOCOLO, chunk_index=1),
    ]
    results = build_metadata_batch(chunks)
    assert results[0].doc_type == "bula"
    assert results[1].doc_type == "protocolo"
