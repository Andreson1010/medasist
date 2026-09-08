from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from medasist.policies.baseline import (
    BaselineEntry,
    find_obsolete,
    load_allowlist,
    load_baseline,
    match,
    normalize_path,
    save_baseline,
)
from medasist.policies.report import PolicyViolation

_SAMPLE_TOML = """\
# Baseline do policy checker

[[entries]]
rule_id = "FUNC-LENGTH"
path = "src/medasist/generation/chain.py"
location = "_run_single"
reason = "débito AD-005"

[[entries]]
rule_id = "NO-PRINT"
path = "scripts/evaluate_rag.py"
location = "_print_report"
reason = "carve-out stdout de CLI (OQ-03)"
active = false
"""


def _entry(**overrides) -> BaselineEntry:
    defaults = {
        "rule_id": "FUNC-LENGTH",
        "path": "src/medasist/generation/chain.py",
        "location": "_run_single",
        "reason": "débito AD-005",
    }
    defaults.update(overrides)
    return BaselineEntry(**defaults)


def _violation(**overrides) -> PolicyViolation:
    defaults = {
        "rule_id": "FUNC-LENGTH",
        "path": "src/medasist/generation/chain.py",
        "line": 10,
        "message": "função com mais de 50 linhas",
        "symbol": "_run_single",
    }
    defaults.update(overrides)
    return PolicyViolation(**defaults)


class TestBaselineEntry:
    def test_active_defaults_true(self) -> None:
        entry = _entry()
        assert entry.active is True

    def test_inactive_explicit(self) -> None:
        entry = _entry(active=False)
        assert entry.active is False

    def test_frozen(self) -> None:
        entry = _entry()
        with pytest.raises(AttributeError):
            entry.reason = "outra"  # type: ignore[misc]


class TestLoadBaseline:
    def test_loads_entries_and_active_default(self, tmp_path: Path) -> None:
        path = tmp_path / "policies.toml"
        path.write_text(_SAMPLE_TOML, encoding="utf-8")
        entries = load_baseline(path)
        assert len(entries) == 2
        assert entries[0].rule_id == "FUNC-LENGTH"
        assert entries[0].path == "src/medasist/generation/chain.py"
        assert entries[0].location == "_run_single"
        assert entries[0].active is True
        assert entries[1].active is False

    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        entries = load_baseline(tmp_path / "nao-existe.toml")
        assert entries == ()

    def test_malformed_toml_raises(self, tmp_path: Path) -> None:
        path = tmp_path / "quebrado.toml"
        path.write_text("[[entries]\nrule_id = ", encoding="utf-8")
        with pytest.raises(tomllib.TOMLDecodeError):
            load_baseline(path)


class TestLoadAllowlist:
    def test_loads_tokens_lowercased(self, tmp_path: Path) -> None:
        path = tmp_path / "policies.toml"
        path.write_text(
            "[allowlist.patient_data]\n"
            'tokens = ["Zolatril", "Alphazol", "amoxicilina"]\n',
            encoding="utf-8",
        )
        assert load_allowlist(path) == frozenset(
            {"zolatril", "alphazol", "amoxicilina"}
        )

    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        assert load_allowlist(tmp_path / "nao-existe.toml") == frozenset()

    def test_missing_section_returns_empty(self, tmp_path: Path) -> None:
        path = tmp_path / "policies.toml"
        path.write_text('[[entries]]\nrule_id = "X"\n', encoding="utf-8")
        assert load_allowlist(path) == frozenset()


