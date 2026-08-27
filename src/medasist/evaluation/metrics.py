from __future__ import annotations

import logging
from dataclasses import dataclass
from statistics import fmean
from typing import Any

import numpy as np
from datasets import Dataset
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from ragas.embeddings import LangchainEmbeddingsWrapper
from ragas.evaluation import evaluate
from ragas.llms import LangchainLLMWrapper
from ragas.metrics import AnswerRelevancy, ContextPrecision, ContextRecall, Faithfulness
from ragas.run_config import RunConfig

from medasist.config import Settings
from medasist.evaluation.dataset import GoldenQuestion
from medasist.generation.chain import run_query
from medasist.ingestion.schemas import DocType
from medasist.profiles.schemas import UserProfile
from medasist.retrieval.retriever import retrieve, select_collections

logger = logging.getLogger(__name__)

_COLUMN_MAP = {"reference": "reference_answer"}


@dataclass(frozen=True)
class QuestionEvalRow:
    """Avaliação individual de uma pergunta do golden set.

    Attributes
    ----------
    question : str
        Pergunta avaliada.
    contexts : list[str]
        Contextos recuperados pelo ``retrieve`` real do pipeline.
    answer : str
        Resposta gerada pelo ``run_query`` (ou mensagem de cold start).
    is_cold_start : bool
        ``True`` quando a pergunta resultou em cold start no pipeline.
    metrics : dict[str, float | None]
        Score por métrica; ``None`` quando a métrica não foi avaliada
        (ex: métricas de geração em perguntas de cold start).
    """

    question: str
    contexts: list[str]
    answer: str
    is_cold_start: bool
    metrics: dict[str, float | None]


@dataclass(frozen=True)
class EvaluationReport:
    """Relatório consolidado da avaliação RAG sobre o golden set.

    Attributes
    ----------
    aggregates : dict[str, float | None]
        Média por métrica sobre o subconjunto onde foi avaliada.
    per_question : list[QuestionEvalRow]
        Avaliação de cada pergunta.
    num_questions : int
        Total de perguntas avaliadas.
    num_cold_start : int
        Perguntas que resultaram em cold start no pipeline.
    num_generation_evaluated : int
        Perguntas não-cold-start (avaliadas em Faithfulness/AnswerRelevancy).
    num_retrieval_evaluated : int
        Perguntas não-cold-start (avaliadas em ContextPrecision/ContextRecall);
        igual a ``num_generation_evaluated``.
    """

    aggregates: dict[str, float | None]
    per_question: list[QuestionEvalRow]
    num_questions: int
    num_cold_start: int
    num_generation_evaluated: int
    num_retrieval_evaluated: int


def build_eval_run_config(settings: Settings) -> RunConfig:
    """Constrói o ``RunConfig`` do RAGAS a partir das settings do projeto.

    ``timeout`` e ``max_workers`` vêm de ``eval_timeout``/``eval_max_workers``
    (defaults pensados para LM Studio local, que não lida bem com 16 workers
    simultâneos do default do RAGAS).

    Parameters
    ----------
    settings : Settings
        Configurações com ``eval_timeout``, ``eval_max_workers`` e
        ``llm_max_retries``.

    Returns
    -------
    RunConfig
        Configuração de timeout/retry/workers do judge RAGAS.
    """
    return RunConfig(
        timeout=settings.eval_timeout,
        max_workers=settings.eval_max_workers,
        max_retries=settings.llm_max_retries,
    )


def build_eval_llm(settings: Settings) -> LangchainLLMWrapper:
    """Constrói o LLM judge RAGAS apontando para o LM Studio local.

    Usa ``eval_llm_model`` (default: ``lm_studio_llm_model``) com
    ``temperature=0.0`` para determinismo do judge. Timeout e workers
    paralelos vêm de ``eval_timeout``/``eval_max_workers`` (defaults pensados
    para LM Studio local, que não lida bem com 16 workers simultâneos).

    Parameters
    ----------
    settings : Settings
        Configurações com URL do LM Studio e modelo de avaliação.

    Returns
    -------
    LangchainLLMWrapper
        Wrapper RAGAS sobre o ``ChatOpenAI`` do LM Studio.
    """
    llm = ChatOpenAI(
        base_url=settings.lm_studio_base_url,
        api_key=settings.lm_studio_api_key.get_secret_value(),
        model=settings.eval_llm_model,
        temperature=0.0,
        max_retries=settings.llm_max_retries,
        request_timeout=settings.llm_request_timeout,
    )
    run_config = build_eval_run_config(settings)
    return LangchainLLMWrapper(llm, run_config=run_config)


