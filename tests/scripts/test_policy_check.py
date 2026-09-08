from __future__ import annotations

from pathlib import Path

from policy_check import _render, main, parse_args

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


def _write(tree: Path, rel: str, content: str) -> None:
    target = tree / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


class TestParseArgs:
    def test_defaults(self) -> None:
        args = parse_args([])
        assert args.paths == ["src", "tests", "scripts"]
        assert args.exclude is None
        assert args.baseline == Path("policies.toml")
        assert args.baseline_generate is None

    def test_explicit_paths_and_excludes(self) -> None:
        args = parse_args(
            ["custom", "--exclude", "vendors", "temp", "--baseline", "b.toml"]
        )
        assert args.paths == ["custom"]
        assert args.exclude == ["vendors", "temp"]
        assert args.baseline == Path("b.toml")

    def test_baseline_generate_flag(self) -> None:
        args = parse_args(["--baseline-generate"])
        assert args.baseline_generate is True

    def test_baseline_generate_with_path(self) -> None:
        args = parse_args(["--baseline-generate", "out.toml"])
        assert args.baseline_generate == "out.toml"


class TestMainExitCodes:
    def test_conforming_tree_exits_zero(self, tmp_path: Path, capsys) -> None:
        src = tmp_path / "src"
        _write(src, "ok.py", _CLEAN_PY)
        assert main([str(src), "--baseline", str(tmp_path / "nao.toml")]) == 0
        captured = capsys.readouterr().out
        assert "0" in captured

    def test_new_violation_exits_one_and_reports(self, tmp_path: Path, capsys) -> None:
        src = tmp_path / "src"
        _write(src, "bad.py", _BAD_PY)
        result = main([str(src), "--baseline", str(tmp_path / "nao.toml")])
        assert result == 1
        captured = capsys.readouterr().out
        assert "NO-PRINT" in captured
        assert "src/bad.py" in captured

    def test_baseline_generate_writes_file(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        _write(src, "bad.py", _BAD_PY)
        out = tmp_path / "gerada.toml"
        assert main([str(src), "--baseline-generate", str(out)]) == 0
        assert out.exists()
        content = out.read_text(encoding="utf-8")
        assert "NO-PRINT" in content
        assert "src/bad.py" in content

    def test_baseline_generate_preserves_allowlist(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        _write(src, "bad.py", _BAD_PY)
        out = tmp_path / "gerada.toml"
        out.write_text(
            '[allowlist.patient_data]\ntokens = ["meufix"]\n',
            encoding="utf-8",
        )
        assert main([str(src), "--baseline-generate", str(out)]) == 0
        content = out.read_text(encoding="utf-8")
        assert '"meufix"' in content

    def test_obsolete_baseline_exits_one(self, tmp_path: Path, capsys) -> None:
        src = tmp_path / "src"
        _write(src, "ok.py", _CLEAN_PY)
        baseline = tmp_path / "policies.toml"
        baseline.write_text(
            "[[entries]]\n"
            'rule_id = "NO-PRINT"\n'
            'path = "src/bad.py"\n'
            'location = "<module>"\n'
            'reason = "teste"\n',
            encoding="utf-8",
        )
        result = main([str(src), "--baseline", str(baseline)])
        assert result == 1
        assert "obsoleta" in capsys.readouterr().out.lower()

    def test_malformed_baseline_exits_one(self, tmp_path: Path, capsys) -> None:
        src = tmp_path / "src"
        _write(src, "ok.py", _CLEAN_PY)
        baseline = tmp_path / "quebrado.toml"
        baseline.write_text("[[entries\n", encoding="utf-8")
        assert main([str(src), "--baseline", str(baseline)]) == 1

    def test_exclude_is_forwarded(self, tmp_path: Path) -> None:
        _write(tmp_path, "vendors/v.py", _BAD_PY)
        _write(tmp_path, "src/ok.py", _CLEAN_PY)
        args = [
            str(tmp_path),
            "--exclude",
            "vendors",
            "--baseline",
            str(tmp_path / "nao.toml"),
        ]
        assert main(args) == 0

    def test_excluded_dir_without_exclude_flag_fails(self, tmp_path: Path) -> None:
        _write(tmp_path, "vendors/v.py", _BAD_PY)
        _write(tmp_path, "src/ok.py", _CLEAN_PY)
        args = [str(tmp_path), "--baseline", str(tmp_path / "nao.toml")]
        assert main(args) == 1

    def test_patient_data_violation_prints_not_baselinable(
        self, tmp_path: Path, capsys
    ) -> None:
        src = tmp_path / "src"
        cpf = "123" + ".456" + ".789-00"
        _write(src, "bad.py", f'{_CLEAN_PY}\ncpf = "{cpf}"\n')
        result = main([str(src), "--baseline", str(tmp_path / "nao.toml")])
        assert result == 1
        captured = capsys.readouterr().out
        assert "não é baselinável" in captured


class TestRender:
    def test_render_mentions_rule_and_file(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        _write(src, "bad.py", _BAD_PY)
        from medasist.policies import run_scan

        report = run_scan([src], baseline_path=tmp_path / "nao.toml")
        rendered = _render(report)
        assert "NO-PRINT" in rendered
        assert "src/bad.py" in rendered
        assert "FALHA" in rendered

    def test_render_ok_state(self, tmp_path: Path) -> None:
        src = tmp_path / "src"
        _write(src, "ok.py", _CLEAN_PY)
        from medasist.policies import run_scan

        report = run_scan([src], baseline_path=tmp_path / "nao.toml")
        rendered = _render(report)
        assert "OK" in rendered
