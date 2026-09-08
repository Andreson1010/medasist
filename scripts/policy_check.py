"""CLI do policy checker do MedAssist (convenções AGENTS.md + segurança).

Varre ``src/ tests/ scripts/`` (ou paths informados) com as regras estáticas
do pacote ``medasist.policies`` e aplica a baseline em ``policies.toml``.
Exit 0 quando não há violações novas nem baseline obsoleta; exit 1 caso
contrário. O relatório é emitido via ``sys.stdout.write`` (nunca ``print()``).
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from medasist.policies import PolicyReport, generate_baseline, run_scan  # noqa: E402
from medasist.policies.baseline import save_baseline  # noqa: E402
from medasist.policies.scanner import DEFAULT_ROOTS  # noqa: E402

logger = logging.getLogger(__name__)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parseia argumentos da linha de comando do policy checker.

    Parameters
    ----------
    argv : list[str] | None
        Lista de argumentos (None usa sys.argv).

    Returns
    -------
    argparse.Namespace
        Argumentos parseados (paths, exclude, baseline, baseline_generate).
    """
    parser = argparse.ArgumentParser(
        description="Valida convenções do AGENTS.md e regras de segurança "
        "por policy-as-code (exit 0 conforme / 1 com violações ou baseline "
        "obsoleta).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "paths",
        nargs="*",
        default=list(DEFAULT_ROOTS),
        help="Diretórios a varrer (padrão: src tests scripts).",
    )
    parser.add_argument(
        "--exclude",
        nargs="+",
        default=None,
        help="Segmentos adicionais a excluir da varredura.",
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        default=Path("policies.toml"),
        help="Caminho do arquivo de baseline (padrão: policies.toml).",
    )
    parser.add_argument(
        "--baseline-generate",
        nargs="?",
        const=True,
        default=None,
        metavar="PATH",
        help="Gera baseline das violações atuais em PATH (padrão: --baseline).",
    )
    return parser.parse_args(argv)


def _render(report: PolicyReport) -> str:
    """Converte um relatório de política em texto legível para o stdout.

    Parameters
    ----------
    report : PolicyReport
        Relatório gerado por ``run_scan``.

    Returns
    -------
    str
        Relatório formatado (regra, arquivo, local, mensagem e contagens).
    """
    lines = [
        "Policy check MedAssist — convenções AGENTS.md + regras de segurança",
        f"Arquivos: {report.files_scanned} escaneados, "
        f"{report.files_skipped} pulados",
        f"Violacoes: {report.total_violations} total "
        f"({len(report.new_violations)} novas, "
        f"{len(report.baselined_violations)} baselinadas)",
    ]
    if report.files_skipped:
        lines.append("Arquivos ilegíveis: falha o gate (corrija a codificação/leitura)")
    if report.files_scanned == 0:
        lines.append("nenhum arquivo .py varrido — verifique raízes/exclusões")
    for v in report.new_violations:
        symbol = f" ({v.symbol})" if v.symbol else ""
        lines.append(f"  [{v.rule_id}] {v.path}:{v.line}{symbol} — {v.message}")
    if any(v.rule_id == "PATIENT-DATA" for v in report.new_violations):
        lines.append(
            "PATIENT-DATA: regra de segurança não é baselinável — corrija o dado"
        )
    if report.obsolete_entries:
        lines.append(
            f"Baseline obsoleta ({len(report.obsolete_entries)}): "
            "remova/regere policies.toml"
        )
        for entry in report.obsolete_entries:
            lines.append(
                f"  [{entry.rule_id}] {entry.path} / {entry.location} "
                f"— {entry.reason}"
            )
    lines.append(
        "Resultado: FALHA (exit 1)" if report.has_failures else "Resultado: OK (exit 0)"
    )
    return "\n".join(lines) + "\n"


def _run_generate(args: argparse.Namespace, excludes: tuple[str, ...] | None) -> int:
    """Gera a baseline das violações atuais e grava o arquivo TOML.

    Parameters
    ----------
    args : argparse.Namespace
        Argumentos parseados (paths, exclude, baseline, baseline_generate).
    excludes : tuple[str, ...] | None
        Segmentos excluídos repassados ao scanner.

    Returns
    -------
    int
        0 em sucesso.
    """
    output = (
        args.baseline
        if args.baseline_generate is True
        else Path(args.baseline_generate)
    )
    entries = generate_baseline(args.paths, excludes)
    save_baseline(output, entries)
    sys.stdout.write(f"Baseline gerada em {output}: {len(entries)} entrada(s).\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Ponto de entrada do policy checker.

    Executa a varredura com a baseline informada e emite o relatório no
    stdout. Erros fatais (ex.: baseline TOML inválida) são logados e retornam
    1 — nunca falso-verde.

    Parameters
    ----------
    argv : list[str] | None
        Argumentos CLI (None usa sys.argv).

    Returns
    -------
    int
        0 quando a árvore está conforme; 1 com violações novas, baseline
        obsoleta ou erro fatal.
    """
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parse_args(argv)
    excludes = tuple(args.exclude) if args.exclude else None
    try:
        if args.baseline_generate is not None:
            return _run_generate(args, excludes)
        report = run_scan(args.paths, excludes, args.baseline)
    except Exception as exc:
        logger.error("Falha na varredura de políticas: %s", exc)
        return 1
    sys.stdout.write(_render(report))
    return 0 if not report.has_failures else 1


if __name__ == "__main__":
    sys.exit(main())