def build_eval_embeddings(settings: Settings) -> LangchainEmbeddingsWrapper:
    """Constrói os embeddings RAGAS apontando para o LM Studio local.

    Usa ``eval_embedding_model`` (default: ``lm_studio_embedding_model``)
    espelhando a construção de ``store.build_embeddings``.

    Parameters
    ----------
    settings : Settings
        Configurações com URL do LM Studio e modelo de embeddings.

    Returns
    -------
    LangchainEmbeddingsWrapper
        Wrapper RAGAS sobre o ``OpenAIEmbeddings`` do LM Studio.
    """
    embeddings = OpenAIEmbeddings(
        base_url=settings.lm_studio_base_url,
        api_key=settings.lm_studio_api_key.get_secret_value(),
        model=settings.eval_embedding_model,
        check_embedding_ctx_length=False,
    )
    return LangchainEmbeddingsWrapper(embeddings)


def build_metrics() -> list[Any]:
    """Retorna as 4 métricas RAGAS usadas na avaliação.

    Returns
    -------
    list[Any]
        ``ContextPrecision``, ``ContextRecall``, ``Faithfulness`` e
        ``AnswerRelevancy``.
    """
    return [ContextPrecision(), ContextRecall(), Faithfulness(), AnswerRelevancy()]


def _clean_score(value: object) -> float | None:
    """Converte um score do RAGAS em ``float``, mapeando NaN/None para ``None``.

    Parameters
    ----------
    value : object
        Score bruto retornado pelo RAGAS (pode ser NaN em falha de métrica).

    Returns
    -------
    float | None
        Score numérico ou ``None`` quando ausente/NaN.
    """
    if value is None:
        return None
    try:
        if np.isnan(value):
            return None
    except (TypeError, ValueError):
        pass
    return float(value)


def _score_at(result: Any, index: int, metric: str) -> float | None:
    """Extrai o score de uma métrica para a amostra ``index``.

    Parameters
    ----------
    result : Any
        ``EvaluationResult`` do RAGAS (ou mock equivalente).
    index : int
        Posição da amostra no dataset avaliado.
    metric : str
        Nome da métrica (ex: ``context_precision``).

    Returns
    -------
    float | None
        Score da amostra ou ``None`` se indisponível.
    """
    if result is None or index >= len(result.scores):
        return None
    return _clean_score(result.scores[index].get(metric))


def _aggregate(result: Any, metric: str) -> float | None:
    """Calcula a média de uma métrica sobre as amostras avaliadas.

    Parameters
    ----------
    result : Any
        ``EvaluationResult`` do RAGAS (ou mock equivalente).
    metric : str
        Nome da métrica.

    Returns
    -------
    float | None
        Média dos scores válidos ou ``None`` se nenhum score disponível.
    """
    if result is None:
        return None
    scores = [_clean_score(row.get(metric)) for row in result.scores]
    valid = [s for s in scores if s is not None]
    if not valid:
        return None
    return fmean(valid)


def _reciprocal_rank(
    contexts: list[str],
    reference_contexts: list[str],
) -> float:
    """Calcula o rank recíproco (MRR) do primeiro hit em ``reference_contexts``.

    Retorna ``1 / rank`` da primeira posição de ``contexts`` cujo contexto
    aparece em ``reference_contexts`` (rank baseado em 1); ``0.0`` quando
    nenhum contexto recuperado está entre os de referência.

    Parameters
    ----------
    contexts : list[str]
        Contextos recuperados por ``retrieve`` (na ordem retornada).
    reference_contexts : list[str]
        Contextos de referência do golden set (ground truth).

    Returns
    -------
    float
        Rank recíproco (entre 0.0 e 1.0).
    """
    for rank, context in enumerate(contexts, start=1):
        if context in reference_contexts:
            return 1.0 / rank
    return 0.0


