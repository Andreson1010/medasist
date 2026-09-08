"""Acceptance tests do policy-as-code (ACs executáveis).

Cobertura por critério de aceitação (spec REQ-POL):
- AC-01/02: CLI exit 0 em árvore conforme e com baseline cobrindo débito.
- AC-06/14/15: exit 1 com relato; baseline não mascara violação nova; baseline
  obsoleta exige limpeza.
- AC-03/07/20: future import na posição permitida (após docstring), ausente/
  atrasado é violação; vazios/``__init__.py``/privados/dunders isentos.
- AC-08: path string bruta (open/... ) é violação; Path(...)/str(...) ok.
- AC-09/18: print() fora do carve-out é violação; carve-out de stdout de CLI
  em ``scripts/evaluate_rag.py:_print_report`` preservado (o contrato ``capsys``
  de ``tests/scripts/test_evaluate_rag.py`` segue passando na suíte).
- AC-10: logger ausente em ``src/`` é violação.
- AC-11/20: símbolo público sem docstring é violação; privados isentos.
- AC-12/19: limites 50/4/800 inclusivos.
- AC-13: dado real de paciente é violação; fixtures sintéticas não.

ACs de pre-commit/CI/template (AC-04/05/16/17/21/22) são validados
manualmente (pre-commit/CI não rodam em pytest) e por code review.
"""

from __future__ import annotations

from pathlib import Path

from policy_check import main

_FUTURE = "from __future__ import annotations\n"
_LOGGER = "import logging\n\nlogger = logging.getLogger(__name__)\n"

_CLEAN_PY = (
    "from __future__ import annotations\n"
    "\n"
    "import logging\n"
    "\n"
    "logger = logging.getLogger(__name__)\n"
    "\n"
    "def run() -> None:\n"
    '    """Executa algo."""\n'
    "    return None\n"
)