class TestSaveBaseline:
    def test_round_trip_identical(self, tmp_path: Path) -> None:
        entries = (
            _entry(),
            _entry(
                rule_id="NO-PRINT",
                path="scripts/evaluate_rag.py",
                location="_print_report",
                reason="carve-out stdout de CLI (OQ-03)",
            ),
            _entry(
                rule_id="PATIENT-DATA",
                path="src/x.py",
                location="42",
                reason="escape hatch",
            ),
        )
        path = tmp_path / "policies.toml"
        save_baseline(path, entries)
        loaded = load_baseline(path)
        assert loaded == entries

    def test_preserves_existing_allowlist(self, tmp_path: Path) -> None:
        path = tmp_path / "policies.toml"
        path.write_text(
            "[allowlist.patient_data]\n" 'tokens = ["Zolatril", "meufix"]\n',
            encoding="utf-8",
        )
        save_baseline(path, (_entry(),))
        assert load_allowlist(path) == frozenset({"zolatril", "meufix"})
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        assert data["allowlist"]["patient_data"]["tokens"] == ["Zolatril", "meufix"]

    def test_no_allowlist_when_none_exists(self, tmp_path: Path) -> None:
        path = tmp_path / "policies.toml"
        save_baseline(path, (_entry(),))
        text = path.read_text(encoding="utf-8")
        assert "allowlist" not in text
        assert load_allowlist(path) == frozenset()

    def test_escapes_backslash_and_quote(self, tmp_path: Path) -> None:
        entries = (
            _entry(
                rule_id="PATIENT-DATA",
                path='src/weird"name\\x.py',
                location="7",
                reason='motivo com "aspas" e \\ barra',
            ),
        )
        path = tmp_path / "policies.toml"
        save_baseline(path, entries)
        loaded = load_baseline(path)
        assert loaded == entries


class TestNormalizePath:
    def test_relative_to_base_and_posix(self, tmp_path: Path) -> None:
        base = tmp_path
        file = base / "src" / "medasist" / "x.py"
        assert normalize_path(file, base) == "src/medasist/x.py"

    def test_windows_style_separators(self) -> None:
        assert normalize_path(Path("C:/proj/src/x.py"), Path("C:/proj")) == "src/x.py"

    def test_default_base_is_cwd(self) -> None:
        cwd = Path.cwd()
        assert normalize_path(cwd / "src" / "x.py") == "src/x.py"


class TestMatch:
    def test_matches_by_rule_path_location(self) -> None:
        baseline = (
            _entry(),
            _entry(
                rule_id="NO-PRINT",
                path="scripts/evaluate_rag.py",
                location="_print_report",
            ),
        )
        found = match(
            baseline, "FUNC-LENGTH", "src/medasist/generation/chain.py", "_run_single"
        )
        assert found == baseline[0]

    def test_no_match_returns_none(self) -> None:
        baseline = (_entry(),)
        assert match(baseline, "FUNC-LENGTH", "src/other.py", "_run_single") is None

    def test_patient_data_entry_still_matches(self) -> None:
        # Entrada PATIENT-DATA escrita à mão continua funcional (backward compat)
        baseline = (_entry(rule_id="PATIENT-DATA", path="src/x.py", location="42"),)
        assert match(baseline, "PATIENT-DATA", "src/x.py", "42") == baseline[0]

    def test_inactive_entry_never_suppresses(self) -> None:
        baseline = (_entry(active=False),)
        assert (
            match(
                baseline,
                "FUNC-LENGTH",
                "src/medasist/generation/chain.py",
                "_run_single",
            )
            is None
        )

    def test_path_normalization_on_match(self) -> None:
        baseline = (_entry(),)
        assert match(
            baseline, "FUNC-LENGTH", "src/medasist/generation/chain.py", "_run_single"
        )


class TestFindObsolete:
    def test_no_obsolete_when_all_matched(self) -> None:
        baseline = (_entry(),)
        violations = (_violation(),)
        assert (
            find_obsolete(violations, baseline, ["src/medasist/generation/chain.py"])
            == ()
        )

    def test_fixed_violation_is_obsolete(self) -> None:
        baseline = (_entry(),)
        assert (
            find_obsolete((), baseline, ["src/medasist/generation/chain.py"])
            == baseline
        )

    def test_renamed_symbol_is_obsolete(self) -> None:
        baseline = (_entry(location="_run_single"),)
        violations = (_violation(symbol="_run_single_renamed"),)
        assert (
            find_obsolete(violations, baseline, ["src/medasist/generation/chain.py"])
            == baseline
        )

    def test_deleted_file_is_obsolete(self) -> None:
        baseline = (_entry(),)
        assert find_obsolete((), baseline, []) == baseline

    def test_inactive_entries_never_obsolete(self) -> None:
        baseline = (_entry(active=False),)
        assert find_obsolete((), baseline, []) == ()

    def test_line_based_location(self) -> None:
        baseline = (_entry(rule_id="PATIENT-DATA", path="src/x.py", location="42"),)
        violations = (
            _violation(rule_id="PATIENT-DATA", path="src/x.py", line=42, symbol=None),
        )
        assert find_obsolete(violations, baseline, ["src/x.py"]) == ()
