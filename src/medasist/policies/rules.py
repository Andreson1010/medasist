"""Regras estáticas de política do MedAssist (convenções AGENTS.md + segurança).

Cada regra é uma função pura ``check(text, path, root) -> list[PolicyViolation]``
sem IO e sem estado global, testável isoladamente. ``RULE_REGISTRY`` é a fonte
única de regras usada pelo scanner para relatórios e geração de baseline.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Callable

from medasist.policies.report import PolicyViolation

RuleChecker = Callable[..., list[PolicyViolation]]


def _violation(
    rule_id: str,
    path: str,
    line: int,
    message: str,
    symbol: str | None = None,
) -> PolicyViolation:
    """Cria uma violação de política com os campos padrão.

    Parameters
    ----------
    rule_id : str
        Identificador da regra violada.
    path : str
        Caminho POSIX relativo do arquivo.
    line : int
        Linha física (1-based) da violação.
    message : str
        Descrição em PT-BR.
    symbol : str | None
        Símbolo associado, quando aplicável.

    Returns
    -------
    PolicyViolation
        Violação pronta para o relatório.
    """
    return PolicyViolation(
        rule_id=rule_id,
        path=path,
        line=line,
        message=message,
        symbol=symbol,
    )


def _is_docstring_stmt(node: ast.stmt) -> bool:
    """Indica se um statement é uma docstring (Expr de string constante).

    Parameters
    ----------
    node : ast.stmt
        Statement do AST.

    Returns
    -------
    bool
        ``True`` quando o nó é um ``Expr`` cujo valor é string.
    """
    return isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(
        node.value.value, str
    )


def _first_executable(module: ast.Module) -> ast.stmt | None:
    """Retorna o primeiro statement executável do módulo.

    A docstring de módulo (primeiro statement quando é string) pode preceder o
    código executável; comentários não aparecem no AST.

    Parameters
    ----------
    module : ast.Module
        AST do módulo.

    Returns
    -------
    ast.stmt | None
        Primeiro statement não-docstring, ou ``None`` se não houver.
    """
    if not module.body:
        return None
    start = 1 if _is_docstring_stmt(module.body[0]) else 0
    if start >= len(module.body):
        return None
    return module.body[start]


def check_future_import(text: str, path: str, root: Path) -> list[PolicyViolation]:
    """Valida a convenção ``from __future__ import annotations``.

    A primeira linha executável do módulo (após a docstring opcional) deve ser
    o future import. Arquivos vazios, só-docstring ou ``__init__.py`` sem
    conteúdo executável são isentos (OQ-02).

    Parameters
    ----------
    text : str
        Conteúdo do arquivo.
    path : str
        Caminho POSIX relativo do arquivo.
    root : Path
        Raiz da varredura (não usado por esta regra).

    Returns
    -------
    list[PolicyViolation]
        Violações da regra ``FUTURE-IMPORT``, se houver.
    """
    module = ast.parse(text)
    first = _first_executable(module)
    if first is None:
        return []
    if (
        isinstance(first, ast.ImportFrom)
        and first.module == "__future__"
        and any(alias.name == "annotations" for alias in first.names)
    ):
        return []
    return [
        _violation(
            "FUTURE-IMPORT",
            path,
            first.lineno,
            "arquivo deve começar com `from __future__ import annotations` "
            "como primeira instrução executável (docstring de módulo pode preceder)",
        )
    ]