_BAD_PRINT_PY = (
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


def _baseline(path: Path, rule: str, rel: str, location: str) -> None:
    path.write_text(
        "[[entries]]\n"
        f'rule_id = "{rule}"\n'
        f'path = "{rel}"\n'
        f'location = "{location}"\n'
        'reason = "teste"\n',
        encoding="utf-8",
    )


def _cli(tree: Path, *extra: str) -> int:
    return main([str(tree / "src"), *extra])


class TestAc01ConformingTree:
    def test_ac01_conforming_tree_exits_zero(self, tmp_path: Path) -> None:
        _write(tmp_path, "src/ok.py", _CLEAN_PY)
        assert _cli(tmp_path, "--baseline", str(tmp_path / "nao.toml")) == 0


class TestAc02BaselineCoversDebt:
    def test_ac02_baseline_covers_preexisting_debt(self, tmp_path: Path) -> None:
        _write(tmp_path, "src/bad.py", _BAD_PRINT_PY)
        _baseline(tmp_path / "policies.toml", "NO-PRINT", "src/bad.py", "<module>")
        assert _cli(tmp_path, "--baseline", str(tmp_path / "policies.toml")) == 0


class TestAc06ViolationReported:
    def test_ac06_new_violation_exits_one_with_report(
        self, tmp_path: Path, capsys
    ) -> None:
        _write(tmp_path, "src/bad.py", _BAD_PRINT_PY)
        result = _cli(tmp_path, "--baseline", str(tmp_path / "nao.toml"))
        assert result == 1
        captured = capsys.readouterr().out
        assert "NO-PRINT" in captured
        assert "src/bad.py:" in captured


class TestAc03Ac07FutureImport:
    def test_ac03_docstring_before_future_import_is_legal(self, tmp_path: Path) -> None:
        content = '"""Módulo de exemplo."""\n\n' + _FUTURE + _LOGGER + "\nX = 1\n"
        _write(tmp_path, "src/ok.py", content)
        assert _cli(tmp_path, "--baseline", str(tmp_path / "nao.toml")) == 0

    def test_ac07_missing_future_import_exits_one(self, tmp_path: Path, capsys) -> None:
        _write(tmp_path, "src/bad.py", "import os\n\nX = 1\n")
        assert _cli(tmp_path, "--baseline", str(tmp_path / "nao.toml")) == 1
        assert "FUTURE-IMPORT" in capsys.readouterr().out

    def test_ac07_executable_before_future_import_exits_one(
        self, tmp_path: Path
    ) -> None:
        _write(tmp_path, "src/bad.py", "import os\n" + _FUTURE)
        assert _cli(tmp_path, "--baseline", str(tmp_path / "nao.toml")) == 1


class TestAc08Pathlib:
    def test_ac08_raw_path_string_exits_one(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "src/bad.py",
            _FUTURE + _LOGGER + '\npath = "C:\\\\x\\\\y"\n',
        )
        assert _cli(tmp_path, "--baseline", str(tmp_path / "nao.toml")) == 1

    def test_ac08_open_with_string_exits_one(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "src/bad.py",
            _FUTURE + _LOGGER + '\nwith open("data/x.pdf") as f:\n    pass\n',
        )
        assert _cli(tmp_path, "--baseline", str(tmp_path / "nao.toml")) == 1

    def test_ac08_pathlib_usage_is_legal(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "src/ok.py",
            _FUTURE
            + _LOGGER
            + "\nfrom pathlib import Path\n"
            + '\np = Path("data/x.pdf")\n',
        )
        assert _cli(tmp_path, "--baseline", str(tmp_path / "nao.toml")) == 0


class TestAc09Ac18Print:
    def test_ac09_print_in_src_exits_one(self, tmp_path: Path) -> None:
        _write(tmp_path, "src/bad.py", _BAD_PRINT_PY)
        assert _cli(tmp_path, "--baseline", str(tmp_path / "nao.toml")) == 1

    def test_ac18_print_carveout_preserved_with_baseline(self, tmp_path: Path) -> None:
        # Mesmo carve-out de scripts/evaluate_rag.py:_print_report: print() em
        # função allowlistada por baseline não falha o gate.
        _write(
            tmp_path,
            "scripts/tool.py",
            _FUTURE + "\ndef _print_report() -> None:\n    print('relatório')\n",
        )
        baseline = tmp_path / "policies.toml"
        _baseline(baseline, "NO-PRINT", "scripts/tool.py", "_print_report")
        result = main(
            [
                str(tmp_path / "scripts"),
                "--baseline",
                str(baseline),
            ]
        )
        assert result == 0


class TestAc10Logger:
    def test_ac10_missing_logger_in_src_exits_one(self, tmp_path: Path, capsys) -> None:
        _write(tmp_path, "src/bad.py", _FUTURE + "\nX = 1\n")
        assert _cli(tmp_path, "--baseline", str(tmp_path / "nao.toml")) == 1
        assert "LOGGER" in capsys.readouterr().out

    def test_ac10_logger_present_is_legal(self, tmp_path: Path) -> None:
        _write(tmp_path, "src/ok.py", _CLEAN_PY)
        assert _cli(tmp_path, "--baseline", str(tmp_path / "nao.toml")) == 0


class TestAc11Ac20Docstring:
    def test_ac11_public_without_docstring_exits_one(
        self, tmp_path: Path, capsys
    ) -> None:
        content = _FUTURE + _LOGGER + "\ndef run() -> None:\n    return None\n"
        _write(tmp_path, "src/bad.py", content)
        assert _cli(tmp_path, "--baseline", str(tmp_path / "nao.toml")) == 1
        assert "DOCSTRING" in capsys.readouterr().out

    def test_ac20_private_without_docstring_is_exempt(self, tmp_path: Path) -> None:
        content = _FUTURE + _LOGGER + "\ndef _helper() -> None:\n    return None\n"
        _write(tmp_path, "src/ok.py", content)
        assert _cli(tmp_path, "--baseline", str(tmp_path / "nao.toml")) == 0

    def test_ac20_empty_init_py_is_exempt(self, tmp_path: Path) -> None:
        _write(tmp_path, "src/pkg/__init__.py", "")
        assert _cli(tmp_path, "--baseline", str(tmp_path / "nao.toml")) == 0


class TestAc12Ac19Complexity:
    def _func_source(self, n_statements: int) -> str:
        body = "\n".join(f"    x{i} = {i}" for i in range(n_statements))
        return (
            _FUTURE
            + _LOGGER
            + '\ndef run() -> None:\n    """Executa."""\n'
            + body
            + "\n"
        )

    def _file_source(self, n_lines: int) -> str:
        prefix = _FUTURE + _LOGGER + "\n"
        assignments = n_lines - 5
        return prefix + "\n".join(f"x{i} = {i}" for i in range(assignments)) + "\n"

    def test_ac12_51_line_function_exits_one(self, tmp_path: Path) -> None:
        _write(tmp_path, "src/bad.py", self._func_source(49))
        assert _cli(tmp_path, "--baseline", str(tmp_path / "nao.toml")) == 1

    def test_ac19_50_line_function_is_legal(self, tmp_path: Path) -> None:
        _write(tmp_path, "src/ok.py", self._func_source(48))
        assert _cli(tmp_path, "--baseline", str(tmp_path / "nao.toml")) == 0

    def test_ac12_nesting_five_exits_one(self, tmp_path: Path) -> None:
        content = (
            _FUTURE + _LOGGER + "\ndef run() -> None:\n"
            '    """Executa."""\n'
            "    if a:\n"
            "        for x in y:\n"
            "            while z:\n"
            "                try:\n"
            "                    if b:\n"
            "                        return 1\n"
            "                except ValueError:\n"
            "                    return 2\n"
        )
        _write(tmp_path, "src/bad.py", content)
        assert _cli(tmp_path, "--baseline", str(tmp_path / "nao.toml")) == 1

    def test_ac19_nesting_four_is_legal(self, tmp_path: Path) -> None:
        content = (
            _FUTURE + _LOGGER + "\ndef run() -> None:\n"
            '    """Executa."""\n'
            "    if a:\n"
            "        for x in y:\n"
            "            while z:\n"
            "                if b:\n"
            "                    return 1\n"
        )
        _write(tmp_path, "src/ok.py", content)
        assert _cli(tmp_path, "--baseline", str(tmp_path / "nao.toml")) == 0

    def test_ac12_801_line_file_exits_one(self, tmp_path: Path) -> None:
        _write(tmp_path, "src/bad.py", self._file_source(801))
        assert _cli(tmp_path, "--baseline", str(tmp_path / "nao.toml")) == 1

    def test_ac19_800_line_file_is_legal(self, tmp_path: Path) -> None:
        _write(tmp_path, "src/ok.py", self._file_source(800))
        assert _cli(tmp_path, "--baseline", str(tmp_path / "nao.toml")) == 0


class TestAc13PatientData:
    def test_ac13_patient_cpf_exits_one(self, tmp_path: Path) -> None:
        _write(tmp_path, "src/bad.py", f'{_FUTURE}{_LOGGER}\ncpf = "123.456.789-00"\n')
        assert _cli(tmp_path, "--baseline", str(tmp_path / "nao.toml")) == 1

    def test_ac13_synthetic_tokens_are_legal(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "src/ok.py",
            f'{_FUTURE}{_LOGGER}\nmedicamento = "Zolatril 500mg"\n',
        )
        assert _cli(tmp_path, "--baseline", str(tmp_path / "nao.toml")) == 0


class TestAc14Ac15BaselineLifecycle:
    def test_ac14_new_violation_with_baseline_exits_one(self, tmp_path: Path) -> None:
        # A baseline cobre o print de bad.py, mas other.py tem print novo.
        _write(tmp_path, "src/bad.py", _BAD_PRINT_PY)
        _write(tmp_path, "src/other.py", _BAD_PRINT_PY)
        _baseline(tmp_path / "policies.toml", "NO-PRINT", "src/bad.py", "<module>")
        assert _cli(tmp_path, "--baseline", str(tmp_path / "policies.toml")) == 1

    def test_ac15_obsolete_baseline_exits_one(self, tmp_path: Path, capsys) -> None:
        _write(tmp_path, "src/ok.py", _CLEAN_PY)
        _baseline(tmp_path / "policies.toml", "NO-PRINT", "src/ok.py", "<module>")
        result = _cli(tmp_path, "--baseline", str(tmp_path / "policies.toml"))
        assert result == 1
        assert "obsoleta" in capsys.readouterr().out.lower()
