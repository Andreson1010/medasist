from __future__ import annotations

from pathlib import Path

from medasist.policies.rules import check_complexity

_ROOT = Path(".")


def _func(lines_body: list[str]) -> str:
    body = "\n".join(lines_body)
    return f"def run() -> None:\n{body}\n"


class TestFuncLength:
    def test_function_with_exactly_50_lines_is_legal(self) -> None:
        body = [f"    x{i} = {i}" for i in range(49)]
        text = _func(body)
        # def(1) + 49 statements = 50 linhas físicas
        assert len(text.splitlines()) == 50
        assert check_complexity(text, "src/foo.py", _ROOT) == []

    def test_function_with_51_lines_is_violation(self) -> None:
        body = [f"    x{i} = {i}" for i in range(50)]
        text = _func(body)
        assert len(text.splitlines()) == 51
        violations = check_complexity(text, "src/foo.py", _ROOT)
        assert any(v.rule_id == "FUNC-LENGTH" for v in violations)
        assert any(v.symbol == "run" for v in violations)

    def test_span_includes_nested_def(self) -> None:
        # O span físico de outer inclui o corpo do def aninhado (49 + 2 > 50).
        body = [f"    x{i} = {i}" for i in range(48)]
        text = (
            "def outer() -> None:\n"
            "    def inner() -> None:\n"
            "        return 1\n"
            + "\n".join(body)
            + "\n"
        )
        violations = check_complexity(text, "src/foo.py", _ROOT)
        assert any(v.rule_id == "FUNC-LENGTH" and v.symbol == "outer" for v in violations)


class TestNestingDepth:
    def test_four_levels_is_legal(self) -> None:
        text = (
            "def run() -> None:\n"
            "    if a:\n"
            "        for x in y:\n"
            "            while z:\n"
            "                try:\n"
            "                    return 1\n"
            "                except ValueError:\n"
            "                    return 2\n"
        )
        assert check_complexity(text, "src/foo.py", _ROOT) == []

    def test_five_levels_is_violation(self) -> None:
        text = (
            "def run() -> None:\n"
            "    if a:\n"
            "        for x in y:\n"
            "            while z:\n"
            "                try:\n"
            "                    with open(p) as f:\n"
            "                        return 1\n"
            "                except ValueError:\n"
            "                    return 2\n"
        )
        violations = check_complexity(text, "src/foo.py", _ROOT)
        assert any(v.rule_id == "NESTING-DEPTH" and v.symbol == "run" for v in violations)

    def test_else_and_elif_add_compound_level(self) -> None:
        # elif é um If aninhado no orelse do if: cada bloco composto soma nível.
        text = (
            "def run() -> None:\n"
            "    if a:\n"
            "        pass\n"
            "    elif b:\n"
            "        if c:\n"
            "            for x in y:\n"
            "                while z:\n"
            "                    pass\n"
        )
        violations = check_complexity(text, "src/foo.py", _ROOT)
        assert any(v.rule_id == "NESTING-DEPTH" and v.symbol == "run" for v in violations)

    def test_four_levels_with_else_is_legal(self) -> None:
        text = (
            "def run() -> None:\n"
            "    if a:\n"
            "        for x in y:\n"
            "            while z:\n"
            "                if b:\n"
            "                    return 1\n"
            "                else:\n"
            "                    return 2\n"
        )
        assert check_complexity(text, "src/foo.py", _ROOT) == []


class TestFileLength:
    def test_file_with_exactly_800_lines_is_legal(self) -> None:
        text = "\n".join(f"x{i} = {i}" for i in range(800)) + "\n"
        assert len(text.splitlines()) == 800
        assert check_complexity(text, "src/foo.py", _ROOT) == []

    def test_file_with_801_lines_is_violation(self) -> None:
        text = "\n".join(f"x{i} = {i}" for i in range(801)) + "\n"
        violations = check_complexity(text, "src/foo.py", _ROOT)
        assert any(v.rule_id == "FILE-LENGTH" for v in violations)
        assert any(v.symbol == "<file>" for v in violations)

    def test_crlf_is_normalized(self) -> None:
        text = "\r\n".join(f"x{i} = {i}" for i in range(801)) + "\r\n"
        violations = check_complexity(text, "src/foo.py", _ROOT)
        assert any(v.rule_id == "FILE-LENGTH" for v in violations)

    def test_non_src_file_is_exempt(self) -> None:
        text = "\n".join(f"x{i} = {i}" for i in range(900)) + "\n"
        assert check_complexity(text, "tests/test_foo.py", _ROOT) == []