def _aggregate_mrr(
    rows: list[dict[str, Any]],
    eval_indices: list[int],
) -> float | None:
    """Calcula a média do MRR sobre o subconjunto não-cold-start.

    Parameters
    ----------
    rows : list[dict[str, Any]]
        Linhas do dataset com ``contexts`` e ``reference_contexts``.
    eval_indices : list[int]
        Índices das perguntas avaliadas (não-cold-start).

    Returns
    -------
    float | None
        Média do rank recíproco, ou ``None`` quando não há perguntas avaliadas.
    """
    if not eval_indices:
        return None
    return fmean(
        _reciprocal_rank(rows[i]["contexts"], rows[i]["reference_contexts"])
        for i in eval_indices
    )


def _collect_rows(
    questions: list[GoldenQuestion],
    stores: dict[DocType, Any],
    settings: Settings,
    profile: UserProfile | None,
    doc_types: list[DocType] | None,
) -> tuple[list[dict[str, Any]], list[bool]]:
    """Executa ``retrieve``/``run_query`` por pergunta, montando as linhas do dataset.

    ``retrieve`` usa o mesmo subconjunto de stores do ``run_query``
    (``select_collections``, compartilhado com a chain), mantendo contexts
    consistentes com a resposta.

    Parameters
    ----------
    questions : list[GoldenQuestion]
    stores : dict[DocType, Any]
    settings : Settings
    profile : UserProfile | None
    doc_types : list[DocType] | None

    Returns
    -------
    tuple[list[dict[str, Any]], list[bool]]
        Linhas do dataset e flags de cold start.
    """
    rows: list[dict[str, Any]] = []
    cold_flags: list[bool] = []
    for q in questions:
        eff_profile = profile or q.profile
        eff_doc_types = doc_types or (q.doc_types or None)
        subset = select_collections(stores, eff_doc_types)
        docs = retrieve(q.question, subset, settings)
        result = run_query(q.question, stores, eff_profile, settings, eff_doc_types)
        rows.append(
            {
                "question": q.question,
                "contexts": [d.page_content for d in docs],
                "answer": result.answer,
                "reference_answer": q.reference_answer,
                "reference_contexts": q.reference_contexts,
            }
        )
        cold_flags.append(result.is_cold_start)
        logger.info(
            "avaliada pergunta %d/%d (cold_start=%s): %s",
            len(rows),
            len(questions),
            result.is_cold_start,
            q.question[:60],
        )
    return rows, cold_flags


def _evaluate_retrieval_set(
    rows: list[dict[str, Any]],
    eval_indices: list[int],
    llm: LangchainLLMWrapper,
    embeddings: LangchainEmbeddingsWrapper,
    batch_size: int,
    run_config: RunConfig,
) -> Any | None:
    """Executa ``ragas.evaluate`` do conjunto de retrieval (não-cold-start).

    Avalia apenas as perguntas não-cold-start, no mesmo subconjunto da
    geração: cold starts têm ``contexts=[]`` e pontuariam 0 em
    ContextPrecision/ContextRecall, enviesando as agregadas de retrieval.

    Parameters
    ----------
    rows : list[dict[str, Any]]
        Linhas com ``question``/``contexts``/``reference_answer``.
    eval_indices : list[int]
        Índices das perguntas avaliadas (não-cold-start) no dataset.
    llm : LangchainLLMWrapper
        LLM judge do LM Studio.
    embeddings : LangchainEmbeddingsWrapper
        Embeddings do LM Studio.
    batch_size : int
        Tamanho do lote do RAGAS.
    run_config : RunConfig
        Configuração de timeout/retry/workers do judge RAGAS.

    Returns
    -------
    Any | None
        ``EvaluationResult`` do RAGAS com ContextPrecision/ContextRecall, ou
        ``None`` quando nenhuma pergunta não-cold-start existe.
    """
    if not eval_indices:
        logger.warning(
            "Retrieval: nenhuma pergunta não-cold-start — sem métricas de retrieval."
        )
        return None
    return evaluate(
        Dataset.from_list([rows[i] for i in eval_indices]),
        metrics=[ContextPrecision(), ContextRecall()],
        llm=llm,
        embeddings=embeddings,
        column_map=_COLUMN_MAP,
        batch_size=batch_size,
        run_config=run_config,
    )


