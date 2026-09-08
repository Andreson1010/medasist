"""Suíte de aceite do verifier do policy-as-code (ACs executáveis, outside-in).

Valida os critérios de aceitação da spec (REQ-POL) pelo contrato real do CLI
``python scripts/policy_check.py`` via subprocess sobre árvores sintéticas em
``tmp_path`` — nunca importando internos do pacote ``medasist.policies``.

Cobertura por critério:
- AC-01: CLI exit 0 em árvore conforme.
- AC-02: baseline cobre débito pré-existente (CLI sobre a árvore real do
  worktree → exit 0).
- AC-03: future import após docstring é legal.
- AC-06: ≥1 violação não baselinada → exit 1 com relato (regra/arquivo/local).
- AC-07: future import ausente ou atrasado → violação FUTURE-IMPORT.
- AC-08: string bruta de path (drive Windows / open) → violação PATHLIB.
- AC-09: print() fora do carve-out → violação NO-PRINT.
- AC-10: módulo em src/ sem logger → violação LOGGER.
- AC-11: símbolo público sem docstring → violação DOCSTRING.
- AC-12: função >50 / aninhamento >4 / arquivo >800 → violações de complexidade.
- AC-13: padrão de dado real de paciente → violação PATIENT-DATA; tokens
  sintéticos (Zolatril) legais.
- AC-14: violação nova não baselinada com baseline presente → exit 1.
- AC-15: entrada de baseline obsoleta → exit 1.
- AC-18: carve-out de print por arquivo+símbolo → exit 0; print em símbolo não
  allowlistado do mesmo arquivo segue violando.
- AC-19: limites inclusivos (50/50, 4/4, 800/800) → exit 0.
- AC-20: vazio/``__init__.py``/privados/dunders isentos.

ACs de pre-commit/CI/template (AC-04/05/16/17/21/22) não rodam em pytest:
validados por inspeção (ver relatório do verifier) e pelo gate completo.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _REPO_ROOT / "scripts" / "policy_check.py"

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

_PRINT_PY = (
    "from __future__ import annotations\n"
    "\n"
    "import logging\n"
    "\n"
    "logger = logging.getLogger(__name__)\n"
    "\n"
    'print("x")\n'
)

# Padrões de paciente montados por concatenação para que esta própria suíte
# (varrida pelo policy checker) não contenha literais completos.
_PATIENT_FIXTURES: dict[str, str] = {
    "cpf": "123.456" + ".789-00",
    "telefone": "(11) 98" + "765-4321",
    "email": "paciente" + "@exemplo.com",
    "nascimento": "15/03" + "/1985",
}


def _write(tree: Path, rel: str, content: str) -> None:
    """Grava um arquivo em uma árvore sintética sob tmp_path."""
    target = tree / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def _write_baseline(tree: Path, rule: str, rel: str, location: str) -> None:
    """Grava uma baseline mínima de uma entrada em policies.toml."""
    (tree / "policies.toml").write_text(
        "[[entries]]\n"
        f'rule_id = "{rule}"\n'
        f'path = "{rel}"\n'
        f'location = "{location}"\n'
        'reason = "verifier"\n',
        encoding="utf-8",
    )


def _run_cli(
    tree: Path, *args: str, cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    """Executa o CLI do policy checker como um usuário real (subprocess)."""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, str(_SCRIPT), *args],
        cwd=cwd or tree,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        check=False,
    )


def _no_baseline(tree: Path) -> tuple[str, ...]:
    """Argumentos apontando para baseline inexistente (nenhuma máscara)."""
    return ("--baseline", str(tree / "nao.toml"))


def _func_source(n_assignments: int) -> str:
    """Constrói um arquivo com função de n atribuições (span 50 para n=48)."""
    body = "\n".join(f"    x{i} = {i}" for i in range(n_assignments))
    return (
        _FUTURE + _LOGGER + '\ndef run() -> None:\n    """Executa."""\n' + body + "\n"
    )


def _file_source(n_lines: int) -> str:
    """Constrói um arquivo com exatamente n_lines linhas físicas."""
    prefix = _FUTURE + _LOGGER + "\n"
    assignments = n_lines - 5
    return prefix + "\n".join(f"x{i} = {i}" for i in range(assignments)) + "\n"


class TestAc01ConformingTree:
    def test_ac01_conforming_tree_exits_zero(self, tmp_path: Path) -> None:
        """AC-01: árvore em conformidade deve resultar em exit 0."""
        _write(tmp_path, "src/ok.py", _CLEAN_PY)
        proc = _run_cli(tmp_path, "src", *_no_baseline(tmp_path))
        assert proc.returncode == 0, proc.stdout + proc.stderr


class TestAc02BaselineCoversDebt:
    def test_ac02_baseline_covers_real_tree_debt(self) -> None:
        """AC-02: baseline atual cobre o débito pré-existente (árvore real)."""
        proc = _run_cli(_REPO_ROOT, "src", "tests", "scripts", cwd=_REPO_ROOT)
        assert proc.returncode == 0, proc.stdout + proc.stderr


class TestAc03Ac07FutureImport:
    def test_ac03_docstring_before_future_import_is_legal(self, tmp_path: Path) -> None:
        """AC-03: docstring de módulo pode preceder o future import."""
        content = '"""Módulo de exemplo."""\n\n' + _FUTURE + _LOGGER + "\nX = 1\n"
        _write(tmp_path, "src/ok.py", content)
        proc = _run_cli(tmp_path, "src", *_no_baseline(tmp_path))
        assert proc.returncode == 0, proc.stdout + proc.stderr

    def test_ac07_missing_future_import_exits_one(self, tmp_path: Path) -> None:
        """AC-07: ausência de future import deve ser violação."""
        _write(tmp_path, "src/bad.py", "import os\n\nX = 1\n")
        proc = _run_cli(tmp_path, "src", *_no_baseline(tmp_path))
        assert proc.returncode == 1
        assert "FUTURE-IMPORT" in proc.stdout

    def test_ac07_executable_before_future_import_exits_one(
        self, tmp_path: Path
    ) -> None:
        """AC-07: código executável antes do future import deve ser violação."""
        _write(tmp_path, "src/bad.py", "import os\n" + _FUTURE)
        proc = _run_cli(tmp_path, "src", *_no_baseline(tmp_path))
        assert proc.returncode == 1
        assert "FUTURE-IMPORT" in proc.stdout


class TestAc06ViolationReported:
    def test_ac06_violation_exits_one_with_report(self, tmp_path: Path) -> None:
        """AC-06: violação não baselinada deve sair com exit 1 e relatar."""
        _write(tmp_path, "src/bad.py", _PRINT_PY)
        proc = _run_cli(tmp_path, "src", *_no_baseline(tmp_path))
        assert proc.returncode == 1
        assert "NO-PRINT" in proc.stdout
        assert "src/bad.py:" in proc.stdout
        assert re.search(r"src/bad\.py:\d+", proc.stdout) is not None


class TestAc08Pathlib:
    def test_ac08_windows_drive_string_exits_one(self, tmp_path: Path) -> None:
        """AC-08: string com drive Windows fora de Path(...) é violação."""
        content = _FUTURE + _LOGGER + '\npath = "C:\\\\x\\\\y"\n'
        _write(tmp_path, "src/bad.py", content)
        proc = _run_cli(tmp_path, "src", *_no_baseline(tmp_path))
        assert proc.returncode == 1
        assert "PATHLIB" in proc.stdout

    def test_ac08_open_with_string_exits_one(self, tmp_path: Path) -> None:
        """AC-08: open(\"...\") com string bruta é violação."""
        content = _FUTURE + _LOGGER + '\nwith open("data/x.pdf") as f:\n    pass\n'
        _write(tmp_path, "src/bad.py", content)
        proc = _run_cli(tmp_path, "src", *_no_baseline(tmp_path))
        assert proc.returncode == 1
        assert "PATHLIB" in proc.stdout

    def test_ac08_pathlib_usage_is_legal(self, tmp_path: Path) -> None:
        """AC-08: uso de Path(...) não deve gerar violação."""
        content = (
            _FUTURE + _LOGGER + '\nfrom pathlib import Path\np = Path("data/x.pdf")\n'
        )
        _write(tmp_path, "src/ok.py", content)
        proc = _run_cli(tmp_path, "src", *_no_baseline(tmp_path))
        assert proc.returncode == 0, proc.stdout + proc.stderr


class TestAc09PrintOutsideCarveout:
    def test_ac09_print_in_src_exits_one(self, tmp_path: Path) -> None:
        """AC-09: print() fora do carve-out deve ser violação."""
        _write(tmp_path, "src/bad.py", _PRINT_PY)
        proc = _run_cli(tmp_path, "src", *_no_baseline(tmp_path))
        assert proc.returncode == 1
        assert "NO-PRINT" in proc.stdout


class TestAc10Logger:
    def test_ac10_module_without_logger_exits_one(self, tmp_path: Path) -> None:
        """AC-10: módulo em src/ sem logger deve ser violação."""
        _write(tmp_path, "src/bad.py", _FUTURE + "\nX = 1\n")
        proc = _run_cli(tmp_path, "src", *_no_baseline(tmp_path))
        assert proc.returncode == 1
        assert "LOGGER" in proc.stdout


class TestAc11Docstring:
    def test_ac11_public_without_docstring_exits_one(self, tmp_path: Path) -> None:
        """AC-11: símbolo público sem docstring deve ser violação."""
        content = _FUTURE + _LOGGER + "\ndef run() -> None:\n    return None\n"
        _write(tmp_path, "src/bad.py", content)
        proc = _run_cli(tmp_path, "src", *_no_baseline(tmp_path))
        assert proc.returncode == 1
        assert "DOCSTRING" in proc.stdout


class TestAc12ComplexityViolations:
    def test_ac12_function_over_50_lines_exits_one(self, tmp_path: Path) -> None:
        """AC-12: função com 51 linhas deve gerar FUNC-LENGTH."""
        _write(tmp_path, "src/bad.py", _func_source(49))
        proc = _run_cli(tmp_path, "src", *_no_baseline(tmp_path))
        assert proc.returncode == 1
        assert "FUNC-LENGTH" in proc.stdout

    def test_ac12_nesting_five_exits_one(self, tmp_path: Path) -> None:
        """AC-12: aninhamento 5 deve gerar NESTING-DEPTH."""
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
        proc = _run_cli(tmp_path, "src", *_no_baseline(tmp_path))
        assert proc.returncode == 1
        assert "NESTING-DEPTH" in proc.stdout

    def test_ac12_file_over_800_lines_exits_one(self, tmp_path: Path) -> None:
        """AC-12: arquivo com 801 linhas deve gerar FILE-LENGTH."""
        _write(tmp_path, "src/bad.py", _file_source(801))
        proc = _run_cli(tmp_path, "src", *_no_baseline(tmp_path))
        assert proc.returncode == 1
        assert "FILE-LENGTH" in proc.stdout


class TestAc13PatientData:
    @pytest.mark.parametrize("kind", sorted(_PATIENT_FIXTURES))
    def test_ac13_patient_data_pattern_exits_one(
        self, tmp_path: Path, kind: str
    ) -> None:
        """AC-13: padrão de dado real de paciente deve ser violação."""
        value = _PATIENT_FIXTURES[kind]
        _write(tmp_path, "src/bad.py", f'{_FUTURE}{_LOGGER}\nregistro = "{value}"\n')
        proc = _run_cli(tmp_path, "src", *_no_baseline(tmp_path))
        assert proc.returncode == 1
        assert "PATIENT-DATA" in proc.stdout

    def test_ac13_synthetic_tokens_are_legal(self, tmp_path: Path) -> None:
        """AC-13: fixtures sintéticas (Zolatril) não devem gerar violação."""
        content = f'{_FUTURE}{_LOGGER}\nmedicamento = "Zolatril 500mg"\n'
        _write(tmp_path, "src/ok.py", content)
        proc = _run_cli(tmp_path, "src", *_no_baseline(tmp_path))
        assert proc.returncode == 0, proc.stdout + proc.stderr


class TestAc14Ac15BaselineLifecycle:
    def test_ac14_new_violation_not_baselined_exits_one(self, tmp_path: Path) -> None:
        """AC-14: violação nova não coberta pela baseline deve falhar."""
        _write(tmp_path, "src/bad.py", _PRINT_PY)
        _write(tmp_path, "src/other.py", _PRINT_PY)
        _write_baseline(tmp_path, "NO-PRINT", "src/bad.py", "<module>")
        proc = _run_cli(tmp_path, "src", "--baseline", str(tmp_path / "policies.toml"))
        assert proc.returncode == 1
        assert "src/other.py" in proc.stdout

    def test_ac15_obsolete_baseline_entry_exits_one(self, tmp_path: Path) -> None:
        """AC-15: entrada de baseline sem violação correspondente deve falhar."""
        _write(tmp_path, "src/ok.py", _CLEAN_PY)
        _write_baseline(tmp_path, "NO-PRINT", "src/ok.py", "<module>")
        proc = _run_cli(tmp_path, "src", "--baseline", str(tmp_path / "policies.toml"))
        assert proc.returncode == 1
        assert "obsoleta" in proc.stdout.lower()


class TestAc18PrintCarveout:
    def test_ac18_print_carveout_by_symbol_exits_zero(self, tmp_path: Path) -> None:
        """AC-18: print allowlistado por arquivo+símbolo não falha o gate."""
        content = _FUTURE + "\ndef _print_report() -> None:\n    print('relatório')\n"
        _write(tmp_path, "scripts/tool.py", content)
        _write_baseline(tmp_path, "NO-PRINT", "scripts/tool.py", "_print_report")
        proc = _run_cli(
            tmp_path, "scripts", "--baseline", str(tmp_path / "policies.toml")
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr

    def test_ac18_carveout_does_not_mask_other_symbol(self, tmp_path: Path) -> None:
        """AC-18: carve-out é por símbolo; print novo no mesmo arquivo viola."""
        content = (
            _FUTURE
            + "\ndef _print_report() -> None:\n    print('relatório')\n"
            + "\ndef outro() -> None:\n    print('fora')\n"
        )
        _write(tmp_path, "scripts/tool.py", content)
        _write_baseline(tmp_path, "NO-PRINT", "scripts/tool.py", "_print_report")
        proc = _run_cli(
            tmp_path, "scripts", "--baseline", str(tmp_path / "policies.toml")
        )
        assert proc.returncode == 1
        assert "outro" in proc.stdout


class TestAc19InclusiveLimits:
    def test_ac19_50_line_function_is_legal(self, tmp_path: Path) -> None:
        """AC-19: função com exatamente 50 linhas não é violação."""
        _write(tmp_path, "src/ok.py", _func_source(48))
        proc = _run_cli(tmp_path, "src", *_no_baseline(tmp_path))
        assert proc.returncode == 0, proc.stdout + proc.stderr

    def test_ac19_nesting_four_is_legal(self, tmp_path: Path) -> None:
        """AC-19: aninhamento exatamente 4 não é violação."""
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
        proc = _run_cli(tmp_path, "src", *_no_baseline(tmp_path))
        assert proc.returncode == 0, proc.stdout + proc.stderr

    def test_ac19_800_line_file_is_legal(self, tmp_path: Path) -> None:
        """AC-19: arquivo com exatamente 800 linhas não é violação."""
        _write(tmp_path, "src/ok.py", _file_source(800))
        proc = _run_cli(tmp_path, "src", *_no_baseline(tmp_path))
        assert proc.returncode == 0, proc.stdout + proc.stderr


class TestAc20Exemptions:
    def test_ac20_empty_file_is_exempt(self, tmp_path: Path) -> None:
        """AC-20: arquivo vazio é isento das regras estruturais."""
        _write(tmp_path, "src/ok.py", "")
        proc = _run_cli(tmp_path, "src", *_no_baseline(tmp_path))
        assert proc.returncode == 0, proc.stdout + proc.stderr

    def test_ac20_empty_init_py_is_exempt(self, tmp_path: Path) -> None:
        """AC-20: __init__.py vazio é isento."""
        _write(tmp_path, "src/pkg/__init__.py", "")
        proc = _run_cli(tmp_path, "src", *_no_baseline(tmp_path))
        assert proc.returncode == 0, proc.stdout + proc.stderr

    def test_ac20_private_symbol_without_docstring_is_exempt(
        self, tmp_path: Path
    ) -> None:
        """AC-20: símbolo privado sem docstring é isento."""
        content = _FUTURE + _LOGGER + "\ndef _helper() -> None:\n    return None\n"
        _write(tmp_path, "src/ok.py", content)
        proc = _run_cli(tmp_path, "src", *_no_baseline(tmp_path))
        assert proc.returncode == 0, proc.stdout + proc.stderr

    def test_ac20_dunder_symbol_without_docstring_is_exempt(
        self, tmp_path: Path
    ) -> None:
        """AC-20: método dunder sem docstring é isento."""
        content = (
            _FUTURE
            + _LOGGER
            + "\nclass C:\n"
            + '    """Classe de teste."""\n'
            + "    def __init__(self) -> None:\n"
            + "        return None\n"
        )
        _write(tmp_path, "src/ok.py", content)
        proc = _run_cli(tmp_path, "src", *_no_baseline(tmp_path))
        assert proc.returncode == 0, proc.stdout + proc.stderr
