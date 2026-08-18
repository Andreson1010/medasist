#!/usr/bin/env python
"""Harness de avaliação de RAG para o skill rag-pipeline-audit.

Computa métricas de Retrieval (Context Precision, MRR, Hit Rate) e Generation
(Faithfulness, Answer Relevancy, Context Utilization) usando o pipeline REAL do
projeto + o LLM local (LM Studio) como juiz, sem dependência externa (ragas/deepeval).

Degrada com elegância: se o LLM, o vector DB ou as dependências nativas (chromadb/
hnswlib) não estiverem disponíveis, grava um JSON com status "skipped" e o motivo —
o skill então entrega a análise estática + plano de evals no lugar das métricas.

Uso:
    python rag_eval.py --out results.json [--golden evals/rag_golden.json] [--n 15] [--k 5]

Golden set (opcional), JSON:
    [{"pergunta": "...", "resposta_ref": "...", "contexto_ref": "..."}, ...]
Sem golden, gera perguntas sintéticas a partir de chunks amostrados (rotuladas como
baseline sintético; Context Recall e Answer Correctness ficam "requer golden set").
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any


# --------------------------------------------------------------------------- #
# Setup do projeto (descobre src/ e importa medasist)
# --------------------------------------------------------------------------- #
def _achar_raiz_projeto(start: Path) -> Path | None:
    """Sobe diretórios procurando src/medasist/."""
    for p in [start, *start.parents]:
        if (p / "src" / "medasist").is_dir():
            return p
    return None


def _bootstrap() -> dict[str, Any]:
    """Importa os módulos do projeto. Retorna dict com handles ou {'erro': ...}."""
    raiz = _achar_raiz_projeto(Path.cwd()) or _achar_raiz_projeto(Path(__file__).resolve())
    if raiz is None:
        return {"erro": "não encontrei src/medasist/ — rode a partir da raiz do projeto"}
    src = str(raiz / "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    try:
        from medasist.config import get_settings  # type: ignore
        from medasist.vectorstore.store import get_client, build_embeddings, get_all_vectorstores  # type: ignore
        from medasist.retrieval.retriever import retrieve  # type: ignore
        from medasist.generation.chain import run_query  # type: ignore
    except Exception as exc:  # noqa: BLE001 — import nativo (chromadb/hnswlib) pode falhar
        return {"erro": f"falha ao importar medasist ({type(exc).__name__}): {exc}"}
    return {
        "raiz": raiz,
        "get_settings": get_settings,
        "get_client": get_client,
        "build_embeddings": build_embeddings,
        "get_all_vectorstores": get_all_vectorstores,
        "retrieve": retrieve,
        "run_query": run_query,
    }


# --------------------------------------------------------------------------- #
# Cliente LLM (juiz) — OpenAI-compat (LM Studio)
# --------------------------------------------------------------------------- #
def _criar_llm(settings: Any):
    """Retorna (client, model) ou (None, motivo)."""
    try:
        from openai import OpenAI  # type: ignore
    except Exception as exc:  # noqa: BLE001
        return None, f"pacote openai indisponível: {exc}"
    try:
        base_url = settings.lm_studio_base_url
        api_key = settings.lm_studio_api_key.get_secret_value()
        client = OpenAI(base_url=base_url, api_key=api_key)
        client.models.list()
    except Exception as exc:  # noqa: BLE001
        return None, f"LM Studio inacessível em {settings.lm_studio_base_url}: {exc}"
    return client, settings.lm_studio_llm_model


def _juiz_json(client: Any, model: str, prompt: str) -> dict:
    """Pede ao LLM um veredito JSON {'score': float, 'motivo': str}. Robusto a ruído."""
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "Você é um avaliador rigoroso. Responda SÓ JSON."},
                {"role": "user", "content": prompt},
            ],
            temperature=0,
        )
        texto = (resp.choices[0].message.content or "").strip()
        ini, fim = texto.find("{"), texto.rfind("}")
        if ini >= 0 and fim > ini:
            obj = json.loads(texto[ini : fim + 1])
            return {"score": float(obj.get("score", 0)), "motivo": str(obj.get("motivo", ""))}
    except Exception as exc:  # noqa: BLE001
        return {"score": 0.0, "motivo": f"juiz falhou: {exc}"}
    return {"score": 0.0, "motivo": "sem JSON na resposta"}


# --------------------------------------------------------------------------- #
# Conjunto de perguntas (golden ou sintético)
# --------------------------------------------------------------------------- #
def _carregar_golden(caminho: Path) -> list[dict]:
    dados = json.loads(caminho.read_text(encoding="utf-8"))
    return [d for d in dados if d.get("pergunta")]


def _gerar_sinteticas(h: dict, client: Any, model: str, n: int) -> list[dict]:
    """Amostra n chunks do vector store e gera 1 pergunta por chunk."""
    settings = h["get_settings"]()
    chroma_client = h["get_client"](settings)
    embeddings = h["build_embeddings"](settings)
    stores = h["get_all_vectorstores"](chroma_client, embeddings, settings)

    sementes = ["resumo", "processo", "definição", "objetivo", "requisito", "exemplo"]
    vistos: dict[str, str] = {}
    for s in sementes:
        try:
            docs = h["retrieve"](s, stores, settings)
            for d in docs:
                vistos.setdefault(d.page_content[:400], d.page_content)
        except Exception:  # noqa: BLE001
            continue
        if len(vistos) >= n:
            break
    chunks = list(vistos.values())[:n]
    perguntas = []
    for ch in chunks:
        v = _juiz_json(
            client, model,
            "A partir DESTE trecho, gere UMA pergunta objetiva respondível só com ele. "
            'Responda JSON {"score": 1, "motivo": "<a pergunta>"}.\n\nTrecho:\n' + ch[:1500],
        )
        q = v["motivo"].strip()
        if q:
            perguntas.append({"pergunta": q, "chunk_fonte": ch})
    return perguntas


# --------------------------------------------------------------------------- #
# Métricas por pergunta
# --------------------------------------------------------------------------- #
def _cosine(a: list[float], b: list[float]) -> float:
    import math
    num = sum(x * y for x, y in zip(a, b))
    da = math.sqrt(sum(x * x for x in a)) or 1.0
    db = math.sqrt(sum(y * y for y in b)) or 1.0
    return num / (da * db)


def _avaliar_pergunta(h: dict, client: Any, model: str, item: dict, k: int) -> dict:
    from medasist.profiles.schemas import UserProfile  # type: ignore

    q = item["pergunta"]
    settings = h["get_settings"]()
    chroma_client = h["get_client"](settings)
    embeddings = h["build_embeddings"](settings)
    stores = h["get_all_vectorstores"](chroma_client, embeddings, settings)

    # Retrieval
    try:
        docs = h["retrieve"](q, stores, settings)[:k]
    except Exception as exc:  # noqa: BLE001
        return {"pergunta": q, "erro_retrieval": str(exc)}
    contextos = [d.page_content for d in docs]

    # Context Precision: juiz de relevância por chunk
    rels = []
    for c in contextos:
        v = _juiz_json(
            client, model,
            f'Pergunta: {q}\n\nTrecho:\n{c[:1200]}\n\nO trecho é relevante para responder a '
            'pergunta? Responda JSON {"score": 0 ou 1, "motivo": "..."}.',
        )
        rels.append(1 if v["score"] >= 0.5 else 0)
    precision = sum(rels) / len(rels) if rels else 0.0
    hit = 1.0 if any(rels) else 0.0
    mrr = 0.0
    for i, r in enumerate(rels):
        if r:
            mrr = 1.0 / (i + 1)
            break

    # Generation — usa run_query do MedAssist (LangChain LCEL)
    try:
        result = h["run_query"](q, stores, UserProfile.MEDICO, settings)
        resposta = result.answer
        gen_erro = ""
    except Exception as exc:  # noqa: BLE001
        resposta = ""
        gen_erro = str(exc)

    # Faithfulness: a resposta é sustentada pelo contexto?
    faith = _juiz_json(
        client, model,
        f'Contexto:\n{chr(10).join(contextos)[:2500]}\n\nResposta:\n{resposta[:1200]}\n\n'
        'Que fração das afirmações da resposta é sustentada pelo contexto? '
        'Responda JSON {"score": 0..1, "motivo": "..."}.',
    )["score"] if resposta else 0.0

    # Context Utilization: a resposta usou o contexto relevante?
    util = _juiz_json(
        client, model,
        f'Pergunta: {q}\nContexto:\n{chr(10).join(contextos)[:2500]}\nResposta:\n{resposta[:1200]}\n\n'
        'A resposta aproveitou o contexto relevante disponível? '
        'Responda JSON {"score": 0..1, "motivo": "..."}.',
    )["score"] if resposta else 0.0

    # Answer Relevancy: cosseno(pergunta, resposta) com embeddings do projeto
    relevancy = 0.0
    if resposta:
        try:
            eq = embeddings.embed_query(q)
            ea = embeddings.embed_query(resposta)
            relevancy = _cosine(eq, ea)
        except Exception:  # noqa: BLE001
            relevancy = 0.0

    return {
        "pergunta": q,
        "resposta": resposta[:300],
        "context_precision": round(precision, 3),
        "hit": hit,
        "mrr": round(mrr, 3),
        "faithfulness": round(faith, 3),
        "context_utilization": round(util, 3),
        "answer_relevancy": round(relevancy, 3),
        "gen_erro": gen_erro,
        "n_chunks": len(contextos),
    }


def _agregar(linhas: list[dict]) -> dict:
    def media(campo: str) -> float | None:
        vals = [x[campo] for x in linhas if campo in x and isinstance(x[campo], (int, float))]
        return round(statistics.mean(vals), 3) if vals else None

    return {
        "context_precision": media("context_precision"),
        "hit_rate": media("hit"),
        "mrr": media("mrr"),
        "faithfulness": media("faithfulness"),
        "context_utilization": media("context_utilization"),
        "answer_relevancy": media("answer_relevancy"),
        "context_recall": "requer golden set",
        "answer_correctness": "requer golden set",
    }


def _piores_casos(linhas: list[dict]) -> list[dict]:
    ok = [x for x in linhas if "faithfulness" in x]
    piores = sorted(ok, key=lambda x: (x["faithfulness"], x["context_precision"]))[:3]
    return [
        {"pergunta": x["pergunta"], "faithfulness": x["faithfulness"],
         "context_precision": x["context_precision"], "resposta": x["resposta"]}
        for x in piores
    ]


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Avaliação de RAG (retrieval + generation).")
    ap.add_argument("--out", required=True, help="caminho do JSON de saída")
    ap.add_argument("--golden", default=None, help="golden set JSON (opcional)")
    ap.add_argument("--n", type=int, default=15, help="nº de perguntas sintéticas se não houver golden")
    ap.add_argument("--k", type=int, default=5, help="top-k chunks a avaliar")
    args = ap.parse_args(argv)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    def gravar(payload: dict) -> int:
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"status": payload.get("status"), "out": str(out)}, ensure_ascii=False))
        return 0

    h = _bootstrap()
    if "erro" in h:
        return gravar({"status": "skipped", "motivo": h["erro"],
                       "dica": "rode a Fase 1 (estática) + plano de evals no relatório."})

    settings = h["get_settings"]()
    client, model_ou_motivo = _criar_llm(settings)
    if client is None:
        return gravar({"status": "skipped", "motivo": model_ou_motivo,
                       "dica": "suba o LM Studio com o modelo carregado e rode de novo."})
    model = model_ou_motivo

    # Conjunto de perguntas
    if args.golden and Path(args.golden).exists():
        itens = _carregar_golden(Path(args.golden))
        origem = f"golden:{args.golden}"
    else:
        itens = _gerar_sinteticas(h, client, model, args.n)
        origem = "sintético (baseline — Recall/Correctness requerem golden set)"
    if not itens:
        return gravar({"status": "skipped", "motivo": "sem perguntas (corpus vazio?)",
                       "dica": "confirme que o ChromaDB está populado."})

    linhas = [_avaliar_pergunta(h, client, model, it, args.k) for it in itens]
    return gravar({
        "status": "ok",
        "origem_perguntas": origem,
        "n_perguntas": len(linhas),
        "top_k": args.k,
        "agregados": _agregar(linhas),
        "piores_casos": _piores_casos(linhas),
        "detalhe": linhas,
    })


if __name__ == "__main__":
    raise SystemExit(main())
