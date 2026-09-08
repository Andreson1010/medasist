"""Coleta de arquivos e orquestração das regras de política.

O scanner percorre as raízes padrão (``src``, ``tests``, ``scripts``) com as
exclusões obrigatórias, roda cada regra do ``RULE_REGISTRY`` por arquivo,
aplica a suppressão por baseline e detecta entradas obsoletas (AC-15).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterable, Sequence
from dataclasses import replace
from pathlib import Path

from medasist.policies.baseline import (
    BaselineEntry,
    find_obsolete,
    load_allowlist,
    load_baseline,
    location_of,
    match,
    normalize_path,
)
from medasist.policies.report import PolicyReport, PolicyViolation
from medasist.policies.rules import RULE_REGISTRY

logger = logging.getLogger(__name__)

DEFAULT_ROOTS: tuple[str, ...] = ("src", "tests", "scripts")

DEFAULT_EXCLUDES: tuple[str, ...] = (
    ".opencode",
    ".agents",
    ".git",
    "data",
    "evals",
    "chroma_db",
    "logs",
    "docs",
    "node_modules",
)


def _is_excluded(file: Path, excludes: Iterable[str]) -> bool:
    """Indica se um arquivo contém segmento excluído no caminho.

    Qualquer segmento da lista de exclusões, ``__pycache__`` ou diretório
    oculto (prefixo ``.``) faz o arquivo ser pulado.

    Parameters
    ----------
    file : Path
        Caminho do arquivo candidato.
    excludes : Iterable[str]
        Nomes de segmentos excluídos.

    Returns
    -------
    bool
        ``True`` quando o arquivo deve ser ignorado.
    """
    excluded = set(excludes)
    return any(
        part in excluded or part == "__pycache__" or part.startswith(".")
        for part in file.parts
    )


def collect_files(
    roots: Sequence[Path | str],
    excludes: Iterable[str] | None = None,
) -> list[Path]:
    """Coleta os arquivos ``.py`` sob as raízes, aplicando as exclusões.

    Raízes inexistentes são ignoradas (sem erro). Nunca cruza para fora do
    diretório de cada raiz (``rglob``).

    Parameters
    ----------
    roots : Sequence[Path | str]
        Diretórios a varrer.
    excludes : Iterable[str] | None
        Segmentos adicionais a excluir (padrão: ``DEFAULT_EXCLUDES``).

    Returns
    -------
    list[Path]
        Arquivos ``.py`` coletados.
    """
    excluded = DEFAULT_EXCLUDES if excludes is None else tuple(excludes)
    files: list[Path] = []
    for root in roots:
        root_path = Path(root)
        if not root_path.is_dir():
            continue
        for file in root_path.rglob("*.py"):
            if not _is_excluded(file, excluded):
                files.append(file)
    return files


def _scan_base(roots: Sequence[Path | str]) -> Path:
    """Calcula a raiz comum para relativizar os caminhos dos arquivos.

    Para raízes ``src/tests/scripts`` a base é o diretório de trabalho; para
    uma raiz ``<tmp>/src`` a base é ``<tmp>``, produzindo caminhos POSIX
    estáveis tipo ``src/foo.py``.

    Parameters
    ----------
    roots : Sequence[Path | str]
        Diretórios da varredura.

    Returns
    -------
    Path
        Pai comum das raízes.
    """
    resolved = [Path(root).resolve() for root in roots]
    if not resolved:
        return Path.cwd()
    parents = [path.parent for path in resolved]
    return Path(os.path.commonpath([str(parent) for parent in parents]))


def _read_file(file: Path) -> str:
    """Lê um arquivo preservando quebras de linha (CRLF-safe).

    Parameters
    ----------
    file : Path
        Arquivo a ler.

    Returns
    -------
    str
        Conteúdo do arquivo.
    """
    with file.open(encoding="utf-8", newline="") as handle:
        return handle.read()


def _collect_violations(
    files: Sequence[Path],
    base: Path,
    allowlist: frozenset[str],
) -> tuple[list[PolicyViolation], list[str], int]:
    """Roda as regras sobre os arquivos e devolve violações brutas.

    Parameters
    ----------
    files : Sequence[Path]
        Arquivos coletados.
    base : Path
        Raiz comum para relativizar caminhos.
    allowlist : frozenset[str]
        Tokens sintéticos de paciente carregados da baseline.

    Returns
    -------
    tuple[list[PolicyViolation], list[str], int]
        Violações brutas, caminhos varridos e quantidade de ilegíveis.
    """
    raw_violations: list[PolicyViolation] = []
    scanned: list[str] = []
    skipped = 0
    for file in files:
        rel = normalize_path(file, base)
        try:
            text = _read_file(file)
        except (OSError, UnicodeDecodeError):
            skipped += 1
            logger.warning("Arquivo ilegível pulado: %s", rel)
            continue
        scanned.append(rel)
        for _rule_id, checker in RULE_REGISTRY:
            raw_violations.extend(checker(text, rel, base, allowlist=allowlist))
    return raw_violations, scanned, skipped


def run_scan(
    roots: Sequence[Path | str],
    excludes: Iterable[str] | None = None,
    baseline_path: Path | None = None,
) -> PolicyReport:
    """Executa todas as regras sobre os arquivos coletados.

    Roda cada regra por arquivo, marca as violações cobertas por baseline e
    detecta entradas obsoletas. Arquivos ilegíveis contam em ``files_skipped``.

    Parameters
    ----------
    roots : Sequence[Path | str]
        Diretórios a varrer.
    excludes : Iterable[str] | None
        Segmentos adicionais a excluir.
    baseline_path : Path | None
        Caminho do ``policies.toml`` (padrão: ``policies.toml`` no cwd).

    Returns
    -------
    PolicyReport
        Relatório consolidado da varredura.
    """
    baseline_path = baseline_path or Path("policies.toml")
    files = collect_files(roots, excludes)
    entries = load_baseline(baseline_path)
    allowlist = load_allowlist(baseline_path)
    base = _scan_base(roots)
    raw_violations, scanned, skipped = _collect_violations(files, base, allowlist)
    violations = tuple(
        (
            replace(v, baseline=True)
            if match(entries, v.rule_id, v.path, location_of(v))
            else v
        )
        for v in raw_violations
    )
    obsolete = find_obsolete(raw_violations, entries, scanned)
    return PolicyReport(
        violations=violations,
        baseline_entries=entries,
        obsolete_entries=obsolete,
        files_scanned=len(scanned),
        files_skipped=skipped,
    )


def _default_reason(rule_id: str) -> str:
    """Retorna o motivo padrão de baseline por regra.

    Parameters
    ----------
    rule_id : str
        Identificador da regra.

    Returns
    -------
    str
        Motivo usado pelo gerador de baseline.
    """
    if rule_id in ("FUNC-LENGTH", "NESTING-DEPTH"):
        return "débito AD-005 (limite de código pré-existente)"
    if rule_id == "NO-PRINT":
        return "carve-out stdout de CLI (OQ-03)"
    return "débito pré-existente"


def generate_baseline(
    roots: Sequence[Path | str],
    excludes: Iterable[str] | None = None,
) -> tuple[BaselineEntry, ...]:
    """Gera entradas de baseline a partir das violações atuais.

    Usado pelo ``--baseline-generate``: roda as regras sem baseline e emite
    uma entrada ativa por violação, com localização simbólica ou por linha.

    Parameters
    ----------
    roots : Sequence[Path | str]
        Diretórios a varrer.
    excludes : Iterable[str] | None
        Segmentos adicionais a excluir.

    Returns
    -------
    tuple[BaselineEntry, ...]
        Entradas de baseline geradas.
    """
    files = collect_files(roots, excludes)
    base = _scan_base(roots)
    entries: list[BaselineEntry] = []
    for file in files:
        rel = normalize_path(file, base)
        try:
            text = _read_file(file)
        except (OSError, UnicodeDecodeError):
            continue
        for rule_id, checker in RULE_REGISTRY:
            for violation in checker(text, rel, base, allowlist=None):
                location = violation.symbol if violation.symbol else str(violation.line)
                entries.append(
                    BaselineEntry(
                        rule_id=rule_id,
                        path=violation.path,
                        location=location,
                        reason=_default_reason(rule_id),
                    )
                )
    return tuple(entries)
