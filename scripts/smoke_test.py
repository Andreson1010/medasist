from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

_COLD_START_QUESTION = "Como tratar pneumonia fúngica em camaleões?"
_DISCLAIMER = (
    "Este sistema é um auxiliar informativo e não substitui "
    "avaliação médica presencial."
)
_RESULTS: list[tuple[str, bool, str]] = []


def _record(name: str, ok: bool, detail: str = "") -> None:
    """Registra o resultado de um passo do smoke test.

    Parameters
    ----------
    name : str
        Nome do passo testado.
    ok : bool
        ``True`` quando o passo passou.
    detail : str
        Detalhe opcional (ex: resposta recebida).
    """
    status = "OK  " if ok else "FAIL"
    _RESULTS.append((name, ok, detail))
    logger.info("[%s] %s%s", status, name, f" — {detail}" if detail else "")


def _get(base_url: str, path: str) -> httpx.Response:
    """Executa um GET com timeout curto contra a API.

    Parameters
    ----------
    base_url : str
        Base da API (ex: ``http://localhost:8000``).
    path : str
        Caminho do endpoint (ex: ``/health``).

    Returns
    -------
    httpx.Response
        Resposta HTTP.
    """
    return httpx.get(f"{base_url}{path}", timeout=10.0)


def _post(base_url: str, path: str, payload: dict) -> httpx.Response:
    """Executa um POST JSON com timeout curto contra a API.

    Parameters
    ----------
    base_url : str
        Base da API (ex: ``http://localhost:8000``).
    path : str
        Caminho do endpoint (ex: ``/query``).
    payload : dict
        Corpo JSON da requisição.

    Returns
    -------
    httpx.Response
        Resposta HTTP.
    """
    return httpx.post(f"{base_url}{path}", json=payload, timeout=30.0)


def _check_health(base_url: str) -> bool:
    """Verifica ``GET /health``: status ok e dependências ok.

    Parameters
    ----------
    base_url : str
        Base da API.

    Returns
    -------
    bool
        ``True`` se o health está ``ok`` com ChromaDB e LM Studio ok.
    """
    try:
        response = _get(base_url, "/health")
    except httpx.HTTPError as exc:
        _record("health HTTP", False, f"erro de conexão: {exc}")
        return False
    body = response.json()
    ok = (
        response.status_code == 200
        and body.get("status") == "ok"
        and body.get("chromadb", {}).get("status") == "ok"
        and body.get("lm_studio", {}).get("status") == "ok"
    )
    _record("health ok (ChromaDB + LM Studio)", ok, str(body.get("status")))
    return ok


def _check_query_success(base_url: str) -> bool:
    """Verifica uma consulta feliz: resposta, citações, disclaimer, não-cold-start.

    Parameters
    ----------
    base_url : str
        Base da API.

    Returns
    -------
    bool
        ``True`` se a resposta tem conteúdo, citações e disclaimer.
    """
    try:
        response = _post(
            base_url,
            "/query",
            {
                "question": "Qual a dose de amoxicilina para adultos?",
                "profile": "medico",
            },
        )
    except httpx.HTTPError as exc:
        _record("query feliz", False, f"erro de conexão: {exc}")
        return False
    body = response.json()
    citations = body.get("citations") or []
    ok = (
        response.status_code == 200
        and bool(body.get("answer"))
        and not body.get("is_cold_start")
        and body.get("disclaimer") == _DISCLAIMER
        and bool(citations)
    )
    if ok and citations:
        first = citations[0]
        ok = ok and bool(first.get("source")) and first.get("source") != "unknown"
    _record(
        "query feliz (answer + citações + disclaimer)",
        ok,
        f"{len(citations)} citações",
    )
    return ok


def _check_query_cold_start(base_url: str) -> bool:
    """Verifica o cold start: mensagem fixa, sem citações, flag true.

    Parameters
    ----------
    base_url : str
        Base da API.

    Returns
    -------
    bool
        ``True`` se a resposta é cold start sem citações.
    """
    try:
        response = _post(
            base_url,
            "/query",
            {"question": _COLD_START_QUESTION, "profile": "medico"},
        )
    except httpx.HTTPError as exc:
        _record("cold start", False, f"erro de conexão: {exc}")
        return False
    body = response.json()
    ok = (
        response.status_code == 200
        and body.get("is_cold_start") is True
        and body.get("citations") == []
        and bool(body.get("answer"))
    )
    _record("cold start (mensagem fixa, sem citações)", ok, body.get("answer", "")[:50])
    return ok


