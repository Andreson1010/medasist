from __future__ import annotations

import json
from pathlib import Path

import pytest
from datasets import Dataset
from pydantic import ValidationError

from medasist.evaluation.dataset import (
    GoldenQuestion,
    GoldenSet,
    build_eval_dataset,
    load_golden_set,
)
from medasist.ingestion.schemas import DocType
from medasist.profiles.schemas import UserProfile

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _valid_questions() -> list[dict[str, object]]:
    return [
        {
            "question": "Qual a dose inicial recomendada de Alphazol?",
            "reference_answer": "A dose inicial de Alphazol é 10 mg/dia.",
            "reference_contexts": ["Alphazol X: dose inicial de 10 mg/dia."],
            "doc_types": ["bula"],
            "profile": "medico",
            "is_cold_start": False,
        },
        {
            "question": "O que fazer em uma cefaleia tensional no pronto-atendimento?",
            "reference_answer": "Aplicar o protocolo institucional de cefaleia.",
        },
    ]


def _write_json(tmp_path: Path, data: object) -> Path:
    path = tmp_path / "golden_set.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


class TestGoldenQuestion:
    def test_parses_enum_values(self) -> None:
        question = GoldenQuestion(
            question="Qual a dose de Zolatril?",
            reference_answer="5 mg/dia.",
            doc_types=["bula", "protocolo"],
            profile="enfermeiro",
        )
        assert question.doc_types == [DocType.BULA, DocType.PROTOCOLO]
        assert question.profile == UserProfile.ENFERMEIRO
        assert question.is_cold_start is False

    def test_defaults(self) -> None:
        question = GoldenQuestion(
            question="Qual a dose de Betanorm?",
            reference_answer="20 mg/dia.",
        )
        assert question.doc_types == []
        assert question.profile == UserProfile.MEDICO
        assert question.is_cold_start is False
        assert question.reference_contexts == []

    def test_whitespace_question_raises(self) -> None:
        with pytest.raises(ValidationError):
            GoldenQuestion(question="   ", reference_answer="resposta")

    def test_whitespace_reference_answer_raises(self) -> None:
        with pytest.raises(ValidationError):
            GoldenQuestion(question="pergunta", reference_answer=" \n ")

    def test_invalid_doc_types_raises(self) -> None:
        with pytest.raises(ValidationError):
            GoldenQuestion(
                question="pergunta",
                reference_answer="resposta",
                doc_types=["nota-fiscal"],
            )

    def test_invalid_profile_raises(self) -> None:
        with pytest.raises(ValidationError):
            GoldenQuestion(
                question="pergunta",
                reference_answer="resposta",
                profile="doutor",
            )


class TestGoldenSet:
    def test_empty_questions_raises(self) -> None:
        with pytest.raises(ValidationError):
            GoldenSet(version="1.0.0", description="desc", questions=[])


