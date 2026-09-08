from __future__ import annotations

from pathlib import Path

from medasist.policies.rules import check_docstring

_ROOT = Path(".")


class TestDocstring:
    def test_public_function_with_docstring_is_legal(self) -> None:
        text = "def run() -> None:\n" '    """Executa algo."""\n' "    return None\n"
        assert check_docstring(text, "src/foo.py", _ROOT) == []

    def test_public_function_without_docstring_is_violation(self) -> None:
        text = "def run() -> None:\n    return None\n"
        violations = check_docstring(text, "src/foo.py", _ROOT)
        assert len(violations) == 1
        assert violations[0].rule_id == "DOCSTRING"
        assert violations[0].symbol == "run"

    def test_private_function_is_exempt(self) -> None:
        text = "def _helper() -> None:\n    return None\n"
        assert check_docstring(text, "src/foo.py", _ROOT) == []

    def test_dunder_method_is_exempt(self) -> None:
        text = (
            "class Foo:\n"
            '    """Classe com docstring."""\n'
            "    def __init__(self) -> None:\n"
            "        self.x = 1\n"
        )
        assert check_docstring(text, "src/foo.py", _ROOT) == []

    def test_public_method_without_docstring_is_violation(self) -> None:
        text = (
            "class Foo:\n"
            '    """Classe com docstring."""\n'
            "    def run(self) -> None:\n"
            "        return None\n"
        )
        violations = check_docstring(text, "src/foo.py", _ROOT)
        assert len(violations) == 1
        assert violations[0].symbol == "Foo.run"

    def test_public_method_with_docstring_is_legal(self) -> None:
        text = (
            "class Foo:\n"
            '    """Classe com docstring."""\n'
            "    def run(self) -> None:\n"
            '        """Executa."""\n'
            "        return None\n"
        )
        assert check_docstring(text, "src/foo.py", _ROOT) == []

    def test_public_class_without_docstring_is_violation(self) -> None:
        text = "class Foo:\n    pass\n"
        violations = check_docstring(text, "src/foo.py", _ROOT)
        assert len(violations) == 1
        assert violations[0].symbol == "Foo"

    def test_private_class_is_exempt(self) -> None:
        text = "class _Internal:\n    pass\n"
        assert check_docstring(text, "src/foo.py", _ROOT) == []

    def test_async_function_is_checked(self) -> None:
        text = "async def fetch() -> None:\n    return None\n"
        violations = check_docstring(text, "src/foo.py", _ROOT)
        assert len(violations) == 1

    def test_empty_init_py_is_exempt(self) -> None:
        assert check_docstring("", "src/medasist/pkg/__init__.py", _ROOT) == []

    def test_non_src_file_is_exempt(self) -> None:
        text = "def test_sem_docstring() -> None:\n    pass\n"
        assert check_docstring(text, "tests/test_foo.py", _ROOT) == []