def _evaluate_generation_set(
    rows: list[dict[str, Any]],
    eval_indices: list[int],
    llm: LangchainLLMWrapper,
    embeddings: LangchainEmbeddingsWrapper,
    batch_size: int,
    run_config: RunConfig,
) -> Any | None:
    """Executa ``ragas.evaluate`` do conjunto de geração (não-cold-start).

    Parameters
    ----------
    rows : list[dict[str, Any]]
        Linhas com ``question``/``contexts``/``answer``/``reference_answer``.
    eval_indices : list[int]
        Índices das perguntas não-cold-start no dataset de retrieval.
    llm : LangchainLLMWrapper
        LLM judge do LM Studio.
    embeddings : LangchainEmbeddingsWrapper
        Embeddings do LM Studio.
    batch_size : int
        Tamanho do lote do RAGAS.
    run_config : RunConfig
        Configuração de timeout/retry/workers do judge RAGAS.

    Returns
    -------
    Any | None
        ``EvaluationResult`` do RAGAS com Faithfulness/AnswerRelevancy, ou
        ``None`` quando nenhuma pergunta não-cold-start existe.
    """
    if not eval_indices:
        logger.warning(
            "Geração: nenhuma pergunta não-cold-start — sem métricas de geração."
        )
        return None
    return evaluate(
        Dataset.from_list([rows[i] for i in eval_indices]),
        metrics=[Faithfulness(), AnswerRelevancy()],
        llm=llm,
        embeddings=embeddings,
        column_map=_COLUMN_MAP,
        batch_size=batch_size,
        run_config=run_config,
    )


def _build_per_question(
    rows: list[dict[str, Any]],
    cold_flags: list[bool],
    retrieval_result: Any | None,
    generation_result: Any | None,
) -> list[QuestionEvalRow]:
    """Monta os ``QuestionEvalRow`` mapeando os scores do RAGAS por pergunta.

    Ambos os resultados do RAGAS estão indexados sobre o subconjunto
    não-cold-start (o mesmo de geração); por isso um único contador ``pos``
    serve para retrieval e geração. Perguntas cold start recebem ``None`` em
    todas as métricas.

    Parameters
    ----------
    rows : list[dict[str, Any]]
    cold_flags : list[bool]
    retrieval_result : Any | None
    generation_result : Any | None

    Returns
    -------
    list[QuestionEvalRow]
        Avaliação consolidada por pergunta, na mesma ordem de ``rows``.
    """
    per_question: list[QuestionEvalRow] = []
    pos = 0
    for row, is_cold in zip(rows, cold_flags, strict=True):
        if is_cold:
            qmetrics: dict[str, float | None] = {
                "context_precision": None,
                "context_recall": None,
                "faithfulness": None,
                "answer_relevancy": None,
                "mrr": None,
            }
        else:
            qmetrics = {
                "context_precision": _score_at(
                    retrieval_result, pos, "context_precision"
                ),
                "context_recall": _score_at(retrieval_result, pos, "context_recall"),
                "faithfulness": _score_at(generation_result, pos, "faithfulness"),
                "answer_relevancy": _score_at(
                    generation_result, pos, "answer_relevancy"
                ),
                "mrr": _reciprocal_rank(
                    list(row["contexts"]), list(row["reference_contexts"])
                ),
            }
            pos += 1
        per_question.append(
            QuestionEvalRow(
                question=row["question"],
                contexts=list(row["contexts"]),
                answer=str(row["answer"]),
                is_cold_start=is_cold,
                metrics=qmetrics,
            )
        )
    return per_question