class TestLoadGoldenSet:
    def test_valid_json_returns_golden_set(self, tmp_path: Path) -> None:
        path = _write_json(
            tmp_path,
            {
                "version": "1.0.0",
                "description": "golden set sintético",
                "questions": _valid_questions(),
            },
        )
        golden = load_golden_set(path)
        assert isinstance(golden, GoldenSet)
        assert golden.version == "1.0.0"
        assert len(golden.questions) == 2
        assert golden.questions[0].doc_types == [DocType.BULA]
        assert golden.questions[1].profile == UserProfile.MEDICO

    def test_malformed_json_raises_value_error_with_path(self, tmp_path: Path) -> None:
        path = tmp_path / "broken.json"
        path.write_text("{not valid json", encoding="utf-8")
        with pytest.raises(ValueError) as excinfo:
            load_golden_set(path)
        message = str(excinfo.value)
        assert "malformado" in message
        assert str(path) in message

    def test_missing_file_raises_value_error_with_path(self, tmp_path: Path) -> None:
        path = tmp_path / "nao_existe.json"
        with pytest.raises(ValueError) as excinfo:
            load_golden_set(path)
        assert str(path) in str(excinfo.value)

    def test_blank_question_error_names_field_and_index(self, tmp_path: Path) -> None:
        questions = _valid_questions()
        questions[1]["question"] = "   "
        path = _write_json(
            tmp_path, {"version": "1.0.0", "description": "d", "questions": questions}
        )
        with pytest.raises(ValueError) as excinfo:
            load_golden_set(path)
        message = str(excinfo.value)
        assert "pergunta 2" in message
        assert "question" in message

    def test_blank_reference_answer_error_names_field_and_index(
        self, tmp_path: Path
    ) -> None:
        questions = _valid_questions()
        questions[0]["reference_answer"] = "  "
        path = _write_json(
            tmp_path, {"version": "1.0.0", "description": "d", "questions": questions}
        )
        with pytest.raises(ValueError) as excinfo:
            load_golden_set(path)
        message = str(excinfo.value)
        assert "pergunta 1" in message
        assert "reference_answer" in message

    def test_invalid_doc_types_error_is_descriptive(self, tmp_path: Path) -> None:
        questions = _valid_questions()
        questions[0]["doc_types"] = ["nota-fiscal"]
        path = _write_json(
            tmp_path, {"version": "1.0.0", "description": "d", "questions": questions}
        )
        with pytest.raises(ValueError) as excinfo:
            load_golden_set(path)
        message = str(excinfo.value)
        assert "pergunta 1" in message
        assert "nota-fiscal" in message
        assert "inválido" in message

    def test_invalid_profile_error_is_descriptive(self, tmp_path: Path) -> None:
        questions = _valid_questions()
        questions[0]["profile"] = "doutor"
        path = _write_json(
            tmp_path, {"version": "1.0.0", "description": "d", "questions": questions}
        )
        with pytest.raises(ValueError) as excinfo:
            load_golden_set(path)
        message = str(excinfo.value)
        assert "pergunta 1" in message
        assert "doutor" in message

    def test_empty_questions_error(self, tmp_path: Path) -> None:
        path = _write_json(
            tmp_path, {"version": "1.0.0", "description": "d", "questions": []}
        )
        with pytest.raises(ValueError) as excinfo:
            load_golden_set(path)
        assert "pergunta" in str(excinfo.value)

    def test_versioned_golden_set_file_validates(self) -> None:
        path = _REPO_ROOT / "evals" / "dataset" / "golden_set.json"
        golden = load_golden_set(path)
        assert len(golden.questions) >= 8
        assert any(q.is_cold_start for q in golden.questions)
        assert all(q.question.strip() for q in golden.questions)
        assert all(q.reference_answer.strip() for q in golden.questions)


class TestBuildEvalDataset:
    def test_builds_dataset_with_expected_columns(self) -> None:
        questions = [
            GoldenQuestion(
                question="P1",
                reference_answer="R1",
                reference_contexts=["C1"],
                doc_types=[DocType.BULA],
                is_cold_start=True,
            ),
            GoldenQuestion(question="P2", reference_answer="R2"),
        ]
        dataset = build_eval_dataset(questions)
        assert isinstance(dataset, Dataset)
        assert len(dataset) == 2
        assert set(dataset.column_names) == {
            "question",
            "contexts",
            "reference_answer",
            "reference_contexts",
            "is_cold_start",
        }
        assert dataset[0]["question"] == "P1"
        assert dataset[0]["contexts"] == []
        assert dataset[0]["reference_contexts"] == ["C1"]
        assert dataset[0]["is_cold_start"] is True
        assert dataset[1]["is_cold_start"] is False

    def test_accepts_golden_set_questions(self, tmp_path: Path) -> None:
        path = _write_json(
            tmp_path,
            {
                "version": "1.0.0",
                "description": "d",
                "questions": _valid_questions(),
            },
        )
        golden = load_golden_set(path)
        dataset = build_eval_dataset(golden.questions)
        assert len(dataset) == 2
