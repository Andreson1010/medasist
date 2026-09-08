from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from medasist.policies.baseline import BaselineEntry
from medasist.policies.scanner import collect_files, generate_baseline, run_scan

_CLEAN_PY = (
    "from __future__ import annotations\n"
    "\n"
    "import logging\n"
    "\n"
    "logger = logging.getLogger(__name__)\n"
    "\n"
    "X = 1\n"
)

_BAD_PY = (
    "from __future__ import annotations\n"
    "\n"
    "import logging\n"
    "\n"
    "logger = logging.getLogger(__name__)\n"
    "\n"
    'print("x")\n'
)


def _write(tree: Path, rel: str, content: str) -> Path:
    target = tree / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target


class TestCollectFiles:
    def test_collects_py_recursively(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        _write(src, "a.py", "x = 1\n")
        _write(src, "sub/b.py", "x = 2\n")
        files = collect_files([src])
        rels = sorted(f.relative_to(tmp_path).as_posix() for f in files)
        assert rels == ["src/a.py", "src/sub/b.py"]

    def test_skips_non_py(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        _write(src, "a.py", "x = 1\n")
        _write(src, "b.txt", "texto\n")
        files = collect_files([src])
        assert len(files) == 1

    def test_skips_excluded_segments(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        _write(src, "ok.py", "x = 1\n")
        _write(src, "__pycache__/c.py", "x = 2\n")
        _write(src, ".hidden/h.py", "x = 3\n")
        _write(src, "data/d.py", "x = 4\n")
        _write(src, "logs/l.py", "x = 5\n")
        files = collect_files([src])
        assert len(files) == 1

    def test_default_excludes_apply_even_for_extra_root(self, tmp_path: Path) -> None:
        _write(tmp_path, ".opencode/x.py", "x = 1\n")
        _write(tmp_path, "src/a.py", "x = 2\n")
        files = collect_files([tmp_path])
        rels = sorted(f.relative_to(tmp_path).as_posix() for f in files)
        assert rels == ["src/a.py"]

    def test_custom_excludes(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        _write(src, "a.py", "x = 1\n")
        _write(src, "vendors/v.py", "x = 2\n")
        files = collect_files([src], excludes=("vendors",))
        assert len(files) == 1

    def test_missing_root_is_ignored(self, tmp_path: Path) -> None:
        assert collect_files([tmp_path / "nao-existe"]) == []


class TestRunScan:
    def test_clean_tree_returns_no_failures(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        _write(src, "ok.py", _CLEAN_PY)
        report = run_scan([src], baseline_path=tmp_path / "policies.toml")
        assert report.files_scanned == 1
        assert report.new_violations == ()
        assert report.obsolete_entries == ()
        assert report.has_failures is False

    def test_violation_without_baseline_is_new(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        _write(src, "bad.py", _BAD_PY)
        report = run_scan([src], baseline_path=tmp_path / "nao-existe.toml")
        assert any(v.rule_id == "NO-PRINT" for v in report.new_violations)
        assert report.has_failures is True

    def test_matching_baseline_suppresses_violation(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        _write(src, "bad.py", _BAD_PY)
        baseline = tmp_path / "policies.toml"
        baseline.write_text(
            "[[entries]]\n"
            'rule_id = "NO-PRINT"\n'
            'path = "src/bad.py"\n'
            'location = "<module>"\n'
            'reason = "teste"\n',
            encoding="utf-8",
        )
        report = run_scan([src], baseline_path=baseline)
        assert report.total_violations == 1
        assert report.new_violations == ()
        assert len(report.baselined_violations) == 1
        assert report.has_failures is False

    def test_obsolete_baseline_entry_fails(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        _write(src, "ok.py", _CLEAN_PY)
        baseline = tmp_path / "policies.toml"
        baseline.write_text(
            "[[entries]]\n"
            'rule_id = "NO-PRINT"\n'
            'path = "src/ok.py"\n'
            'location = "<module>"\n'
            'reason = "teste"\n',
            encoding="utf-8",
        )
        report = run_scan([src], baseline_path=baseline)
        assert len(report.obsolete_entries) == 1
        assert report.has_failures is True

    def test_malformed_baseline_raises(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        _write(src, "ok.py", _CLEAN_PY)
        baseline = tmp_path / "policies.toml"
        baseline.write_text("[[entries\n", encoding="utf-8")

        with pytest.raises(tomllib.TOMLDecodeError):
            run_scan([src], baseline_path=baseline)

    def test_unreadable_file_is_skipped(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        target = src / "broken.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"\xff\xfe invalido")
        report = run_scan([src], baseline_path=tmp_path / "nao-existe.toml")
        assert report.files_scanned == 0
        assert report.files_skipped == 1

    def test_relative_paths_are_posix(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        _write(src, "x.py", 'print("y")\n')
        report = run_scan([src], baseline_path=tmp_path / "nao-existe.toml")
        assert report.new_violations[0].path == "src/x.py"


class TestGenerateBaseline:
    def test_generates_active_entries_for_violations(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        _write(src, "bad.py", _BAD_PY)
        entries = generate_baseline([src])
        assert len(entries) == 1
        entry = entries[0]
        assert isinstance(entry, BaselineEntry)
        assert entry.rule_id == "NO-PRINT"
        assert entry.path == "src/bad.py"
        assert entry.location == "<module>"
        assert entry.active is True

    def test_clean_tree_generates_empty_baseline(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        _write(src, "ok.py", _CLEAN_PY)
        assert generate_baseline([src]) == ()
