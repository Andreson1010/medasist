from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import chromadb
import pytest

from medasist.config import Settings
from medasist.ingestion.pipeline import (
    IngestionResult,
    ingest_directory,
    ingest_document,
)
from medasist.ingestion.schemas import DocType, LoadedDocument, PageContent

# ---------------------------------------------------------------------------
# Helpers de teste
# ---------------------------------------------------------------------------

_LONG_TEXT = (
    "Medicamento sintético Alphazol — indicado para dores agudas moderadas. " * 30
)


def _fake_embed(texts: list[str]) -> list[list[float]]:
    """Embedding fake de 4 dimensões para testes."""
    return [[0.1, 0.2, 0.3, 0.4] for _ in texts]


def _make_doc(
    path: Path, doc_type: DocType, sha256: str, text: str = _LONG_TEXT
) -> LoadedDocument:
    return LoadedDocument(
        path=path.resolve(),
        doc_type=doc_type,
        sha256=sha256,
        pages=(PageContent(page_number=1, text=text),),
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def settings() -> Settings:
    return Settings(
        lm_studio_base_url="http://localhost:1234/v1",
        lm_studio_api_key="lm-studio",
        chunk_size_bula=200,
        chunk_overlap_bula=20,
        chunk_size_diretriz=200,
        chunk_overlap_diretriz=20,
        chunk_size_protocolo=200,
        chunk_overlap_protocolo=20,
        chunk_size_manual=200,
        chunk_overlap_manual=20,
    )


@pytest.fixture
def chroma(tmp_path) -> chromadb.ClientAPI:
    """PersistentClient em diretório temporário — isolado por teste."""
    return chromadb.PersistentClient(path=str(tmp_path / "chroma"))


# ---------------------------------------------------------------------------
# ingest_document
# ---------------------------------------------------------------------------


def test_ingest_document_indexes_chunks(tmp_path, settings, chroma):
    """Pipeline indexa chunks no ChromaDB e retorna contagem correta."""
    pdf = tmp_path / "bula_sintetica.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    sha = "deadbeef" * 8  # 64 chars

    with patch(
        "medasist.ingestion.pipeline.load_pdf",
        return_value=_make_doc(pdf, DocType.BULA, sha),
    ):
        result = ingest_document(pdf, DocType.BULA, chroma, settings, _fake_embed)

    assert isinstance(result, IngestionResult)
    assert result.skipped is False
    assert result.error is None
    assert result.chunks_indexed > 0
    assert result.sha256 == sha
    assert result.doc_type == DocType.BULA


def test_ingest_document_skips_duplicate(tmp_path, settings, chroma):
    """Segunda ingestão do mesmo sha256 é pulada (idempotência)."""
    pdf = tmp_path / "bula_b.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    sha = "cafebabe" * 8

    with patch(
        "medasist.ingestion.pipeline.load_pdf",
        return_value=_make_doc(pdf, DocType.BULA, sha),
    ):
        first = ingest_document(pdf, DocType.BULA, chroma, settings, _fake_embed)
        second = ingest_document(pdf, DocType.BULA, chroma, settings, _fake_embed)

    assert first.skipped is False
    assert first.chunks_indexed > 0
    assert second.skipped is True
    assert second.chunks_indexed == 0
    assert second.error is None


def test_ingest_document_captures_load_error(tmp_path, settings, chroma):
    """Erro ao carregar o PDF é registrado em result.error, sem lançar exceção."""
    pdf = tmp_path / "corrompido.pdf"
    pdf.write_bytes(b"not a pdf")

    with patch(
        "medasist.ingestion.pipeline.load_pdf",
        side_effect=RuntimeError("PDF ilegível"),
    ):
        result = ingest_document(pdf, DocType.BULA, chroma, settings, _fake_embed)

    assert result.error == "PDF ilegível"
    assert result.chunks_indexed == 0
    assert result.skipped is False
    assert result.sha256 == ""


def test_ingest_document_empty_text_returns_zero_chunks(tmp_path, settings, chroma):
    """Documento sem texto gera 0 chunks sem erro."""
    pdf = tmp_path / "vazio.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    sha = "00000000" * 8

    with patch(
        "medasist.ingestion.pipeline.load_pdf",
        return_value=_make_doc(pdf, DocType.PROTOCOLO, sha, text="   "),
    ):
        result = ingest_document(pdf, DocType.PROTOCOLO, chroma, settings, _fake_embed)

    assert result.chunks_indexed == 0
    assert result.skipped is False
    assert result.error is None


def test_ingest_document_uses_correct_collection(tmp_path, settings, chroma):
    """Cada DocType é indexado na coleção correta."""
    pdf = tmp_path / "diretriz.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    sha = "11223344" * 8

    with patch(
        "medasist.ingestion.pipeline.load_pdf",
        return_value=_make_doc(pdf, DocType.DIRETRIZ, sha),
    ):
        result = ingest_document(pdf, DocType.DIRETRIZ, chroma, settings, _fake_embed)

    assert result.doc_type == DocType.DIRETRIZ
    assert result.chunks_indexed > 0

    col = chroma.get_collection(settings.collection_diretrizes)
    assert col.count() > 0


def test_ingest_document_ids_are_deterministic(tmp_path, settings, chroma):
    """Upsert com mesmo sha256 não duplica chunks na coleção."""
    pdf = tmp_path / "protocolo.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    sha = "aabbccdd" * 8
    doc = _make_doc(pdf, DocType.PROTOCOLO, sha)

    with patch("medasist.ingestion.pipeline.load_pdf", return_value=doc):
        ingest_document(pdf, DocType.PROTOCOLO, chroma, settings, _fake_embed)

    col = chroma.get_collection(settings.collection_protocolos)
    count_after_first = col.count()

    # Segunda chamada: doc já foi marcado como indexado → skip
    with patch("medasist.ingestion.pipeline.load_pdf", return_value=doc):
        ingest_document(pdf, DocType.PROTOCOLO, chroma, settings, _fake_embed)

    assert col.count() == count_after_first


# ---------------------------------------------------------------------------
# ingest_directory
# ---------------------------------------------------------------------------


def test_ingest_directory_processes_all_pdfs(tmp_path, settings, chroma):
    """Todos os PDFs do diretório são processados."""
    (tmp_path / "bula_a.pdf").write_bytes(b"%PDF-1.4")
    (tmp_path / "bula_b.pdf").write_bytes(b"%PDF-1.4")

    def _side_effect(path: Path, doc_type: DocType) -> LoadedDocument:
        sha = path.stem.encode().hex().ljust(64, "0")[:64]
        return _make_doc(path, doc_type, sha)

    with patch("medasist.ingestion.pipeline.load_pdf", side_effect=_side_effect):
        results = ingest_directory(
            tmp_path, DocType.BULA, chroma, settings, _fake_embed
        )

    assert len(results) == 2
    assert all(r.error is None for r in results)
    assert all(r.chunks_indexed > 0 for r in results)


def test_ingest_directory_raises_for_missing_dir(settings, chroma):
    """Diretório inexistente lança NotADirectoryError."""
    with pytest.raises(NotADirectoryError):
        ingest_directory(
            Path("/nonexistent/medasist_dir"),
            DocType.BULA,
            chroma,
            settings,
            _fake_embed,
        )


def test_ingest_directory_returns_empty_for_no_pdfs(tmp_path, settings, chroma):
    """Diretório sem PDFs retorna lista vazia."""
    (tmp_path / "notes.txt").write_text("irrelevante")

    results = ingest_directory(tmp_path, DocType.BULA, chroma, settings, _fake_embed)

    assert results == []


def test_ingest_directory_partial_errors_dont_abort(tmp_path, settings, chroma):
    """Erro em um PDF não impede processamento dos demais."""
    (tmp_path / "ok.pdf").write_bytes(b"%PDF-1.4")
    (tmp_path / "ruim.pdf").write_bytes(b"%PDF-1.4")

    sha_ok = "feedface" * 8

    def _side_effect(path: Path, doc_type: DocType) -> LoadedDocument:
        if "ruim" in path.name:
            raise RuntimeError("Falha simulada")
        return _make_doc(path, doc_type, sha_ok)

    with patch("medasist.ingestion.pipeline.load_pdf", side_effect=_side_effect):
        results = ingest_directory(
            tmp_path, DocType.BULA, chroma, settings, _fake_embed
        )

    assert len(results) == 2
    ok = next(r for r in results if r.error is None)
    err = next(r for r in results if r.error is not None)
    assert ok.chunks_indexed > 0
    assert "Falha simulada" in err.error
