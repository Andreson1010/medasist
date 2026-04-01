from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from ingest_docs import main, parse_args
from medasist.ingestion.pipeline import IngestionResult
from medasist.ingestion.schemas import DocType


# ---------------------------------------------------------------------------
# parse_args
# ---------------------------------------------------------------------------


def test_parse_args_valid(tmp_path: Path) -> None:
    args = parse_args(["--dir", str(tmp_path), "--doc-type", "bula"])
    assert args.dir == tmp_path
    assert args.doc_type == "bula"
    assert args.dry_run is False


def test_parse_args_invalid_doc_type(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        parse_args(["--dir", str(tmp_path), "--doc-type", "invalido"])


# ---------------------------------------------------------------------------
# main — verificações de diretório e PDFs
# ---------------------------------------------------------------------------


def test_main_dir_not_found() -> None:
    result = main(["--dir", "/caminho/inexistente", "--doc-type", "bula"])
    assert result == 1


def test_main_empty_dir(tmp_path: Path) -> None:
    result = main(["--dir", str(tmp_path), "--doc-type", "bula"])
    assert result == 0


# ---------------------------------------------------------------------------
# main — dry-run
# ---------------------------------------------------------------------------


def test_main_dry_run(tmp_path: Path, mocker: MagicMock) -> None:
    (tmp_path / "doc.pdf").write_bytes(b"%PDF-1.4")
    mock_ingest = mocker.patch("ingest_docs.ingest_directory")

    result = main(["--dir", str(tmp_path), "--doc-type", "bula", "--dry-run"])

    assert result == 0
    mock_ingest.assert_not_called()


# ---------------------------------------------------------------------------
# main — ingestão com mock de ingest_directory
# ---------------------------------------------------------------------------


def _make_result(
    path: Path,
    chunks_indexed: int = 5,
    skipped: bool = False,
    error: str | None = None,
) -> IngestionResult:
    return IngestionResult(
        path=path,
        doc_type=DocType.BULA,
        sha256="abc123",
        chunks_indexed=chunks_indexed,
        skipped=skipped,
        error=error,
    )


def test_main_success(tmp_path: Path, mocker: MagicMock) -> None:
    pdf = tmp_path / "bula.pdf"
    pdf.write_bytes(b"%PDF-1.4")

    mocker.patch("ingest_docs.get_settings", return_value=MagicMock())
    mocker.patch("ingest_docs.chromadb.PersistentClient", return_value=MagicMock())
    mocker.patch("ingest_docs.build_embed_fn", return_value=MagicMock())
    mocker.patch(
        "ingest_docs.ingest_directory",
        return_value=[_make_result(pdf)],
    )

    result = main(["--dir", str(tmp_path), "--doc-type", "bula"])
    assert result == 0


def test_main_with_errors(tmp_path: Path, mocker: MagicMock) -> None:
    pdf = tmp_path / "bula.pdf"
    pdf.write_bytes(b"%PDF-1.4")

    mocker.patch("ingest_docs.get_settings", return_value=MagicMock())
    mocker.patch("ingest_docs.chromadb.PersistentClient", return_value=MagicMock())
    mocker.patch("ingest_docs.build_embed_fn", return_value=MagicMock())
    mocker.patch(
        "ingest_docs.ingest_directory",
        return_value=[_make_result(pdf, chunks_indexed=0, error="falha simulada")],
    )

    result = main(["--dir", str(tmp_path), "--doc-type", "bula"])
    assert result == 1


def test_main_skipped_docs(tmp_path: Path, mocker: MagicMock) -> None:
    pdf = tmp_path / "bula.pdf"
    pdf.write_bytes(b"%PDF-1.4")

    mocker.patch("ingest_docs.get_settings", return_value=MagicMock())
    mocker.patch("ingest_docs.chromadb.PersistentClient", return_value=MagicMock())
    mocker.patch("ingest_docs.build_embed_fn", return_value=MagicMock())
    mocker.patch(
        "ingest_docs.ingest_directory",
        return_value=[_make_result(pdf, chunks_indexed=0, skipped=True)],
    )

    result = main(["--dir", str(tmp_path), "--doc-type", "bula"])
    assert result == 0


def test_main_all_skipped_no_error(tmp_path: Path, mocker: MagicMock) -> None:
    pdfs = [tmp_path / f"doc{i}.pdf" for i in range(3)]
    for p in pdfs:
        p.write_bytes(b"%PDF-1.4")

    mocker.patch("ingest_docs.get_settings", return_value=MagicMock())
    mocker.patch("ingest_docs.chromadb.PersistentClient", return_value=MagicMock())
    mocker.patch("ingest_docs.build_embed_fn", return_value=MagicMock())
    mocker.patch(
        "ingest_docs.ingest_directory",
        return_value=[_make_result(p, chunks_indexed=0, skipped=True) for p in pdfs],
    )

    result = main(["--dir", str(tmp_path), "--doc-type", "bula"])
    assert result == 0


def test_main_mixed_results(tmp_path: Path, mocker: MagicMock) -> None:
    ok_pdf = tmp_path / "ok.pdf"
    err_pdf = tmp_path / "err.pdf"
    skip_pdf = tmp_path / "skip.pdf"
    for p in [ok_pdf, err_pdf, skip_pdf]:
        p.write_bytes(b"%PDF-1.4")

    mocker.patch("ingest_docs.get_settings", return_value=MagicMock())
    mocker.patch("ingest_docs.chromadb.PersistentClient", return_value=MagicMock())
    mocker.patch("ingest_docs.build_embed_fn", return_value=MagicMock())
    mocker.patch(
        "ingest_docs.ingest_directory",
        return_value=[
            _make_result(ok_pdf),
            _make_result(err_pdf, chunks_indexed=0, error="falha"),
            _make_result(skip_pdf, chunks_indexed=0, skipped=True),
        ],
    )

    result = main(["--dir", str(tmp_path), "--doc-type", "bula"])
    assert result == 1
