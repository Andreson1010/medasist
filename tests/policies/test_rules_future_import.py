from __future__ import annotations

from pathlib import Path

from medasist.policies.rules import check_future_import

_ROOT = Path(".")


class TestFutureImport:
    def test_first_statement_is_future_import(self) -> None:
        text = 'from __future__ import annotations\n\nimport os\n'
        assert check_future_import(text, "src/foo.py", _ROOT) == []

    def test_docstring_before_future_import_is_legal(self) -> None:
        text = (
            '"""Módulo de exemplo.\n\nMais detalhes.\n"""\n\n'
            "from __future__ import annotations\n\nimport os\n"
        )
        assert check_future_import(text, "src/foo.py", _ROOT) == []

    def test_comments_before_future_import_are_ignored(self) -> None:
        text = "# comentário\n# outro\nfrom __future__ import annotations\n"
        assert check_future_import(text, "src/foo.py", _ROOT) == []

    def test_future_import_absent_is_violation(self) -> None:
        text = "import os\n\nx = 1\n"
        violations = check_future_import(text, "src/foo.py", _ROOT)
        assert len(violations) == 1
        assert violations[0].rule_id == "FUTURE-IMPORT"
        assert violations[0].path == "src/foo.py"
        assert violations[0].line == 1

    def test_executable_code_before_future_import_is_violation(self) -> None:
        text = "import os\nfrom __future__ import annotations\n"
        violations = check_future_import(text, "src/foo.py", _ROOT)
        assert len(violations) == 1

    def test_empty_file_is_exempt(self) -> None:
        assert check_future_import("", "src/foo.py", _ROOT) == []

    def test_comment_only_file_is_exempt(self) -> None:
        assert check_future_import("# só comentários\n", "src/foo.py", _ROOT) == []

    def test_only_docstring_file_is_exempt(self) -> None:
        text = '"""Apenas docstring."""\n'
        assert check_future_import(text, "src/foo.py", _ROOT) == []

    def test_empty_init_py_is_exempt(self) -> None:
        assert check_future_import("", "src/medasist/pkg/__init__.py", _ROOT) == []

    def test_comment_only_init_py_is_exempt(self) -> None:
        assert check_future_import("# apenas comentário\n", "src/medasist/pkg/__init__.py", _ROOT) == []

    def test_future_import_with_other_names_is_legal(self) -> None:
        text = "from __future__ import annotations, division\n"
        assert check_future_import(text, "src/foo.py", _ROOT) == []

    def test_violation_uses_first_executable_line(self) -> None:
        text = '"""Doc."""\nimport os\n'
        violations = check_future_import(text, "src/foo.py", _ROOT)
        assert violations[0].line == 2