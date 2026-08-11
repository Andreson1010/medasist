from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx

from medasist.config import get_settings
from medasist.evaluation import (
    EvaluationReport,
    GoldenQuestion,
    evaluate_golden_set,
    load_golden_set,
)
from medasist.ingestion.schemas import DocType
from medasist.profiles.schemas import UserProfile
from medasist.vectorstore.store import (
    build_embeddings,
    get_all_vectorstores,
    get_client,
)

logger = logging.getLogger(__name__)

_PROFILE_CHOICES = [p.value for p in UserProfile]
_DOC_TYPE_CHOICES = [dt.value for dt in DocType]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parseia argumentos da linha de comando da avaliação RAG.

    Parameters
    ----------
    argv : list[str] | None
        Lista de argumentos (None usa sys.argv).

    Returns
    -------
    argparse.Namespace
        Argumentos parseados com defaults vindos das Settings.
    """
    settings = get_settings()
    parser = argparse.ArgumentParser(
        description="Avalia o pipeline RAG do MedAssist sobre um golden set "
        "offline (RAGAS 0.2.15), sem passar pela API HTTP.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=settings.eval_golden_set_path,
        help="Caminho do golden set JSON.",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=None,
        help="Sobrescreve retrieval_top_k (chunks recuperados por pergunta).",
    )
    parser.add_argument(
        "--n",
        type=int,
        default=None,
        help="Limita o número de perguntas avaliadas.",
    )
    parser.add_argument(
        "--profile",
        choices=_PROFILE_CHOICES,
        default=UserProfile.MEDICO.value,
        help="Perfil de geração/judge.",
    )
    parser.add_argument(
        "--doc-types",
        nargs="+",
        choices=_DOC_TYPE_CHOICES,
        default=None,
        help="Filtra coleções avaliadas (todas por padrão).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Grava relatório JSON em evals/results/ (não versionado).",
    )
    return parser.parse_args(argv)


def _probe_lm_studio(settings) -> bool:
    """Verifica a disponibilidade do LM Studio via ``GET {base_url}/models``.

    Espelha ``api/health.py:check_lm_studio`` sem acoplar ao módulo da API.

    Parameters
    ----------
    settings
        Configurações com ``lm_studio_base_url`` e ``healthcheck_timeout``.

    Returns
    -------
    bool
        ``True`` se o LM Studio respondeu 2xx, ``False`` caso contrário.
    """
    url = f"{settings.lm_studio_base_url}/models"
    try:
        response = httpx.get(url, timeout=settings.healthcheck_timeout)
    except httpx.HTTPError as exc:
        logger.error("LM Studio inacessível: %s", exc)
        return False
    if response.status_code >= 300:
        logger.error("LM Studio respondeu HTTP %s", response.status_code)
        return False
    logger.info("LM Studio disponível em %s", settings.lm_studio_base_url)
    return True


def _probe_collections(settings, doc_types: list[str] | None) -> dict | None:
    """Abre os vectorstores e valida que as coleções selecionadas não estão vazias.

    Parameters
    ----------
    settings
        Configurações com ``chroma_dir``.
    doc_types : list[str] | None
        DocTypes selecionados no CLI (None = todos).

    Returns
    -------
    dict | None
        Dicionário ``DocType → Chroma`` filtrado, ou ``None`` em falha.
    """
    try:
        client = get_client(settings)
        stores = get_all_vectorstores(client, build_embeddings(settings), settings)
    except Exception as exc:
        logger.error("Falha ao abrir vectorstores ChromaDB: %s", exc)
        return None

    selected = {DocType(v) for v in doc_types} if doc_types else set(stores)
    filtered = {dt: stores[dt] for dt in selected if dt in stores}

    empty = sorted(
        dt.value for dt, store in filtered.items() if store._collection.count() == 0
    )
    if empty:
        logger.error("Coleção(ões) ChromaDB vazia(s): %s", ", ".join(empty))
        return None

    logger.info("Coleções prontas: %s", [dt.value for dt in filtered])
    return filtered


def _probe_fail_fast(
    args: argparse.Namespace,
    settings: object,
) -> tuple[dict, list[GoldenQuestion]] | None:
    """Probes fail-fast na ordem: dataset → LM Studio → coleções ChromaDB.

    Parameters
    ----------
    args : argparse.Namespace
        Argumentos parseados (``dataset``, ``doc_types``, ``n``).
    settings : object
        Configurações do projeto (``healthcheck_timeout``, ``chroma_dir``).

    Returns
    -------
    tuple[dict, list[GoldenQuestion]] | None
        ``(stores filtradas, perguntas após --n)`` em sucesso, ou ``None``
        quando qualquer probe falha.
    """
    try:
        golden = load_golden_set(args.dataset)
    except ValueError as exc:
        logger.error("Golden set inválido: %s", exc)
        return None

    if not _probe_lm_studio(settings):
        return None

    stores = _probe_collections(settings, args.doc_types)
    if stores is None:
        return None

    questions = golden.questions
    if args.n is not None:
        questions = questions[: args.n]
    if not questions:
        logger.error("Nenhuma pergunta para avaliar (--n reduzido a zero).")
        return None
    return stores, questions


def _emit_report(report: EvaluationReport, args: argparse.Namespace) -> None:
    """Imprime o relatório no stdout e grava JSON quando ``--output``.

    Parameters
    ----------
    report : EvaluationReport
        Relatório gerado por ``evaluate_golden_set``.
    args : argparse.Namespace
        Argumentos do CLI (usa ``--output`` quando informado).
    """
    _print_report(report)
    if args.output is not None:
        _write_report(args.output, report, args)


def _print_report(report: EvaluationReport) -> None:
    """Imprime agregadas e tabela por pergunta no stdout.

    Parameters
    ----------
    report : EvaluationReport
        Relatório gerado por ``evaluate_golden_set``.
    """
    print("\nAvaliação RAG (RAGAS 0.2.15) — agregadas")
    for metric, value in report.aggregates.items():
        display = f"{value:.4f}" if value is not None else "n/d"
        print(f"  {metric}: {display}")
    print(
        f"  perguntas: {report.num_questions} | cold start: {report.num_cold_start} "
        f"| avaliadas em geração: {report.num_generation_evaluated}"
    )
    print("\nPor pergunta:")
    for i, row in enumerate(report.per_question, start=1):
        status = "cold start" if row.is_cold_start else "ok"
        print(f"  {i}. [{status}] {row.question[:70]}")
        details = ", ".join(
            f"{k}={v:.4f}" if v is not None else f"{k}=n/d"
            for k, v in row.metrics.items()
        )
        print(f"     {details}")


def _write_report(
    output: Path,
    report: EvaluationReport,
    args: argparse.Namespace,
) -> None:
    """Grava o relatório JSON de saída (schema do design §5.3).

    Parameters
    ----------
    output : Path
        Caminho de saída do arquivo JSON.
    report : EvaluationReport
        Relatório a serializar.
    args : argparse.Namespace
        Argumentos do CLI (dataset, profile, doc_types).
    """
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "dataset": str(args.dataset),
        "profile": args.profile,
        "doc_types": args.doc_types or [],
        "aggregates": report.aggregates,
        "counts": {
            "questions": report.num_questions,
            "cold_start": report.num_cold_start,
            "generation_evaluated": report.num_generation_evaluated,
        },
        "per_question": [
            {
                "question": row.question,
                "is_cold_start": row.is_cold_start,
                "contexts": row.contexts,
                "answer": row.answer,
                "metrics": row.metrics,
            }
            for row in report.per_question
        ],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info("Relatório gravado em %s", output)


def main(argv: list[str] | None = None) -> int:
    """Ponto de entrada do script de avaliação RAG.

    Fail-fast: dataset → LM Studio → coleções (``_probe_fail_fast``). Em
    sucesso, avalia o golden set, imprime o relatório e opcionalmente grava
    JSON (``_emit_report``).

    Parameters
    ----------
    argv : list[str] | None
        Argumentos CLI (None usa sys.argv).

    Returns
    -------
    int
        0 em sucesso, 1 em qualquer falha (dataset, LM Studio, coleções,
        geração vazia ou erro interno do RAGAS).
    """
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parse_args(argv)
    settings = get_settings()

    prepared = _probe_fail_fast(args, settings)
    if prepared is None:
        return 1
    stores, questions = prepared
    doc_types = [DocType(v) for v in args.doc_types] if args.doc_types else None

    try:
        report = evaluate_golden_set(
            questions,
            stores,
            settings,
            profile=UserProfile(args.profile),
            doc_types=doc_types,
            top_k=args.top_k,
        )
    except Exception as exc:
        logger.error("Falha na avaliação RAGAS: %s", exc)
        return 1

    if report.num_generation_evaluated == 0:
        logger.error(
            "Nenhuma pergunta não-cold-start para avaliar em geração (REQ-12)."
        )
        return 1

    _emit_report(report, args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
