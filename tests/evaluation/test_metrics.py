from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from langchain_core.documents import Document
from ragas.embeddings import LangchainEmbeddingsWrapper
from ragas.llms import LangchainLLMWrapper

from medasist.config import Settings
from medasist.evaluation.dataset import GoldenQuestion
from medasist.evaluation.metrics import (
    build_eval_embeddings,
    build_eval_llm,
    build_metrics,
)
from medasist.ingestion.schemas import DocType
from medasist.profiles.schemas import UserProfile

_ADMIN_KEY = "test-admin-key-0123456789"


def _settings(**overrides: object) -> Settings:
    return Settings(
        lm_studio_api_key="lm-studio-test",
        admin_api_key=_ADMIN_KEY,
        eval_llm_model="judge-mini",
        eval_embedding_model="judge-embed",
        **overrides,
    )


def _documents(*texts: str) -> list[Document]:
    return [Document(page_content=text) for text in texts]


def _eval_result(scores: list[dict[str, float]]) -> MagicMock:
    result = MagicMock()
    result.scores = scores
    return result


def _make_questions() -> list[GoldenQuestion]:
    return [
        GoldenQuestion(question="Q1", reference_answer="R1", doc_types=[DocType.BULA]),
        GoldenQuestion(
            question="Q2",
            reference_answer="R2",
            is_cold_start=True,
            doc_types=[DocType.PROTOCOLO],
        ),
        GoldenQuestion(question="Q3", reference_answer="R3"),
    ]


class TestBuildEvalLlm:
    def test_points_to_lm_studio_and_uses_eval_model(self, mocker: MagicMock) -> None:
        mock_chat = mocker.patch(
            "medasist.evaluation.metrics.ChatOpenAI",
            return_value=MagicMock(),
        )
        settings = _settings()
        wrapper = build_eval_llm(settings)
        assert isinstance(wrapper, LangchainLLMWrapper)
        call = mock_chat.call_args
        assert call.kwargs["base_url"] == settings.lm_studio_base_url
        assert call.kwargs["api_key"] == settings.lm_studio_api_key.get_secret_value()
        assert call.kwargs["model"] == settings.eval_llm_model
        assert call.kwargs["temperature"] == 0.0


class TestBuildEvalEmbeddings:
    def test_points_to_lm_studio_and_uses_eval_model(self, mocker: MagicMock) -> None:
        mock_emb = mocker.patch(
            "medasist.evaluation.metrics.OpenAIEmbeddings",
            return_value=MagicMock(),
        )
        settings = _settings()
        wrapper = build_eval_embeddings(settings)
        assert isinstance(wrapper, LangchainEmbeddingsWrapper)
        call = mock_emb.call_args
        assert call.kwargs["base_url"] == settings.lm_studio_base_url
        assert call.kwargs["api_key"] == settings.lm_studio_api_key.get_secret_value()
        assert call.kwargs["model"] == settings.eval_embedding_model
        assert call.kwargs["check_embedding_ctx_length"] is False


class TestBuildMetrics:
    def test_returns_four_metrics(self) -> None:
        metrics = build_metrics()
        assert len(metrics) == 4
        names = {m.name for m in metrics}
        assert names == {
            "context_precision",
            "context_recall",
            "faithfulness",
            "answer_relevancy",
        }


class TestReciprocalRank:
    def _rr(self, contexts: list[str], reference: list[str]) -> float:
        from medasist.evaluation.metrics import _reciprocal_rank

        return _reciprocal_rank(contexts, reference)

    def test_first_reference_hit_at_rank_one(self) -> None:
        assert self._rr(["A", "B", "C"], ["A", "C"]) == pytest.approx(1.0)

    def test_first_reference_hit_at_later_rank(self) -> None:
        assert self._rr(["A", "B", "C"], ["C"]) == pytest.approx(1.0 / 3)

    def test_zero_when_no_reference_hit(self) -> None:
        assert self._rr(["A", "B", "C"], ["X", "Y"]) == 0.0

    def test_zero_when_contexts_empty(self) -> None:
        assert self._rr([], ["A"]) == 0.0

    def test_uses_first_hit_position_not_count(self) -> None:
        # 'B' aparece na posição 2 → 1/2, mesmo havendo vários hits depois
        assert self._rr(["A", "B", "B"], ["B"]) == pytest.approx(0.5)