def _build_aggregates(
    retrieval_result: Any,
    generation_result: Any | None,
    rows: list[dict[str, Any]],
    eval_indices: list[int],
) -> dict[str, float | None]:
    """Calcula a média por métrica sobre o subconjunto onde foi avaliada.

    Parameters
    ----------
    retrieval_result : Any
        Resultado do conjunto de retrieval.
    generation_result : Any | None
        Resultado do conjunto de geração (pode ser ``None``).
    rows : list[dict[str, Any]]
        Linhas do dataset (para o MRR customizado).
    eval_indices : list[int]
        Índices das perguntas não-cold-start (subconjunto do MRR).

    Returns
    -------
    dict[str, float | None]
        Média por métrica (``None`` quando a métrica não foi avaliada).
    """
    return {
        "context_precision": _aggregate(retrieval_result, "context_precision"),
        "context_recall": _aggregate(retrieval_result, "context_recall"),
        "faithfulness": _aggregate(generation_result, "faithfulness"),
        "answer_relevancy": _aggregate(generation_result, "answer_relevancy"),
        "mrr": _aggregate_mrr(rows, eval_indices),
    }


def _evaluate_sets(
    rows: list[dict[str, Any]],
    eval_indices: list[int],
    settings: Settings,
    batch_size: int,
) -> tuple[Any | None, Any | None]:
    """Avalia retrieval e geração sobre o mesmo subconjunto não-cold-start.

    Parameters
    ----------
    rows : list[dict[str, Any]]
    eval_indices : list[int]
    settings : Settings
    batch_size : int

    Returns
    -------
    tuple[Any | None, Any | None]
        Resultados de retrieval e de geração (``None`` quando vazios).
    """
    llm = build_eval_llm(settings)
    embeddings = build_eval_embeddings(settings)
    run_config = build_eval_run_config(settings)
    ret_result = _evaluate_retrieval_set(
        rows, eval_indices, llm, embeddings, batch_size, run_config
    )
    gen_result = _evaluate_generation_set(
        rows, eval_indices, llm, embeddings, batch_size, run_config
    )
    return ret_result, gen_result


def evaluate_golden_set(
    questions: list[GoldenQuestion],
    stores: dict[DocType, Any],
    settings: Settings,
    profile: UserProfile | None = None,
    doc_types: list[DocType] | None = None,
    top_k: int | None = None,
    batch_size: int | None = None,
) -> EvaluationReport:
    """Avalia o pipeline RAG sobre um golden set, sem passar pela API HTTP.

    ``retrieve`` usa o mesmo subconjunto de stores do ``run_query``
    (``select_collections``): contexts idênticos à resposta; retrieve explícito
    porque ``GenerationResult`` não os carrega (2 retrievals/pergunta, aceito).
    Roda ``ragas.evaluate`` duas vezes sobre o mesmo subconjunto não-cold-start:
    retrieval (ContextPrecision/Recall) e geração (Faithfulness/Relevancy).

    Parameters
    ----------
    questions : list[GoldenQuestion]
    stores : dict[DocType, Chroma]
    settings : Settings
    profile : UserProfile | None
    doc_types : list[DocType] | None
    top_k : int | None
    batch_size : int | None

    Returns
    -------
    EvaluationReport
    """
    if not questions:
        raise ValueError("lista de perguntas vazia: nada a avaliar")
    if top_k is not None:
        settings = settings.model_copy(update={"retrieval_top_k": top_k})
    batch = batch_size or settings.eval_batch_size

    rows, cold_flags = _collect_rows(questions, stores, settings, profile, doc_types)
    eval_indices = [i for i, flag in enumerate(cold_flags) if not flag]
    ret_result, gen_result = _evaluate_sets(rows, eval_indices, settings, batch)

    return EvaluationReport(
        aggregates=_build_aggregates(ret_result, gen_result, rows, eval_indices),
        per_question=_build_per_question(rows, cold_flags, ret_result, gen_result),
        num_questions=len(rows),
        num_cold_start=sum(cold_flags),
        num_retrieval_evaluated=len(eval_indices),
        num_generation_evaluated=len(eval_indices),
    )