def _check_query_validation(base_url: str) -> bool:
    """Verifica validação 422 para requisição inválida (question vazia).

    Parameters
    ----------
    base_url : str
        Base da API.

    Returns
    -------
    bool
        ``True`` se a API retorna 422 para question vazia.
    """
    try:
        response = _post(
            base_url,
            "/query",
            {"question": "", "profile": "medico"},
        )
    except httpx.HTTPError as exc:
        _record("validação 422", False, f"erro de conexão: {exc}")
        return False
    ok = response.status_code == 422
    _record("validação (question vazia -> 422)", ok, f"HTTP {response.status_code}")
    return ok


def _check_ingest(base_url: str, admin_key: str, sample_pdf: Path | None) -> bool:
    """Verifica o /ingest com admin key (se um PDF de exemplo for fornecido).

    Parameters
    ----------
    base_url : str
        Base da API.
    admin_key : str
        Chave de admin (header ``X-Admin-Key``).
    sample_pdf : Path | None
        PDF de exemplo para testar ingestão (None pula o upload).

    Returns
    -------
    bool
        ``True`` se a ingestão responde 200 com chunks indexados.
    """
    try:
        unauthorized = httpx.post(
            f"{base_url}/ingest", timeout=10.0, headers={"X-Admin-Key": "senha-errada"}
        )
    except httpx.HTTPError as exc:
        _record("ingest auth", False, f"erro de conexão: {exc}")
        return False
    _record(
        "ingest sem/senha errada -> 401/422", unauthorized.status_code in (401, 422)
    )

    if sample_pdf is None:
        _record("ingest upload (sem --sample-pdf, pulado)", True)
        return True

    try:
        with sample_pdf.open("rb") as handle:
            response = httpx.post(
                f"{base_url}/ingest",
                headers={"X-Admin-Key": admin_key},
                files={"file": (sample_pdf.name, handle, "application/pdf")},
                timeout=60.0,
            )
    except httpx.HTTPError as exc:
        _record("ingest upload", False, f"erro de conexão: {exc}")
        return False
    body = response.json()
    ok = (
        response.status_code == 200
        and body.get("chunks_indexed", 0) > 0
        and body.get("skipped") is False
    )
    _record(
        "ingest upload (admin key correta)",
        ok,
        f"{body.get('chunks_indexed')} chunks",
    )
    return ok


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parseia argumentos do smoke test.

    Parameters
    ----------
    argv : list[str] | None
        Lista de argumentos (None usa sys.argv).

    Returns
    -------
    argparse.Namespace
        Argumentos com ``base_url``, ``admin_key`` e ``sample_pdf``.
    """
    parser = argparse.ArgumentParser(
        description="Smoke test ponta a ponta da API MedAssist "
        "(health, query, cold start, validação e ingestão)."
    )
    parser.add_argument(
        "--base-url",
        default="http://localhost:8000",
        help="Base da API MedAssist (default: http://localhost:8000).",
    )
    parser.add_argument(
        "--admin-key",
        default="",
        help="Admin key para testar /ingest (header X-Admin-Key).",
    )
    parser.add_argument(
        "--sample-pdf",
        type=Path,
        default=None,
        help="PDF de exemplo para testar upload no /ingest (opcional).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Executa o smoke test e retorna o código de saída.

    Parameters
    ----------
    argv : list[str] | None
        Argumentos CLI (None usa sys.argv).

    Returns
    -------
    int
        0 quando todos os passos passam; 1 caso contrário.
    """
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parse_args(argv)

    _check_health(args.base_url)
    _check_query_success(args.base_url)
    _check_query_cold_start(args.base_url)
    _check_query_validation(args.base_url)
    _check_ingest(args.base_url, args.admin_key, args.sample_pdf)

    failed = [name for name, ok, _ in _RESULTS if not ok]
    logger.info("")
    logger.info(
        "=== Smoke test: %d/%d passos OK ===",
        len(_RESULTS) - len(failed),
        len(_RESULTS),
    )
    if failed:
        logger.error("Passos falhos:")
        for name, ok, detail in _RESULTS:
            if not ok:
                logger.error("  - %s%s", name, f" — {detail}" if detail else "")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
