"""Regras estáticas de política do MedAssist (convenções AGENTS.md + segurança).

Cada regra é uma função pura ``check(text, path, root) -> list[PolicyViolation]``
sem IO e sem estado global, testável isoladamente. ``RULE_REGISTRY`` é a fonte
única de regras usada pelo scanner para relatórios e geração de baseline.

As regras estruturais (docstring e limites 50/4/800) valem para código de
``src/``; arquivos de teste e scripts não são exigidos a ter docstrings nem a
respeitar os limites físicos (a convenção do AGENTS.md é de código-fonte).
"""

from __future__ import annotations

import ast
import logging
import re
from collections.abc import Callable
from datetime import date
from pathlib import Path

from medasist.policies.report import PolicyViolation

logger = logging.getLogger(__name__)

RuleChecker = Callable[..., list[PolicyViolation]]

_PATH_EXTENSIONS: tuple[str, ...] = (
    ".pdf",
    ".json",
    ".toml",
    ".md",
    ".txt",
    ".csv",
    ".log",
    ".py",
    ".lock",
    ".env",
    ".example",
)

_PATH_NAME_RE = re.compile(r"(path|file|dir|output)")

_DEFAULT_PATIENT_ALLOWLIST = frozenset(
    {
        "zolatril",
        "alphazol",
        "betazol",
        "gammacol",
        "amoxicilina",
        "ibuprofeno",
        "omeprazol",
        "dipirona",
        "paracetamol",
        "insulina",
        "wrong-key",
    }
)

_PATIENT_DATA_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "CPF",
        re.compile(r"(?<![A-Za-z0-9])\d{3}\.?\d{3}\.?\d{3}-?\d{2}(?![A-Za-z0-9])"),
    ),
    (
        "RG",
        re.compile(
            r"(?<![A-Za-z0-9])(?:\d{1,2}\.\d{3}\.\d{3}-?[0-9A-Za-z]"
            r"|\d{7,8}(?:-[0-9A-Za-z]|[A-Za-z]))(?![A-Za-z0-9])"
        ),
    ),
    ("SUS", re.compile(r"(?<![A-Za-z0-9])\d{15}(?![A-Za-z0-9])")),
    (
        "TELEFONE",
        re.compile(
            r"(?<![A-Za-z0-9])(?:\([1-9]\d\)|[1-9]\d)[ ]?\d{4,5}-?\d{4}"
            r"(?![A-Za-z0-9])"
        ),
    ),
    (
        "EMAIL",
        re.compile(
            r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\."
            r"[A-Za-z]{2,}(?![A-Za-z0-9])"
        ),
    ),
    ("DATA-NASCIMENTO", re.compile(r"\b\d{2}/\d{2}/\d{4}\b")),
)

_MAX_FUNC_LINES = 50
_MAX_NESTING_DEPTH = 4
_MAX_FILE_LINES = 800

_COMPOUND_TYPES: tuple[type[ast.AST], ...] = (
    ast.If,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.Try,
    ast.With,
    ast.AsyncWith,
    ast.Match,
)


def _violation(
    rule_id: str,
    path: str,
    line: int,
    message: str,
    symbol: str | None = None,
) -> PolicyViolation:
    """Cria uma violação de política com os campos padrão."""
    return PolicyViolation(
        rule_id=rule_id,
        path=path,
        line=line,
        message=message,
        symbol=symbol,
    )