class TestAggregateMrr:
    def _agg(self, rows: list[dict], eval_indices: list[int]) -> float | None:
        from medasist.evaluation.metrics import _aggregate_mrr

        return _aggregate_mrr(rows, eval_indices)

    def test_mean_over_non_cold_start_subset(self) -> None:
        rows = [
            {"contexts": ["A"], "reference_contexts": ["A"]},  # RR = 1.0
            {"contexts": ["X"], "reference_contexts": ["A"]},  # RR = 0.0 (cold)
            {"contexts": ["X", "B"], "reference_contexts": ["B"]},  # RR = 0.5
        ]
        # Subconjunto não-cold-start: índices 0 e 2 → média (1.0 + 0.5)/2
        assert self._agg(rows, [0, 2]) == pytest.approx(0.75)

    def test_returns_none_when_no_eval_indices(self) -> None:
        assert self._agg([{"contexts": ["A"], "reference_contexts": ["A"]}], []) is None


class TestEvaluateGoldenSet:
    def test_partitions_cold_start_and_reports_counts(self, mocker: MagicMock) -> None:
        from medasist.evaluation.metrics import evaluate_golden_set

        mocker.patch(
            "medasist.evaluation.metrics.retrieve",
            side_effect=lambda query, stores, settings: _documents(f"ctx-{query}"),
        )
        mocker.patch(
            "medasist.evaluation.metrics.run_query",
            side_effect=[
                SimpleNamespace(answer="A1", is_cold_start=False),
                SimpleNamespace(answer="C2", is_cold_start=True),
                SimpleNamespace(answer="A3", is_cold_start=False),
            ],
        )
        calls: dict[str, object] = {}

        def _fake_evaluate(dataset, metrics=None, **kwargs):
            names = {m.name for m in (metrics or [])}
            calls["column_map"] = kwargs.get("column_map")
            calls["batch_size"] = kwargs.get("batch_size")
            if names == {"context_precision", "context_recall"}:
                per_row = [
                    {"context_precision": 1.0, "context_recall": 0.8},
                    {"context_precision": 0.5, "context_recall": 0.4},
                ]
                return _eval_result(per_row[: len(dataset)])
            return _eval_result(
                [
                    {"faithfulness": 0.9, "answer_relevancy": 0.7},
                    {"faithfulness": 0.8, "answer_relevancy": 0.6},
                ]
            )

        mocker.patch(
            "medasist.evaluation.metrics.evaluate",
            side_effect=_fake_evaluate,
        )

        settings = _settings(eval_batch_size=8)
        report = evaluate_golden_set(_make_questions(), stores={}, settings=settings)

        assert report.num_questions == 3
        assert report.num_cold_start == 1
        assert report.num_generation_evaluated == 2
        assert report.num_retrieval_evaluated == 2
        assert report.aggregates["context_precision"] == pytest.approx(0.75)
        assert report.aggregates["context_recall"] == pytest.approx(0.6)
        assert report.aggregates["faithfulness"] == pytest.approx(0.85)
        assert report.aggregates["answer_relevancy"] == pytest.approx(0.65)
        assert report.aggregates["mrr"] == pytest.approx(0.0)
        assert calls["column_map"] == {"reference": "reference_answer"}
        assert calls["batch_size"] == 8

        cold_row = report.per_question[1]
        assert cold_row.question == "Q2"
        assert cold_row.is_cold_start is True
        assert cold_row.answer == "C2"
        assert cold_row.contexts == ["ctx-Q2"]
        assert cold_row.metrics["context_precision"] is None
        assert cold_row.metrics["context_recall"] is None
        assert cold_row.metrics["faithfulness"] is None
        assert cold_row.metrics["answer_relevancy"] is None
        assert cold_row.metrics["mrr"] is None

        normal_row = report.per_question[0]
        assert normal_row.is_cold_start is False
        assert normal_row.metrics["context_precision"] == pytest.approx(1.0)
        assert normal_row.metrics["context_recall"] == pytest.approx(0.8)
        assert normal_row.metrics["faithfulness"] == pytest.approx(0.9)
        assert normal_row.metrics["answer_relevancy"] == pytest.approx(0.7)

    def test_mrr_present_in_aggregates_and_per_question(
        self, mocker: MagicMock
    ) -> None:
        from medasist.evaluation.metrics import evaluate_golden_set

        mocker.patch(
            "medasist.evaluation.metrics.retrieve",
            side_effect=lambda query, stores, settings: (
                _documents("ctx-A") if query == "Q1" else _documents("ctx-X")
            ),
        )
        mocker.patch(
            "medasist.evaluation.metrics.run_query",
            side_effect=[
                SimpleNamespace(answer="A1", is_cold_start=False),
                SimpleNamespace(answer="A2", is_cold_start=False),
            ],
        )
        mocker.patch(
            "medasist.evaluation.metrics.evaluate",
            side_effect=[
                _eval_result(
                    [
                        {"context_precision": 1.0, "context_recall": 1.0},
                        {"context_precision": 0.5, "context_recall": 0.5},
                    ]
                ),
                _eval_result(
                    [
                        {"faithfulness": 1.0, "answer_relevancy": 1.0},
                        {"faithfulness": 0.5, "answer_relevancy": 0.5},
                    ]
                ),
            ],
        )
        questions = [
            GoldenQuestion(
                question="Q1",
                reference_answer="R1",
                reference_contexts=["ctx-A"],
            ),
            GoldenQuestion(
                question="Q2",
                reference_answer="R2",
                reference_contexts=["ctx-B"],
            ),
        ]
        report = evaluate_golden_set(questions, stores={}, settings=_settings())

        # Q1: ctx-A no rank 1 → RR 1.0; Q2: ctx-B ausente → RR 0.0; média 0.5
        assert report.aggregates["mrr"] == pytest.approx(0.5)
        assert report.per_question[0].metrics["mrr"] == pytest.approx(1.0)
        assert report.per_question[1].metrics["mrr"] == pytest.approx(0.0)

    def test_run_query_called_with_per_question_profile_and_doc_types(
        self, mocker: MagicMock
    ) -> None:
        from medasist.evaluation.metrics import evaluate_golden_set

        mocker.patch(
            "medasist.evaluation.metrics.retrieve",
            return_value=_documents("ctx"),
        )
        mock_run_query = mocker.patch(
            "medasist.evaluation.metrics.run_query",
            side_effect=[
                SimpleNamespace(answer="A1", is_cold_start=False),
                SimpleNamespace(answer="A2", is_cold_start=False),
            ],
        )
        mocker.patch(
            "medasist.evaluation.metrics.evaluate",
            side_effect=[
                _eval_result(
                    [
                        {"context_precision": 1.0, "context_recall": 1.0},
                        {"context_precision": 1.0, "context_recall": 1.0},
                    ]
                ),
                _eval_result(
                    [
                        {"faithfulness": 1.0, "answer_relevancy": 1.0},
                        {"faithfulness": 1.0, "answer_relevancy": 1.0},
                    ]
                ),
            ],
        )

        questions = [
            GoldenQuestion(
                question="Q1",
                reference_answer="R1",
                profile=UserProfile.ENFERMEIRO,
                doc_types=[DocType.BULA],
            ),
            GoldenQuestion(question="Q2", reference_answer="R2"),
        ]
        evaluate_golden_set(questions, stores={}, settings=_settings())

        assert mock_run_query.call_count == 2
        first_args = mock_run_query.call_args_list[0].args
        assert first_args[0] == "Q1"
        assert first_args[2] == UserProfile.ENFERMEIRO
        assert first_args[4] == [DocType.BULA]
        second_args = mock_run_query.call_args_list[1].args
        assert second_args[2] == UserProfile.MEDICO
        assert second_args[4] is None

    def test_cli_doc_types_override_per_question(self, mocker: MagicMock) -> None:
        from medasist.evaluation.metrics import evaluate_golden_set

        stores = {DocType.MANUAL: MagicMock()}
        mock_retrieve = mocker.patch(
            "medasist.evaluation.metrics.retrieve",
            return_value=_documents("ctx"),
        )
        mock_run_query = mocker.patch(
            "medasist.evaluation.metrics.run_query",
            return_value=SimpleNamespace(answer="A", is_cold_start=False),
        )
        mocker.patch(
            "medasist.evaluation.metrics.evaluate",
            return_value=_eval_result(
                [{"context_precision": 1.0, "context_recall": 1.0}]
            ),
        )
        questions = [
            GoldenQuestion(
                question="Q1",
                reference_answer="R1",
                doc_types=[DocType.BULA],
            )
        ]
        evaluate_golden_set(
            questions,
            stores,
            settings=_settings(),
            profile=UserProfile.PACIENTE,
            doc_types=[DocType.MANUAL],
        )
        args = mock_run_query.call_args_list[0].args
        assert args[2] == UserProfile.PACIENTE
        assert args[4] == [DocType.MANUAL]
        ret_args = mock_retrieve.call_args_list[0].args
        assert ret_args[1] == {DocType.MANUAL: stores[DocType.MANUAL]}

    def test_retrieve_uses_same_subset_as_run_query(self, mocker: MagicMock) -> None:
        from medasist.evaluation.metrics import evaluate_golden_set

        stores = {DocType.BULA: MagicMock(), DocType.MANUAL: MagicMock()}
        mock_retrieve = mocker.patch(
            "medasist.evaluation.metrics.retrieve",
            return_value=_documents("ctx"),
        )
        mock_run_query = mocker.patch(
            "medasist.evaluation.metrics.run_query",
            return_value=SimpleNamespace(answer="A", is_cold_start=False),
        )
        mocker.patch(
            "medasist.evaluation.metrics.evaluate",
            return_value=_eval_result(
                [{"context_precision": 1.0, "context_recall": 1.0}]
            ),
        )
        questions = [
            GoldenQuestion(
                question="Q1", reference_answer="R1", doc_types=[DocType.BULA]
            ),
            GoldenQuestion(question="Q2", reference_answer="R2"),
        ]
        evaluate_golden_set(questions, stores, settings=_settings())

        subset_args = mock_retrieve.call_args_list[0].args
        assert subset_args[1] == {DocType.BULA: stores[DocType.BULA]}
        assert subset_args[1] is not stores
        all_args = mock_retrieve.call_args_list[1].args
        assert all_args[1] is stores

        run_subset = mock_run_query.call_args_list[0].args
        assert run_subset[4] == [DocType.BULA]
        assert run_subset[1] is stores

    def test_top_k_override_is_propagated_via_model_copy(
        self, mocker: MagicMock
    ) -> None:
        from medasist.evaluation.metrics import evaluate_golden_set

        mock_retrieve = mocker.patch(
            "medasist.evaluation.metrics.retrieve",
            return_value=_documents("ctx"),
        )
        mocker.patch(
            "medasist.evaluation.metrics.run_query",
            return_value=SimpleNamespace(answer="A", is_cold_start=False),
        )
        mocker.patch(
            "medasist.evaluation.metrics.evaluate",
            return_value=_eval_result(
                [{"context_precision": 1.0, "context_recall": 1.0}]
            ),
        )
        questions = [GoldenQuestion(question="Q1", reference_answer="R1")]
        settings = _settings(retrieval_top_k=10)
        evaluate_golden_set(
            questions,
            stores={},
            settings=settings,
            top_k=3,
        )
        passed_settings = mock_retrieve.call_args_list[0].args[2]
        assert passed_settings.retrieval_top_k == 3
        assert settings.retrieval_top_k == 10

    def test_all_cold_start_generation_set_empty(self, mocker: MagicMock) -> None:
        from medasist.evaluation.metrics import evaluate_golden_set

        mocker.patch(
            "medasist.evaluation.metrics.retrieve",
            return_value=[],
        )
        mocker.patch(
            "medasist.evaluation.metrics.run_query",
            return_value=SimpleNamespace(answer="cold", is_cold_start=True),
        )
        mock_evaluate = mocker.patch(
            "medasist.evaluation.metrics.evaluate",
            return_value=_eval_result(
                [{"context_precision": 0.0, "context_recall": 0.0}]
            ),
        )
        questions = [
            GoldenQuestion(question="Q1", reference_answer="R1", is_cold_start=True),
            GoldenQuestion(question="Q2", reference_answer="R2", is_cold_start=True),
        ]
        report = evaluate_golden_set(questions, stores={}, settings=_settings())

        assert mock_evaluate.call_count == 0
        assert report.num_cold_start == 2
        assert report.num_generation_evaluated == 0
        assert report.num_retrieval_evaluated == 0
        assert report.aggregates["context_precision"] is None
        assert report.aggregates["context_recall"] is None
        assert report.aggregates["faithfulness"] is None
        assert report.aggregates["answer_relevancy"] is None
        for row in report.per_question:
            assert row.metrics["context_precision"] is None
            assert row.metrics["context_recall"] is None
            assert row.metrics["faithfulness"] is None
            assert row.metrics["answer_relevancy"] is None

    def test_empty_questions_raises_value_error(self, mocker: MagicMock) -> None:
        from medasist.evaluation.metrics import evaluate_golden_set

        with pytest.raises(ValueError, match="vazia"):
            evaluate_golden_set([], stores={}, settings=_settings())


class TestPackageExports:
    def test_public_api_importable(self) -> None:
        from medasist.evaluation import (
            EvaluationReport,
            GoldenQuestion,
            GoldenSet,
            QuestionEvalRow,
            build_eval_dataset,
            build_eval_embeddings,
            build_eval_llm,
            build_metrics,
            evaluate_golden_set,
            load_golden_set,
        )

        assert build_eval_llm and evaluate_golden_set and load_golden_set
        assert build_eval_embeddings and build_metrics and build_eval_dataset
        assert GoldenQuestion and GoldenSet and QuestionEvalRow and EvaluationReport
