from __future__ import annotations

from pathlib import Path

from medasist.policies.rules import check_no_print

_ROOT = Path(".")


class TestNoPrint:
    def test_print_in_module_is_violation(self) -> None:
        text = 'print("olá")\n'
        violations = check_no_print(text, "src/foo.py", _ROOT)
        assert len(violations) == 1
        assert violations[0].rule_id == "NO-PRINT"
        assert violations[0].symbol == "<module>"

    def test_print_inside_function_uses_function_symbol(self) -> None:
        text = 'def run() -> None:\n    print("x")\n'
        violations = check_no_print(text, "src/foo.py", _ROOT)
        assert len(violations) == 1
        assert violations[0].symbol == "run"

    def test_print_inside_class_method_uses_method_symbol(self) -> None:
        text = 'class Reporter:\n    def emit(self) -> None:\n        print("x")\n'
        violations = check_no_print(text, "src/foo.py", _ROOT)
        assert len(violations) == 1
        assert violations[0].symbol == "emit"

    def test_stdout_write_is_not_flagged(self) -> None:
        text = 'import sys\nsys.stdout.write("relatório")\n'
        assert check_no_print(text, "scripts/policy_check.py", _ROOT) == []

    def test_logger_call_is_not_flagged(self) -> None:
        text = 'logger.info("mensagem %s", value)\n'
        assert check_no_print(text, "src/foo.py", _ROOT) == []

    def test_multiple_prints_each_violation(self) -> None:
        text = 'def f() -> None:\n    print("a")\n    print("b")\n'
        violations = check_no_print(text, "src/foo.py", _ROOT)
        assert len(violations) == 2

    def test_method_named_print_is_not_flagged(self) -> None:
        text = 'obj.print("x")\n'
        assert check_no_print(text, "src/foo.py", _ROOT) == []

    def test_print_in_scripts_also_violates(self) -> None:
        text = 'print("x")\n'
        violations = check_no_print(text, "scripts/some_tool.py", _ROOT)
        assert len(violations) == 1