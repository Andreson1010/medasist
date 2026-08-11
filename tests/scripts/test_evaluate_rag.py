from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest

from medasist.evaluation import (
    EvaluationReport,
    GoldenQuestion,
    GoldenSet,
    QuestionEvalRow,
)
from medasist.ingestion.schemas import DocType
from medasist.profiles.schemas import UserProfile


def _settings() -> MagicMock:
    settings = MagicMock()
    settings.eval_golden_set_path = Path("evals/dataset/golden_set.json")
    settings.lm_studio_base_url = "http://lm.local/v1"
    settings.healthcheck_timeout = 2.0
    settings.retrieval_top_k = 10
    return settings


def _questions(n: int) -> list[GoldenQuestion]:
    return [
        GoldenQuestion(question=f"Pergunta {i}", reference_answer=f"Resposta {i}")
        for i in range(n)
    ]


def _golden_set(n: int = 3) -> GoldenSet:
    return GoldenSet(
        version="1.0.0",
        description="golden set de teste",
        questions=_questions(n),
    )


def _report() -> EvaluationReport:
    return EvaluationReport(
        aggregates={
            "context_precision": 0.7,
            "context_recall": 0.6,
            "faithfulness": 0.8,
            "answer_relevancy": 0.75,
        },
        per_question=[
            QuestionEvalRow(
                question="Pergunta 1",
                contexts=["ctx"],
                answer="Resposta [1]",
                is_cold_start=False,
                metrics={
                    "context_precision": 0.7,
                    "context_recall": 0.6,
                    "faithfulness": 0.8,
                    "answer_relevancy": 0.75,
                },
            )
        ],
        num_questions=1,
        num_cold_start=0,
        num_generation_evaluated=1,
        num_retrieval_evaluated=1,
    )


def _store(count: int) -> MagicMock:
    store = MagicMock()
    store._collection.count.return_value = count
    return store


def _patch_probe(mocker: MagicMock, counts: dict[DocType, int]) -> None:
    mocker.patch("evaluate_rag.get_client", return_value=MagicMock())
    mocker.patch("evaluate_rag.build_embeddings", return_value=MagicMock())
    mocker.patch(
        "evaluate_rag.get_all_vectorstores",
        return_value={dt: _store(c) for dt, c in counts.items()},
    )


class TestParseArgs:
    def test_defaults(self, mocker: MagicMock) -> None:
        mocker.patch("evaluate_rag.get_settings", return_value=_settings())
        from evaluate_rag import parse_args

        args = parse_args([])
        assert args.dataset == Path("evals/dataset/golden_set.json")
        assert args.top_k is None
        assert args.n is None
        assert args.profile is None
        assert args.doc_types is None
        assert args.output is None

    def test_explicit_values(self, mocker: MagicMock) -> None:
        mocker.patch("evaluate_rag.get_settings", return_value=_settings())
        from evaluate_rag import parse_args

        args = parse_args(
            [
                "--dataset",
                "custom.json",
                "--top-k",
                "5",
                "--n",
                "2",
                "--profile",
                "paciente",
                "--doc-types",
                "bula",
                "manual",
                "--output",
                "evals/results/report.json",
            ]
        )
        assert args.dataset == Path("custom.json")
        assert args.top_k == 5
        assert args.n == 2
        assert args.profile == "paciente"
        assert args.doc_types == ["bula", "manual"]
        assert args.output == Path("evals/results/report.json")

    def test_invalid_profile_raises(self, mocker: MagicMock) -> None:
        mocker.patch("evaluate_rag.get_settings", return_value=_settings())
        from evaluate_rag import parse_args

        with pytest.raises(SystemExit):
            parse_args(["--profile", "doutor"])

    def test_invalid_doc_types_raise(self, mocker: MagicMock) -> None:
        mocker.patch("evaluate_rag.get_settings", return_value=_settings())
        from evaluate_rag import parse_args

        with pytest.raises(SystemExit):
            parse_args(["--doc-types", "nota-fiscal"])


class TestMainFailFast:
    def test_invalid_dataset_returns_1(self, mocker: MagicMock) -> None:
        mocker.patch("evaluate_rag.get_settings", return_value=_settings())
        mocker.patch(
            "evaluate_rag.load_golden_set",
            side_effect=ValueError("golden set malformado"),
        )
        mock_httpx = mocker.patch("evaluate_rag.httpx.get")
        mock_eval = mocker.patch("evaluate_rag.evaluate_golden_set")

        from evaluate_rag import main

        assert main(["--dataset", "broken.json"]) == 1
        mock_httpx.assert_not_called()
        mock_eval.assert_not_called()

    def test_lm_studio_down_returns_1_without_evaluating(
        self, mocker: MagicMock
    ) -> None:
        mocker.patch("evaluate_rag.get_settings", return_value=_settings())
        mocker.patch("evaluate_rag.load_golden_set", return_value=_golden_set())
        mocker.patch(
            "evaluate_rag.httpx.get",
            side_effect=httpx.ConnectError("LM Studio fora do ar"),
        )
        mock_eval = mocker.patch("evaluate_rag.evaluate_golden_set")

        from evaluate_rag import main

        assert main([]) == 1
        mock_eval.assert_not_called()

    def test_lm_studio_non_2xx_returns_1(self, mocker: MagicMock) -> None:
        mocker.patch("evaluate_rag.get_settings", return_value=_settings())
        mocker.patch("evaluate_rag.load_golden_set", return_value=_golden_set())
        mocker.patch("evaluate_rag.httpx.get", return_value=MagicMock(status_code=500))

        from evaluate_rag import main

        assert main([]) == 1

    def test_empty_collection_returns_1(self, mocker: MagicMock) -> None:
        mocker.patch("evaluate_rag.get_settings", return_value=_settings())
        mocker.patch("evaluate_rag.load_golden_set", return_value=_golden_set())
        mocker.patch("evaluate_rag.httpx.get", return_value=MagicMock(status_code=200))
        _patch_probe(mocker, {DocType.BULA: 0, DocType.MANUAL: 3})
        mock_eval = mocker.patch("evaluate_rag.evaluate_golden_set")

        from evaluate_rag import main

        assert main([]) == 1
        mock_eval.assert_not_called()


