from __future__ import annotations

from pathlib import Path

from medasist.policies.rules import check_logger

_ROOT = Path(".")


class TestLogger:
    def test_src_module_with_logger_is_legal(self) -> None:
        text = (
            "from __future__ import annotations\n\n"
            "import logging\n\n"
            "logger = logging.getLogger(__name__)\n"
        )
        assert check_logger(text, "src/medasist/foo.py", _ROOT) == []

    def test_src_module_without_logger_is_violation(self) -> None:
        text = "from __future__ import annotations\n\nX = 1\n"
        violations = check_logger(text, "src/medasist/foo.py", _ROOT)
        assert len(violations) == 1
        assert violations[0].rule_id == "LOGGER"
        assert violations[0].path == "src/medasist/foo.py"

    def test_logger_with_type_annotation_is_legal(self) -> None:
        text = (
            "from __future__ import annotations\n\n"
            "import logging\n\n"
            "logger: logging.Logger = logging.getLogger(__name__)\n"
        )
        assert check_logger(text, "src/medasist/foo.py", _ROOT) == []

    def test_non_src_module_is_exempt(self) -> None:
        text = "import os\nx = 1\n"
        assert check_logger(text, "tests/test_foo.py", _ROOT) == []
        assert check_logger(text, "scripts/tool.py", _ROOT) == []

    def test_empty_init_py_is_exempt(self) -> None:
        assert check_logger("", "src/medasist/pkg/__init__.py", _ROOT) == []

    def test_init_py_with_reexports_is_exempt(self) -> None:
        text = (
            "from __future__ import annotations\n\n"
            "from medasist.pkg.foo import bar\n"
        )
        assert check_logger(text, "src/medasist/pkg/__init__.py", _ROOT) == []

    def test_module_with_only_docstring_is_exempt(self) -> None:
        text = '"""Apenas docstring."""\n'
        assert check_logger(text, "src/medasist/foo.py", _ROOT) == []

    def test_logger_assigned_other_value_is_violation(self) -> None:
        text = "from __future__ import annotations\n\n" "logger = something_else()\n"
        violations = check_logger(text, "src/medasist/foo.py", _ROOT)
        assert len(violations) == 1
