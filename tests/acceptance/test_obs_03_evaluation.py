"""Acceptance tests for OBS-03 (Avaliação RAG — RAGAS).

Executa o pipeline de avaliação de ponta a ponta patcheando **apenas
boundaries**, no padrão de ``tests/acceptance/test_obs_02_health_check.py``:
``ragas.evaluate`` (via ``medasist.evaluation.metrics.evaluate``), o probe do
LM Studio (``httpx.get``), ``httpx.post`` (prova de que a API HTTP não é
usada) e os construtores de LLM/embeddings (``ChatOpenAI``/``OpenAIEmbeddings``
— a rede nunca é tocada). As stores ChromaDB são reais e efêmeras com
``_FakeEmbeddings`` (padrão de ``tests/retrieval/test_retriever.py``) e o
golden set é o versionado ``evals/dataset/golden_set.json`` ou cópias
sintéticas em ``tmp_path``. Nenhum arquivo de ``src/`` é modificado. Dados
100% sintéticos — sem dado real de paciente.

Cobertura por critério de aceitação:
- CA-01: ``load_golden_set`` sobre o golden set versionado carrega sem erro e
  ``build_eval_dataset`` converte via ``datasets.Dataset.from_list``; registro
  fora do padrão → ``ValueError`` descritivo com campo + índice.
- CA-02: ``evaluate_rag.main`` com probe do LM Studio mockado, stores reais
  efêmeras e ``ragas.evaluate`` mockado → imprime agregadas + por pergunta,
  usa as 4 métricas e sai com código 0.
- CA-03: ``build_eval_llm``/``build_eval_embeddings`` apontam para
  ``lm_studio_base_url`` (construtores mockados — nenhum endpoint externo);
  os wrappers RAGAS são injetados em ``ragas.evaluate``.
- CA-04: probe do LM Studio levantando ``httpx.ConnectError`` → ``main``
  retorna != 0, ``evaluate_golden_set`` não é chamada e o timeout
  configurado é respeitado (fail-fast, sem timeout longo).
- CA-05: stores reais efêmeras + 1 doc relevante + pergunta fora do corpus
  (cold start sinalizada pelo boundary do pipeline) → ``num_cold_start >= 1``
  e a pergunta cold start excluída de Faithfulness/AnswerRelevancy.
- CA-06: código de saída 0/!=0 em: dataset inválido, coleção vazia, LM Studio
  fora e sucesso.
- CA-07: a avaliação resolve cada pergunta via ``retrieve``/``run_query``
  direto (spies) sobre os vectorstores locais — sem TestClient, sem
  ``POST /query``.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import chromadb
import httpx
import pytest
from datasets import Dataset
from langchain_core.embeddings import Embeddings
from ragas.embeddings import LangchainEmbeddingsWrapper
from ragas.llms import LangchainLLMWrapper

from medasist.config import Settings
from medasist.evaluation import (
    GoldenQuestion,
    GoldenSet,
    build_eval_dataset,
    build_eval_embeddings,
    build_eval_llm,
    evaluate_golden_set,
    load_golden_set,
)
from medasist.evaluation import metrics as eval_metrics
from medasist.ingestion.schemas import DocType
from medasist.vectorstore.store import get_vectorstore

logger = logging.getLogger(__name__)

_ADMIN_KEY = "test-admin-key-0123456789"
_GOLDEN_PATH = Path("evals/dataset/golden_set.json")


def _settings(**overrides: object) -> Settings:
    """Settings de teste com admin key forte e overrides pontuais.

    Parameters
    ----------
    **overrides
        Campos de Settings a sobrescrever (ex: ``healthcheck_timeout``).

    Returns
    -------
    Settings
        Configurações isoladas para o teste.
    """
    return Settings(
        lm_studio_api_key="lm-studio-test",
        admin_api_key=_ADMIN_KEY,
        **overrides,
    )


class _FakeEmbeddings(Embeddings):
    """Embeddings fake com vetores distintos para que a busca funcione."""

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[float(i % 10) * 0.1 + 0.1] * 4 for i, _ in enumerate(texts)]

    def embed_query(self, text: str) -> list[float]:
        return [0.1, 0.1, 0.1, 0.1]


def _seed_bula_store(
    client: chromadb.ClientAPI,
    embeddings: Embeddings,
    settings: Settings,
) -> dict[DocType, object]:
    """Seeda um vectorstore real de BULA com 1 documento sintético.

    Parameters
    ----------
    client : chromadb.ClientAPI
        Cliente ChromaDB efêmero/persistente isolado.
    embeddings : Embeddings
        Função de embedding fake.
    settings : Settings
        Configurações com os nomes das coleções.

    Returns
    -------
    dict[DocType, object]
        Dicionário ``DocType.BULA → Chroma`` com 1 documento indexado.
    """
    store = get_vectorstore(DocType.BULA, client, embeddings, settings)
    store.add_texts(
        texts=[
            "Alphazol X: dose inicial de 10 mg/dia para hipertensão "
            "arterial sistêmica em adultos."
        ],
        metadatas=[{"doc_type": "bula", "source": "alphazol.pdf"}],
        ids=["bula_001"],
    )
    return {DocType.BULA: store}


def _write_golden(path: Path) -> Path:
    """Grava um golden set sintético válido em ``path``.

    Parameters
    ----------
    path : Path
        Caminho de destino do JSON.

    Returns
    -------
    Path
        O mesmo caminho, já gravado.
    """
    payload = {
        "version": "1.0.0",
        "description": "golden set sintético de teste",
        "questions": [
            {
                "question": "Qual a dose inicial recomendada de Alphazol para "
                "adultos com hipertensão?",
                "reference_answer": "A dose inicial de Alphazol é 10 mg/dia.",
                "reference_contexts": ["Alphazol X: 10 mg/dia."],
                "doc_types": ["bula"],
                "profile": "medico",
                "is_cold_start": False,
            },
            {
                "question": "Como tratar pneumonia fúngica em camaleões?",
                "reference_answer": "Fora do corpus; orientar consulta profissional.",
                "reference_contexts": [],
                "doc_types": [],
                "profile": "medico",
                "is_cold_start": True,
            },
        ],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _fake_run_query(
    question: str,
    stores: object,
    profile: object,
    settings: object,
    doc_types: object = None,
) -> SimpleNamespace:
    """Boundary fake do pipeline: cold start para tema fora do corpus."""
    if "camaleões" in question:
        return SimpleNamespace(
            answer="Não encontrei essa informação nos documentos disponíveis.",
            is_cold_start=True,
        )
    return SimpleNamespace(
        answer="A dose inicial de Alphazol é 10 mg/dia [1].",
        is_cold_start=False,
    )


def _fake_ragas_evaluate() -> tuple[object, list]:
    """Boundary fake de ``ragas.evaluate`` com scores uniformes.

    Returns
    -------
    tuple[object, list]
        Função fake e lista de chamadas ``(dataset, nomes de métricas, llm,
        embeddings)`` para asserts.
    """
    calls: list = []

    def _evaluate(dataset, metrics=None, llm=None, embeddings=None, **kwargs):
        names = [m.name for m in (metrics or [])]
        calls.append((dataset, names, llm, embeddings))
        scores = [dict.fromkeys(names, 0.85) for _ in range(len(dataset))]
        return SimpleNamespace(scores=scores)

    return _evaluate, calls


def _golden_questions() -> list[GoldenQuestion]:
    """Duas perguntas sintéticas: uma no corpus, uma fora (cold start)."""
    return [
        GoldenQuestion(
            question="Qual a dose inicial recomendada de Alphazol para adultos "
            "com hipertensão?",
            reference_answer="10 mg/dia",
            doc_types=[DocType.BULA],
        ),
        GoldenQuestion(
            question="Como tratar pneumonia fúngica em camaleões?",
            reference_answer="fora do corpus",
        ),
    ]


class TestCA01GoldenSetLoads:
    """CA-01: golden set carregável via datasets.Dataset.from_list."""

    def test_ca01_golden_set_loads(self) -> None:
        """CA-01: golden set versionado carrega e vira Dataset sem erro."""
        golden = load_golden_set(_GOLDEN_PATH)

        assert isinstance(golden, GoldenSet)
        assert golden.version == "1.0.0"
        assert len(golden.questions) >= 1

        dataset = build_eval_dataset(golden.questions)
        assert isinstance(dataset, Dataset)
        assert len(dataset) == len(golden.questions)
        assert set(dataset.column_names) == {
            "question",
            "contexts",
            "reference_answer",
            "reference_contexts",
            "is_cold_start",
        }
        assert any(dataset["is_cold_start"])

    def test_ca01_invalid_schema_reports_field_and_index(self, tmp_path: Path) -> None:
        """CA-01: registro fora do padrão → erro descritivo com campo + índice."""
        bad = tmp_path / "blank_question.json"
        bad.write_text(
            json.dumps(
                {
                    "version": "1.0.0",
                    "description": "inválido",
                    "questions": [
                        {"question": "pergunta ok", "reference_answer": "resposta"},
                        {"question": "   ", "reference_answer": "resposta"},
                    ],
                }
            ),
            encoding="utf-8",
        )

        with pytest.raises(ValueError, match="pergunta 2: question não pode ser vazio"):
            load_golden_set(bad)

    def test_ca01_invalid_doc_type_reports_value(self, tmp_path: Path) -> None:
        """CA-01: doc_types fora do enum → erro citando o valor inválido."""
        bad = tmp_path / "bad_doc_type.json"
        bad.write_text(
            json.dumps(
                {
                    "version": "1.0.0",
                    "description": "inválido",
                    "questions": [
                        {
                            "question": "pergunta ok",
                            "reference_answer": "resposta",
                            "doc_types": ["nota-fiscal"],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

        with pytest.raises(ValueError, match="doc_types contém 'nota-fiscal'"):
            load_golden_set(bad)

    def test_ca01_malformed_json_reports_path(self, tmp_path: Path) -> None:
        """CA-01: JSON malformado → erro claro com o caminho do arquivo."""
        bad = tmp_path / "malformed.json"
        bad.write_text("{not json", encoding="utf-8")

        with pytest.raises(ValueError, match="golden set malformado"):
            load_golden_set(bad)


class TestCA02CliRunsEvaluation:
    """CA-02: CLI roda ragas.evaluate com as 4 métricas e sai com código 0."""

    def test_ca02_cli_evaluates_golden_set_and_exits_zero(
        self, mocker: MagicMock, tmp_path: Path, capsys: object
    ) -> None:
        settings = _settings(lm_studio_base_url="http://lm-studio-test/v1")
        golden = _write_golden(tmp_path / "golden.json")

        mocker.patch("evaluate_rag.get_settings", return_value=settings)
        mocker.patch(
            "evaluate_rag.httpx.get",
            return_value=SimpleNamespace(status_code=200),
        )
        mocker.patch(
            "evaluate_rag.httpx.post",
            side_effect=AssertionError("CA-02: API HTTP não deve ser chamada"),
        )
        client = chromadb.PersistentClient(path=str(tmp_path / "chroma"))
        stores = _seed_bula_store(client, _FakeEmbeddings(), settings)
        mocker.patch("evaluate_rag.get_client", return_value=client)
        mocker.patch("evaluate_rag.build_embeddings", return_value=_FakeEmbeddings())
        mocker.patch("evaluate_rag.get_all_vectorstores", return_value=stores)

        mocker.patch(
            "medasist.evaluation.metrics.run_query",
            side_effect=_fake_run_query,
        )
        fake_evaluate, eval_calls = _fake_ragas_evaluate()
        mocker.patch("medasist.evaluation.metrics.evaluate", side_effect=fake_evaluate)
        mock_chat = mocker.patch(
            "medasist.evaluation.metrics.ChatOpenAI", return_value=MagicMock()
        )
        mock_emb = mocker.patch(
            "medasist.evaluation.metrics.OpenAIEmbeddings", return_value=MagicMock()
        )

        from evaluate_rag import main

        result = main(["--dataset", str(golden)])

        assert result == 0
        out = capsys.readouterr().out
        assert "Avaliação RAG (RAGAS 0.2.15) — agregadas" in out
        assert "context_precision" in out
        assert "answer_relevancy" in out
        assert "Por pergunta" in out
        assert "cold start" in out

        assert len(eval_calls) == 2
        all_names = [name for _, names, _, _ in eval_calls for name in names]
        assert {
            "context_precision",
            "context_recall",
            "faithfulness",
            "answer_relevancy",
        } <= set(all_names)
        for _, _names, llm, embeddings in eval_calls:
            assert isinstance(llm, LangchainLLMWrapper)
            assert isinstance(embeddings, LangchainEmbeddingsWrapper)
        assert mock_chat.call_args.kwargs["base_url"] == settings.lm_studio_base_url
        assert mock_emb.call_args.kwargs["base_url"] == settings.lm_studio_base_url


class TestCA03JudgeOffline:
    """CA-03: judge e embeddings apontam exclusivamente para o LM Studio."""

    def test_ca03_eval_llm_points_to_lm_studio(self, mocker: MagicMock) -> None:
        mock_chat = mocker.patch(
            "medasist.evaluation.metrics.ChatOpenAI",
            return_value=MagicMock(),
        )
        settings = _settings(eval_llm_model="judge-mini")

        wrapper = build_eval_llm(settings)

        assert isinstance(wrapper, LangchainLLMWrapper)
        assert wrapper.langchain_llm is mock_chat.return_value
        kwargs = mock_chat.call_args.kwargs
        assert kwargs["base_url"] == settings.lm_studio_base_url
        assert kwargs["base_url"] != "https://api.openai.com/v1"
        assert kwargs["model"] == settings.eval_llm_model
        assert kwargs["temperature"] == 0.0

    def test_ca03_eval_embeddings_point_to_lm_studio(self, mocker: MagicMock) -> None:
        mock_emb = mocker.patch(
            "medasist.evaluation.metrics.OpenAIEmbeddings",
            return_value=MagicMock(),
        )
        settings = _settings(eval_embedding_model="judge-embed")

        wrapper = build_eval_embeddings(settings)

        assert isinstance(wrapper, LangchainEmbeddingsWrapper)
        assert wrapper.embeddings is mock_emb.return_value
        kwargs = mock_emb.call_args.kwargs
        assert kwargs["base_url"] == settings.lm_studio_base_url
        assert kwargs["base_url"] != "https://api.openai.com/v1"
        assert kwargs["model"] == settings.eval_embedding_model
        assert kwargs["check_embedding_ctx_length"] is False


class TestCA04FailFast:
    """CA-04: LM Studio indisponível → fail-fast sem timeout longo."""

    def test_ca04_lm_studio_down_fails_fast_before_eval(
        self, mocker: MagicMock, tmp_path: Path, caplog: object
    ) -> None:
        settings = _settings(
            lm_studio_base_url="http://lm-studio-test/v1",
            healthcheck_timeout=1.0,
        )
        golden = _write_golden(tmp_path / "golden.json")
        mocker.patch("evaluate_rag.get_settings", return_value=settings)
        captured: dict[str, object] = {}

        def _boom(*args: object, **kwargs: object) -> None:
            captured["timeout"] = kwargs.get("timeout")
            raise httpx.ConnectError("LM Studio fora do ar")

        mocker.patch("evaluate_rag.httpx.get", side_effect=_boom)
        mock_eval = mocker.patch("evaluate_rag.evaluate_golden_set")

        from evaluate_rag import main

        started = time.perf_counter()
        with caplog.at_level(logging.ERROR, logger="evaluate_rag"):
            result = main(["--dataset", str(golden)])
        elapsed = time.perf_counter() - started

        assert result != 0
        assert mock_eval.call_count == 0
        assert captured["timeout"] == settings.healthcheck_timeout
        assert elapsed < 3.0, f"probe levou {elapsed:.2f}s além do tolerável"
        assert "LM Studio inacessível" in caplog.text


class TestCA05ColdStart:
    """CA-05: perguntas cold start sinalizadas e excluídas da geração."""

    def test_ca05_cold_start_excluded_from_generation_metrics(
        self,
        mocker: MagicMock,
        tmp_path: Path,
        settings: Settings,
    ) -> None:
        client = chromadb.PersistentClient(path=str(tmp_path / "chroma"))
        stores = _seed_bula_store(client, _FakeEmbeddings(), settings)
        spy_retrieve = mocker.spy(eval_metrics, "retrieve")
        mocker.patch(
            "medasist.evaluation.metrics.run_query",
            side_effect=_fake_run_query,
        )
        fake_evaluate, eval_calls = _fake_ragas_evaluate()
        mocker.patch("medasist.evaluation.metrics.evaluate", side_effect=fake_evaluate)
        mocker.patch(
            "medasist.evaluation.metrics.build_eval_llm",
            return_value=MagicMock(),
        )
        mocker.patch(
            "medasist.evaluation.metrics.build_eval_embeddings",
            return_value=MagicMock(),
        )

        questions = _golden_questions()
        report = evaluate_golden_set(questions, stores, settings)

        assert report.num_questions == 2
        assert report.num_cold_start >= 1
        assert report.num_generation_evaluated == (
            report.num_questions - report.num_cold_start
        )
        assert spy_retrieve.call_count == len(questions)
        first_args = spy_retrieve.call_args_list[0].args
        assert first_args[1] == {DocType.BULA: stores[DocType.BULA]}
        second_args = spy_retrieve.call_args_list[1].args
        assert second_args[1] is stores

        cold_rows = [r for r in report.per_question if r.is_cold_start]
        assert cold_rows
        for row in cold_rows:
            assert row.metrics["faithfulness"] is None
            assert row.metrics["answer_relevancy"] is None
            assert row.metrics["context_precision"] is not None

        names_by_call = [set(names) for _, names, _, _ in eval_calls]
        assert {"context_precision", "context_recall"} in names_by_call
        assert {"faithfulness", "answer_relevancy"} in names_by_call

        retrieval_dataset = eval_calls[0][0]
        assert len(retrieval_dataset) == report.num_questions
        gen_dataset, _, _, _ = eval_calls[1]
        assert len(gen_dataset) == report.num_generation_evaluated
        assert (
            "Como tratar pneumonia fúngica em camaleões?" not in gen_dataset["question"]
        )


class TestCA06ExitCodes:
    """CA-06: código de saída 0 em sucesso, != 0 em qualquer falha."""

    def test_ca06_invalid_dataset_exits_nonzero(
        self, mocker: MagicMock, tmp_path: Path
    ) -> None:
        mocker.patch("evaluate_rag.get_settings", return_value=_settings())
        bad = tmp_path / "bad.json"
        bad.write_text("{not json", encoding="utf-8")
        mock_eval = mocker.patch("evaluate_rag.evaluate_golden_set")

        from evaluate_rag import main

        assert main(["--dataset", str(bad)]) != 0
        mock_eval.assert_not_called()

    def test_ca06_empty_collection_exits_nonzero(
        self, mocker: MagicMock, tmp_path: Path
    ) -> None:
        mocker.patch("evaluate_rag.get_settings", return_value=_settings())
        golden = _write_golden(tmp_path / "golden.json")
        mocker.patch(
            "evaluate_rag.httpx.get",
            return_value=SimpleNamespace(status_code=200),
        )
        mocker.patch("evaluate_rag.get_client", return_value=MagicMock())
        mocker.patch("evaluate_rag.build_embeddings", return_value=MagicMock())
        empty = MagicMock()
        empty._collection.count.return_value = 0
        mocker.patch(
            "evaluate_rag.get_all_vectorstores",
            return_value={DocType.BULA: empty},
        )
        mock_eval = mocker.patch("evaluate_rag.evaluate_golden_set")

        from evaluate_rag import main

        assert main(["--dataset", str(golden)]) != 0
        mock_eval.assert_not_called()

    def test_ca06_lm_studio_down_exits_nonzero(
        self, mocker: MagicMock, tmp_path: Path
    ) -> None:
        mocker.patch("evaluate_rag.get_settings", return_value=_settings())
        golden = _write_golden(tmp_path / "golden.json")
        mocker.patch(
            "evaluate_rag.httpx.get",
            side_effect=httpx.ConnectError("LM Studio fora do ar"),
        )
        mock_eval = mocker.patch("evaluate_rag.evaluate_golden_set")

        from evaluate_rag import main

        assert main(["--dataset", str(golden)]) != 0
        mock_eval.assert_not_called()

    def test_ca06_success_exits_zero(self, mocker: MagicMock, tmp_path: Path) -> None:
        mocker.patch(
            "evaluate_rag.get_settings",
            return_value=_settings(lm_studio_base_url="http://lm-studio-test/v1"),
        )
        golden = _write_golden(tmp_path / "golden.json")
        mocker.patch(
            "evaluate_rag.httpx.get",
            return_value=SimpleNamespace(status_code=200),
        )
        mocker.patch("evaluate_rag.get_client", return_value=MagicMock())
        mocker.patch("evaluate_rag.build_embeddings", return_value=MagicMock())
        store = MagicMock()
        store._collection.count.return_value = 3
        mocker.patch(
            "evaluate_rag.get_all_vectorstores",
            return_value={DocType.BULA: store},
        )
        report = eval_metrics.EvaluationReport(
            aggregates={"context_precision": 0.7},
            per_question=[],
            num_questions=2,
            num_cold_start=1,
            num_generation_evaluated=1,
        )
        mocker.patch("evaluate_rag.evaluate_golden_set", return_value=report)

        from evaluate_rag import main

        assert main(["--dataset", str(golden)]) == 0


class TestCA07DirectPipeline:
    """CA-07: avaliação via retrieve/run_query direto, sem API HTTP."""

    def test_ca07_eval_uses_retrieve_and_run_query_not_http_api(
        self,
        mocker: MagicMock,
        tmp_path: Path,
        settings: Settings,
    ) -> None:
        client = chromadb.PersistentClient(path=str(tmp_path / "chroma"))
        stores = _seed_bula_store(client, _FakeEmbeddings(), settings)
        spy_retrieve = mocker.spy(eval_metrics, "retrieve")
        mock_run_query = mocker.patch(
            "medasist.evaluation.metrics.run_query",
            return_value=SimpleNamespace(
                answer="Alphazol: 10 mg/dia [1]",
                is_cold_start=False,
            ),
        )
        fake_evaluate, _ = _fake_ragas_evaluate()
        mocker.patch("medasist.evaluation.metrics.evaluate", side_effect=fake_evaluate)
        mocker.patch(
            "medasist.evaluation.metrics.build_eval_llm",
            return_value=MagicMock(),
        )
        mocker.patch(
            "medasist.evaluation.metrics.build_eval_embeddings",
            return_value=MagicMock(),
        )
        mocker.patch(
            "httpx.post",
            side_effect=AssertionError("CA-07: nenhum POST HTTP na avaliação"),
        )

        questions = [
            GoldenQuestion(
                question="Qual a dose inicial de Alphazol?",
                reference_answer="10 mg/dia",
                doc_types=[DocType.BULA],
            )
        ]
        report = evaluate_golden_set(questions, stores, settings)

        assert spy_retrieve.call_count == 1
        ret_args = spy_retrieve.call_args_list[0].args
        assert ret_args[0] == questions[0].question
        assert ret_args[1] == {DocType.BULA: stores[DocType.BULA]}
        assert mock_run_query.call_count == 1
        rq_args = mock_run_query.call_args_list[0].args
        assert rq_args[0] == questions[0].question
        assert rq_args[1] is stores

        # A resposta veio do run_query (pipeline), não de POST /query...
        assert report.per_question[0].answer == "Alphazol: 10 mg/dia [1]"
        # ...e os contexts vieram do retrieve real sobre o vectorstore local.
        assert report.per_question[0].contexts == [
            "Alphazol X: dose inicial de 10 mg/dia para hipertensão arterial "
            "sistêmica em adultos."
        ]