def _parent_map(tree: ast.AST) -> dict[int, ast.AST]:
    """Constrói o mapa de nó → pai para navegação contextual no AST."""
    parents: dict[int, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[id(child)] = node
    return parents


def _is_docstring_stmt(node: ast.stmt) -> bool:
    """Indica se um statement é uma docstring (Expr de string constante)."""
    return (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    )


def _docstring_value_ids(tree: ast.AST) -> set[int]:
    """Retorna os ids dos nós de string que são docstrings de módulo/classe/função."""
    ids: set[int] = set()
    for node in ast.walk(tree):
        if (
            isinstance(
                node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef
            )
            and node.body
        ):
            first = node.body[0]
            if _is_docstring_stmt(first):
                ids.add(id(first.value))
    return ids


def _first_executable(module: ast.Module) -> ast.stmt | None:
    """Retorna o primeiro statement executável do módulo (após docstring)."""
    if not module.body:
        return None
    start = 1 if _is_docstring_stmt(module.body[0]) else 0
    if start >= len(module.body):
        return None
    return module.body[start]


def _call_name(func: ast.AST) -> str | None:
    """Retorna o nome de um nó de função em uma chamada (Name ou Attribute)."""
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _is_regex_call(call: ast.Call) -> bool:
    """Indica se uma chamada é do módulo ``re`` (compile/search/sub/...)."""
    return (
        isinstance(call.func, ast.Attribute)
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id == "re"
        and call.func.attr
        in (
            "compile",
            "search",
            "match",
            "sub",
            "findall",
            "finditer",
            "split",
            "fullmatch",
            "escape",
        )
    )


def _is_format_placeholder(value: str) -> bool:
    """Indica se uma string contém placeholder de formatação (``%s``/``{}``)."""
    if re.search(r"%[sdifr%]", value):
        return True
    return "{}" in value or bool(re.search(r"\{[^}]*\}", value))


def _looks_like_windows_drive(value: str) -> bool:
    """Indica se a string começa com um drive Windows (``C:\\...``)."""
    return bool(re.match(r"^[A-Za-z]:[\\/]", value))


def _target_name(target: ast.AST) -> str | None:
    """Retorna o nome de um alvo de atribuição simples (``Name``)."""
    if isinstance(target, ast.Name):
        return target.id
    return None


def check_future_import(
    text: str, path: str, root: Path, allowlist: frozenset[str] | None = None
) -> list[PolicyViolation]:
    """Valida a convenção ``from __future__ import annotations``.

    A primeira instrução executável do módulo (após docstring opcional) deve
    ser o future import. Arquivos vazios, só-docstring ou ``__init__.py`` sem
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
            "como primeira instrução executável (docstring pode preceder)",
        )
    ]


def _is_open_first_arg(node: ast.Constant, parent: ast.AST | None) -> bool:
    """Indica se a string é o primeiro argumento posicional do builtin ``open``."""
    return (
        isinstance(parent, ast.Call)
        and isinstance(parent.func, ast.Name)
        and parent.func.id == "open"
        and parent.args
        and parent.args[0] is node
    )


def _is_path_named_assign(node: ast.Constant, parent: ast.AST | None) -> bool:
    """Indica se a string é valor de atribuição a nome de caminho sugestivo.

    O alvo não pode ser privado (``_``-prefixado) e deve conter ``path``,
    ``file``, ``dir`` ou ``output``; o valor deve ter separador ou extensão de
    arquivo conhecida.
    """
    if not isinstance(parent, ast.Assign | ast.AnnAssign):
        return False
    target = parent.targets[0] if isinstance(parent, ast.Assign) else parent.target
    name = _target_name(target)
    if not name or name.startswith("_") or not _PATH_NAME_RE.search(name):
        return False
    value = node.value
    return "/" in value or "\\" in value or value.lower().endswith(_PATH_EXTENSIONS)


def check_pathlib(
    text: str, path: str, root: Path, allowlist: frozenset[str] | None = None
) -> list[PolicyViolation]:
    """Valida a convenção de usar ``pathlib.Path`` em vez de strings brutas.

    Sinaliza strings usadas como caminho: primeiro argumento do builtin
    ``open(...)``, prefixo de drive Windows ou atribuição a variável de nome
    sugestivo (path/file/dir/output) com separador ou extensão de arquivo.
    Strings em ``Path(...)``/``str(...)``, docstrings, URLs, placeholders e
    padrões regex são ignoradas.
    """
    tree = ast.parse(text)
    parents = _parent_map(tree)
    docstrings = _docstring_value_ids(tree)
    violations: list[PolicyViolation] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            continue
        if id(node) in docstrings:
            continue
        value = node.value
        parent = parents.get(id(node))
        if isinstance(parent, ast.Call):
            func_name = _call_name(parent.func)
            if func_name in ("Path", "str") or _is_regex_call(parent):
                continue
        if "://" in value or _is_format_placeholder(value):
            continue
        if _looks_like_windows_drive(value):
            violations.append(_pathlib_violation(path, node))
            continue
        if _is_open_first_arg(node, parent):
            violations.append(_pathlib_violation(path, node))
            continue
        if _is_path_named_assign(node, parent):
            violations.append(_pathlib_violation(path, node))
    return violations


def _pathlib_violation(path: str, node: ast.Constant) -> PolicyViolation:
    """Cria a violação de PATHLIB para uma string candidata a caminho."""
    return _violation(
        "PATHLIB",
        path,
        node.lineno,
        "string usada como caminho bruto; preferir pathlib.Path",
    )


def _enclosing_symbol(node: ast.AST, parents: dict[int, ast.AST]) -> str:
    """Retorna o símbolo que envolve um nó (função/classe ou ``<module>``)."""
    current: ast.AST | None = node
    while current is not None:
        current = parents.get(id(current))
        if isinstance(current, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            return current.name
    return "<module>"


def check_no_print(
    text: str, path: str, root: Path, allowlist: frozenset[str] | None = None
) -> list[PolicyViolation]:
    """Valida a proibição de ``print()`` (usar logger/sys.stdout).

    Toda chamada ``print(...)`` é violação; a localização simbólica
    (função/classe envolvente ou ``<module>``) permite a suppressão por
    baseline/allowlist de carve-out de stdout de CLI. ``sys.stdout.write`` e
    ``logger.*`` não são flaggados.

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
        Violações da regra ``NO-PRINT``, se houver.
    """
    tree = ast.parse(text)
    parents = _parent_map(tree)
    violations: list[PolicyViolation] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name) and node.func.id == "print":
            symbol = _enclosing_symbol(node, parents)
            violations.append(
                _violation(
                    "NO-PRINT",
                    path,
                    node.lineno,
                    "uso de print() proibido; usar logging.getLogger e logger.*",
                    symbol=symbol,
                )
            )
    return violations


def check_logger(
    text: str, path: str, root: Path, allowlist: frozenset[str] | None = None
) -> list[PolicyViolation]:
    """Valida a declaração de logger em módulos de ``src/``.

    Módulos sob ``src/`` (exceto ``__init__.py``) com conteúdo executável
    devem declarar ``logger = logging.getLogger(__name__)`` no nível do módulo.

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
        Violações da regra ``LOGGER``, se houver.
    """
    if not path.startswith("src/") or path.endswith("__init__.py"):
        return []
    module = ast.parse(text)
    if _first_executable(module) is None:
        return []
    for stmt in module.body:
        if isinstance(stmt, ast.Assign | ast.AnnAssign):
            targets = stmt.targets if isinstance(stmt, ast.Assign) else [stmt.target]
            if not any(isinstance(t, ast.Name) and t.id == "logger" for t in targets):
                continue
            if (
                isinstance(stmt.value, ast.Call)
                and _call_name(stmt.value.func) == "getLogger"
            ):
                return []
    return [
        _violation(
            "LOGGER",
            path,
            1,
            "módulo em src/ deve declarar `logger = logging.getLogger(__name__)`",
        )
    ]


def _check_symbol_docstring(
    node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
    path: str,
    violations: list[PolicyViolation],
) -> None:
    """Valida a presença de docstring em um símbolo público.

    Símbolos privados (``_``-prefixados) e dunders são isentos. Para classes
    públicas, também valida os métodos públicos.
    """
    if node.name.startswith("_"):
        return
    if not (node.body and _is_docstring_stmt(node.body[0])):
        violations.append(
            _violation(
                "DOCSTRING",
                path,
                node.lineno,
                f"símbolo público '{node.name}' deve ter docstring",
                symbol=node.name,
            )
        )
    if isinstance(node, ast.ClassDef):
        for stmt in node.body:
            if (
                isinstance(stmt, ast.FunctionDef | ast.AsyncFunctionDef)
                and not stmt.name.startswith("_")
                and not (stmt.body and _is_docstring_stmt(stmt.body[0]))
            ):
                violations.append(
                    _violation(
                        "DOCSTRING",
                        path,
                        stmt.lineno,
                        f"método público '{node.name}.{stmt.name}' "
                        "deve ter docstring",
                        symbol=stmt.name,
                    )
                )


def check_docstring(
    text: str, path: str, root: Path, allowlist: frozenset[str] | None = None
) -> list[PolicyViolation]:
    """Valida docstrings em símbolos públicos de código ``src/``.

    Apenas a presença de docstring é verificada; o estilo NumPy completo é
    melhoria futura. Privados, dunders e arquivos fora de ``src/`` são isentos.

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
        Violações da regra ``DOCSTRING``, se houver.
    """
    if not path.startswith("src/"):
        return []
    tree = ast.parse(text)
    violations: list[PolicyViolation] = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            _check_symbol_docstring(node, path, violations)
    return violations


def _block_children(node: ast.AST) -> list[ast.AST]:
    """Retorna os statements dos corpos de bloco de um statement composto."""
    if isinstance(node, ast.If | ast.For | ast.AsyncFor | ast.While):
        return [*node.body, *node.orelse]
    if isinstance(node, ast.Try):
        handlers = [stmt for handler in node.handlers for stmt in handler.body]
        return [*node.body, *handlers, *node.orelse, *node.finalbody]
    if isinstance(node, ast.With | ast.AsyncWith):
        return list(node.body)
    if isinstance(node, ast.Match):
        return [stmt for case in node.cases for stmt in case.body]
    return []


def _nesting_depth(node: ast.AST, depth: int) -> int:
    """Calcula a profundidade máxima de blocos compostos a partir de ``node``.

    Cada bloco (body/orelse/except/finally/case) de um statement composto
    incrementa um nível de aninhamento.
    """
    if isinstance(node, _COMPOUND_TYPES):
        child_depth = depth + 1
        max_depth = child_depth
        for child in _block_children(node):
            max_depth = max(max_depth, _nesting_depth(child, child_depth))
        return max_depth
    max_depth = depth
    for child in ast.iter_child_nodes(node):
        max_depth = max(max_depth, _nesting_depth(child, depth))
    return max_depth


def _check_function_complexity(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    path: str,
    violations: list[PolicyViolation],
) -> None:
    """Valida os limites de linhas e aninhamento de uma função (50/4)."""
    span = node.end_lineno - node.lineno + 1
    if span > _MAX_FUNC_LINES:
        violations.append(
            _violation(
                "FUNC-LENGTH",
                path,
                node.lineno,
                f"função '{node.name}' com {span} linhas (limite {_MAX_FUNC_LINES})",
                symbol=node.name,
            )
        )
    depth = _nesting_depth(node, 0)
    if depth > _MAX_NESTING_DEPTH:
        violations.append(
            _violation(
                "NESTING-DEPTH",
                path,
                node.lineno,
                f"aninhamento {depth} > {_MAX_NESTING_DEPTH} em '{node.name}'",
                symbol=node.name,
            )
        )


def check_complexity(
    text: str, path: str, root: Path, allowlist: frozenset[str] | None = None
) -> list[PolicyViolation]:
    """Valida os limites de código 50/4/800 em arquivos de ``src/``.

    Sub-regras: ``FUNC-LENGTH`` (span físico do ``def``, limite 50 inclusivo),
    ``NESTING-DEPTH`` (aninhamento de blocos compostos, limite 4 inclusivo) e
    ``FILE-LENGTH`` (linhas físicas do arquivo, limite 800 inclusivo). A
    contagem usa ``splitlines``, normalizando CRLF do Windows.

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
        Violações das regras de complexidade, se houver.
    """
    if not path.startswith("src/"):
        return []
    tree = ast.parse(text)
    violations: list[PolicyViolation] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            _check_function_complexity(node, path, violations)
    num_lines = len(text.splitlines())
    if num_lines > _MAX_FILE_LINES:
        violations.append(
            _violation(
                "FILE-LENGTH",
                path,
                num_lines,
                f"arquivo com {num_lines} linhas (limite {_MAX_FILE_LINES})",
                symbol="<file>",
            )
        )
    return violations


def _patient_match(line: str, current_year: int) -> tuple[str, str] | None:
    """Retorna (padrão, amostra) do primeiro dado de paciente na linha.

    A data de nascimento só casa com ano no range etário 1920..ano atual.
    """
    for name, pattern in _PATIENT_DATA_PATTERNS:
        matched = pattern.search(line)
        if matched is None:
            continue
        if name == "DATA-NASCIMENTO":
            year = int(matched.group(0)[-4:])
            if not (1920 <= year <= current_year):
                continue
        return name, matched.group(0)
    return None


def check_patient_data(
    text: str,
    path: str,
    root: Path,
    allowlist: frozenset[str] | None = None,
) -> list[PolicyViolation]:
    """Valida a ausência de dado real de paciente em código/testes/logs.

    Padrões estáticos PT-BR (CPF, RG, cartão SUS, telefone com DDD, e-mail e
    data de nascimento com range etário 1920..ano atual) são aplicados por
    linha. Uma linha que contém qualquer token da allowlist de fixtures
    sintéticas é suprimida.

    Parameters
    ----------
    text : str
        Conteúdo do arquivo.
    path : str
        Caminho POSIX relativo do arquivo.
    root : Path
        Raiz da varredura (não usado por esta regra).
    allowlist : frozenset[str] | None
        Tokens sintéticos adicionais (de ``policies.toml``); o conjunto padrão
        de fixtures é sempre considerado.

    Returns
    -------
    list[PolicyViolation]
        Violações da regra ``PATIENT-DATA``, se houver.
    """
    allow = _DEFAULT_PATIENT_ALLOWLIST | (allowlist or frozenset())
    current_year = date.today().year
    violations: list[PolicyViolation] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if any(token in line.lower() for token in allow):
            continue
        found = _patient_match(line, current_year)
        if found is not None:
            name, sample = found
            violations.append(
                _violation(
                    "PATIENT-DATA",
                    path,
                    lineno,
                    f"possível dado real de paciente ({name}: {sample})",
                )
            )
    return violations


RULE_REGISTRY: tuple[tuple[str, RuleChecker], ...] = (
    ("FUTURE-IMPORT", check_future_import),
    ("PATHLIB", check_pathlib),
    ("NO-PRINT", check_no_print),
    ("LOGGER", check_logger),
    ("DOCSTRING", check_docstring),
    ("COMPLEXITY", check_complexity),
    ("PATIENT-DATA", check_patient_data),
)