class TestMainSuccess:
    def test_success_returns_0_and_forwards_args(self, mocker: MagicMock) -> None:
        mocker.patch("evaluate_rag.get_settings", return_value=_settings())
        mocker.patch("evaluate_rag.load_golden_set", return_value=_golden_set(4))
        mocker.patch("evaluate_rag.httpx.get", return_value=MagicMock(status_code=200))
        _patch_probe(mocker, dict.fromkeys(DocType, 3))
        mock_eval = mocker.patch(
            "evaluate_rag.evaluate_golden_set", return_value=_report()
        )

        from evaluate_rag import main

        result = main(
            [
                "--n",
                "2",
                "--top-k",
                "5",
                "--profile",
                "enfermeiro",
                "--doc-types",
                "bula",
                "manual",
            ]
        )
        assert result == 0
        assert mock_eval.call_count == 1
        call = mock_eval.call_args
        assert call.kwargs["top_k"] == 5
        assert call.kwargs["profile"] == UserProfile.ENFERMEIRO
        assert call.kwargs["doc_types"] == [DocType.BULA, DocType.MANUAL]
        assert len(call.args[0]) == 2

    def test_n_slices_questions(self, mocker: MagicMock) -> None:
        mocker.patch("evaluate_rag.get_settings", return_value=_settings())
        mocker.patch("evaluate_rag.load_golden_set", return_value=_golden_set(5))
        mocker.patch("evaluate_rag.httpx.get", return_value=MagicMock(status_code=200))
        _patch_probe(mocker, dict.fromkeys(DocType, 3))
        mock_eval = mocker.patch(
            "evaluate_rag.evaluate_golden_set", return_value=_report()
        )

        from evaluate_rag import main

        assert main(["--n", "2"]) == 0
        assert len(mock_eval.call_args.args[0]) == 2

    def test_no_profile_passes_none_so_golden_profiles_are_honored(
        self, mocker: MagicMock
    ) -> None:
        mocker.patch("evaluate_rag.get_settings", return_value=_settings())
        mocker.patch("evaluate_rag.load_golden_set", return_value=_golden_set(1))
        mocker.patch("evaluate_rag.httpx.get", return_value=MagicMock(status_code=200))
        _patch_probe(mocker, dict.fromkeys(DocType, 3))
        mock_eval = mocker.patch(
            "evaluate_rag.evaluate_golden_set", return_value=_report()
        )

        from evaluate_rag import main

        assert main([]) == 0
        assert mock_eval.call_args.kwargs["profile"] is None

    def test_all_cold_start_generation_empty_returns_1(self, mocker: MagicMock) -> None:
        mocker.patch("evaluate_rag.get_settings", return_value=_settings())
        mocker.patch("evaluate_rag.load_golden_set", return_value=_golden_set(1))
        mocker.patch("evaluate_rag.httpx.get", return_value=MagicMock(status_code=200))
        _patch_probe(mocker, dict.fromkeys(DocType, 3))
        mocker.patch(
            "evaluate_rag.evaluate_golden_set",
            return_value=EvaluationReport(
                aggregates={},
                per_question=[],
                num_questions=1,
                num_cold_start=1,
                num_generation_evaluated=0,
                num_retrieval_evaluated=0,
            ),
        )

        from evaluate_rag import main

        assert main([]) == 1

    def test_output_writes_json_report(self, mocker: MagicMock, tmp_path: Path) -> None:
        mocker.patch("evaluate_rag.get_settings", return_value=_settings())
        mocker.patch("evaluate_rag.load_golden_set", return_value=_golden_set(1))
        mocker.patch("evaluate_rag.httpx.get", return_value=MagicMock(status_code=200))
        _patch_probe(mocker, dict.fromkeys(DocType, 3))
        mocker.patch("evaluate_rag.evaluate_golden_set", return_value=_report())

        from evaluate_rag import main

        output = tmp_path / "report.json"
        assert main(["--output", str(output)]) == 0
        assert output.exists()
        payload = json.loads(output.read_text(encoding="utf-8"))
        assert "aggregates" in payload
        assert payload["counts"]["generation_evaluated"] == 1
        assert payload["counts"]["retrieval_evaluated"] == 1
        assert payload["per_question"][0]["metrics"]["faithfulness"] == pytest.approx(
            0.8
        )
