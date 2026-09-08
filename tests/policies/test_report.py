from __future__ import annotations

import pytest

from medasist.policies.baseline import BaselineEntry
from medasist.policies.report import PolicyReport, PolicyViolation


def _violation(**overrides) -> PolicyViolation:
    defaults = {
        "rule_id": "FUNC-LENGTH",
        "path": "src/medasist/generation/chain.py",
        "line": 10,
        "message": "função com mais de 50 linhas",
    }
    defaults.update(overrides)
    return PolicyViolation(**defaults)


def _entry(**overrides) -> BaselineEntry:
    defaults = {
        "rule_id": "FUNC-LENGTH",
        "path": "src/medasist/generation/chain.py",
        "location": "_run_single",
        "reason": "débito AD-005",
    }
    defaults.update(overrides)
    return BaselineEntry(**defaults)


class TestPolicyViolation:
    def test_frozen_dataclass(self) -> None:
        violation = _violation()
        assert violation.rule_id == "FUNC-LENGTH"
        assert violation.path == "src/medasist/generation/chain.py"
        assert violation.line == 10
        assert violation.message == "função com mais de 50 linhas"
        assert violation.baseline is False
        assert violation.symbol is None

    def test_defaults_baseline_false_and_symbol_none(self) -> None:
        violation = _violation()
        assert violation.baseline is False
        assert violation.symbol is None

    def test_symbol_explicit(self) -> None:
        violation = _violation(symbol="_run_single", baseline=True)
        assert violation.symbol == "_run_single"
        assert violation.baseline is True

    def test_immutability(self) -> None:
        violation = _violation()
        with pytest.raises(AttributeError):
            violation.rule_id = "NO-PRINT"  # type: ignore[misc]


class TestPolicyReport:
    def test_counts_empty(self) -> None:
        report = PolicyReport(
            violations=(),
            baseline_entries=(),
            obsolete_entries=(),
            files_scanned=0,
            files_skipped=0,
        )
        assert report.new_violations == ()
        assert report.baselined_violations == ()
        assert report.total_violations == 0
        assert report.has_failures is False

    def test_new_violations_filters_baseline_flag(self) -> None:
        new = _violation()
        baselined = _violation(rule_id="NO-PRINT", baseline=True)
        report = PolicyReport(
            violations=(new, baselined),
            baseline_entries=(),
            obsolete_entries=(),
            files_scanned=2,
            files_skipped=0,
        )
        assert report.new_violations == (new,)
        assert report.baselined_violations == (baselined,)
        assert report.total_violations == 2
        assert report.has_failures is True

    def test_has_failures_true_with_obsolete_entries_only(self) -> None:
        report = PolicyReport(
            violations=(),
            baseline_entries=(_entry(),),
            obsolete_entries=(_entry(),),
            files_scanned=1,
            files_skipped=0,
        )
        assert report.new_violations == ()
        assert report.has_failures is True

    def test_has_failures_false_with_baselined_only(self) -> None:
        report = PolicyReport(
            violations=(_violation(baseline=True),),
            baseline_entries=(_entry(),),
            obsolete_entries=(),
            files_scanned=1,
            files_skipped=0,
        )
        assert report.has_failures is False

    def test_files_scanned_and_skipped(self) -> None:
        report = PolicyReport(
            violations=(),
            baseline_entries=(),
            obsolete_entries=(),
            files_scanned=10,
            files_skipped=2,
        )
        assert report.files_scanned == 10
        assert report.files_skipped == 2

    def test_frozen_dataclass(self) -> None:
        report = PolicyReport(
            violations=(),
            baseline_entries=(),
            obsolete_entries=(),
            files_scanned=0,
            files_skipped=0,
        )
        with pytest.raises(AttributeError):
            report.files_scanned = 3  # type: ignore[misc]