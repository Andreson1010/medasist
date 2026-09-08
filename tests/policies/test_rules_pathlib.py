from __future__ import annotations

from pathlib import Path

from medasist.policies.rules import check_pathlib

_ROOT = Path(".")


class TestPathlib:
    def test_open_with_string_path_is_violation(self) -> None:
        text = 'with open("data/raw/bula.pdf", "rb") as f:\n    pass\n'
        violations = check_pathlib(text, "src/foo.py", _ROOT)
        assert len(violations) == 1
        assert violations[0].rule_id == "PATHLIB"
        assert violations[0].line == 1

    def test_string_with_path_separator_is_violation(self) -> None:
        text = 'path = "C:\\\\x\\\\y"\n'
        violations = check_pathlib(text, "src/foo.py", _ROOT)
        assert len(violations) == 1

    def test_string_with_forward_separator_is_violation(self) -> None:
        text = 'path = "data/raw"\n'
        violations = check_pathlib(text, "src/foo.py", _ROOT)
        assert len(violations) == 1

    def test_string_ending_with_extension_is_violation(self) -> None:
        text = 'filename = "relatorio.json"\n'
        violations = check_pathlib(text, "src/foo.py", _ROOT)
        assert len(violations) == 1

    def test_string_inside_path_call_is_legal(self) -> None:
        text = 'path = Path("data/raw/bula.pdf")\n'
        assert check_pathlib(text, "src/foo.py", _ROOT) == []

    def test_string_inside_str_call_is_legal(self) -> None:
        text = "value = str(path)\n"
        assert check_pathlib(text, "src/foo.py", _ROOT) == []

    def test_module_docstring_with_path_is_ignored(self) -> None:
        text = '"""Módulo com caminho data/raw/bula.pdf e C:\\\\x."""\n'
        assert check_pathlib(text, "src/foo.py", _ROOT) == []

    def test_url_is_ignored(self) -> None:
        text = 'url = "https://exemplo.com/api/v1"\n'
        assert check_pathlib(text, "src/foo.py", _ROOT) == []

    def test_format_placeholder_is_ignored(self) -> None:
        text = 'msg = "arquivo %s enviado"\n'
        assert check_pathlib(text, "src/foo.py", _ROOT) == []

    def test_plain_string_without_path_signal_is_legal(self) -> None:
        text = 'name = "Zolatril"\n'
        assert check_pathlib(text, "src/foo.py", _ROOT) == []

    def test_open_with_pathlib_arg_is_legal(self) -> None:
        text = 'with open(path_obj, "rb") as f:\n    pass\n'
        assert check_pathlib(text, "src/foo.py", _ROOT) == []

    def test_non_string_first_arg_of_open_is_legal(self) -> None:
        text = 'with open(Path("data/x.pdf")) as f:\n    pass\n'
        assert check_pathlib(text, "src/foo.py", _ROOT) == []

    def test_function_docstring_with_path_is_ignored(self) -> None:
        text = (
            "def foo() -> None:\n"
            '    """Lê arquivo em data/raw."""\n'
            "    return None\n"
        )
        assert check_pathlib(text, "src/foo.py", _ROOT) == []

    def test_extension_check_does_not_flag_plain_word(self) -> None:
        text = 'word = "python"\n'
        assert check_pathlib(text, "src/foo.py", _ROOT) == []
