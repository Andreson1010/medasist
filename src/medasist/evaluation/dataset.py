from __future__ import annotations

import json
import logging
from pathlib import Path

from datasets import Dataset
from pydantic import BaseModel, ValidationError, field_validator, model_validator

from medasist.ingestion.schemas import DocType
from medasist.profiles.schemas import UserProfile

logger = logging.getLogger(__name__)


class GoldenQuestion(BaseModel):
    """Uma pergunta do golden set de avaliação RAG.

    Attributes
    ----------
    question : str
        Pergunta sintética em PT-BR. Não pode ser vazia nem só de espaços.
    reference_answer : str
        Resposta de referência (ground truth). Não pode ser vazia.
    reference_contexts : list[str]
        Contextos de referência opcionais (usados pelo RAGAS quando presentes).
    doc_types : list[DocType]
        Tipos de documento a consultar para esta pergunta. Vazio consulta
        todas as coleções.
    profile : UserProfile
        Perfil de usuário usado na geração desta pergunta (padrão: MEDICO).
    is_cold_start : bool
        Indica documentalmente que a pergunta deve validar cold start.
    """

    question: str
    reference_answer: str
    reference_contexts: list[str] = []
    doc_types: list[DocType] = []
    profile: UserProfile = UserProfile.MEDICO
    is_cold_start: bool = False

    @field_validator("question", "reference_answer")
    @classmethod
    def _not_blank(cls, v: str, info) -> str:
        """Rejeita strings vazias ou apenas de espaços, nomeando o campo.

        Parameters
        ----------
        v : str
            Valor do campo a validar.
        info
            Metadados de validação (usado para obter o nome do campo).

        Returns
        -------
        str
            Valor validado sem espaços nas bordas.

        Raises
        ------
        ValueError
            Se o valor for vazio ou só de espaços.
        """
        stripped = v.strip()
        if not stripped:
            raise ValueError(f"{info.field_name} não pode ser vazio")
        return stripped

    @field_validator("doc_types", mode="before")
    @classmethod
    def _validate_doc_types(cls, v: object) -> object:
        """Valida os valores de ``doc_types`` antes da coerção para o enum.

        Produz mensagem descritiva citando o valor inválido (REQ-10).

        Parameters
        ----------
        v : object
            Valor bruto do campo ``doc_types``.

        Returns
        -------
        object
            O valor original, validado.

        Raises
        ------
        ValueError
            Se algum item não for um valor válido de ``DocType``.
        """
        valid = {dt.value for dt in DocType}
        if isinstance(v, list):
            for item in v:
                if isinstance(item, str) and item not in valid:
                    raise ValueError(f"doc_types contém '{item}' (inválido)")
        return v

    @field_validator("profile", mode="before")
    @classmethod
    def _validate_profile(cls, v: object) -> object:
        """Valida o valor de ``profile`` antes da coerção para o enum.

        Produz mensagem descritiva citando o valor inválido.

        Parameters
        ----------
        v : object
            Valor bruto do campo ``profile``.

        Returns
        -------
        object
            O valor original, validado.

        Raises
        ------
        ValueError
            Se o valor não for um perfil de usuário válido.
        """
        valid = {p.value for p in UserProfile}
        if isinstance(v, str) and v not in valid:
            raise ValueError(
                f"profile '{v}' inválido. Use um de: {', '.join(sorted(valid))}."
            )
        return v


class GoldenSet(BaseModel):
    """Conjunto de perguntas de avaliação RAG versionado.

    Attributes
    ----------
    version : str
        Versão do golden set (ex: ``1.0.0``).
    description : str
        Descrição do conteúdo e propósito do golden set.
    questions : list[GoldenQuestion]
        Perguntas do golden set. Deve conter ao menos uma pergunta.
    """

    version: str
    description: str
    questions: list[GoldenQuestion]

    @model_validator(mode="after")
    def _validate_questions_non_empty(self) -> GoldenSet:
        """Garante que o golden set não esteja vazio.

        Returns
        -------
        GoldenSet
            Instância validada.

        Raises
        ------
        ValueError
            Se a lista de perguntas estiver vazia.
        """
        if not self.questions:
            raise ValueError("golden set deve conter ao menos uma pergunta")
        return self


def _clean_msg(msg: str) -> str:
    """Remove o prefixo ``Value error, `` das mensagens do Pydantic.

    Parameters
    ----------
    msg : str
        Mensagem bruta do Pydantic.

    Returns
    -------
    str
        Mensagem sem o prefixo.
    """
    return msg.replace("Value error, ", "", 1)


def _rewrite_validation_error(path: Path, exc: ValidationError) -> ValueError:
    """Reescreve um ``ValidationError`` do Pydantic em ``ValueError`` descritivo.

    Erros dentro de ``questions[i].campo`` ganham o índice da pergunta (1-based),
    conforme REQ-10: ex. ``pergunta 3: question não pode ser vazio``.

    Parameters
    ----------
    path : Path
        Caminho do arquivo de origem.
    exc : ValidationError
        Erro de validação do Pydantic.

    Returns
    -------
    ValueError
        Erro descritivo com campo e índice quando aplicável.
    """
    errors = exc.errors()
    first = errors[0] if errors else {"loc": (), "msg": "schema inválido"}
    loc = first.get("loc", ())
    msg = _clean_msg(str(first.get("msg", "valor inválido")))
    if len(loc) >= 3 and loc[0] == "questions" and isinstance(loc[1], int):
        index = int(loc[1]) + 1
        return ValueError(f"pergunta {index}: {msg}")
    return ValueError(f"golden set inválido em {path}: {msg}")


def load_golden_set(path: Path) -> GoldenSet:
    """Carrega e valida um golden set a partir de um arquivo JSON.

    Erros de leitura/JSON malformado são convertidos em ``ValueError`` com o
    caminho do arquivo; erros de schema (Pydantic) são reescritos com o campo
    e o índice da pergunta quando aplicável (REQ-1, REQ-9, REQ-10).

    Parameters
    ----------
    path : Path
        Caminho do arquivo JSON do golden set.

    Returns
    -------
    GoldenSet
        Golden set validado.

    Raises
    ------
    ValueError
        Se o arquivo não puder ser lido, o JSON for malformado ou o schema
        for inválido.
    """
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"não foi possível ler golden set em {path}: {exc}") from exc
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"golden set malformado em {path}: {exc}") from exc
    try:
        return GoldenSet.model_validate(raw)
    except ValidationError as exc:
        raise _rewrite_validation_error(path, exc) from exc


def build_eval_dataset(questions: list[GoldenQuestion]) -> Dataset:
    """Converte perguntas do golden set em ``datasets.Dataset``.

    As colunas seguem o schema definido no design (§2.3): ``question``,
    ``contexts`` (listas vazias — preenchidas durante a avaliação),
    ``reference_answer``, ``reference_contexts`` e ``is_cold_start``.

    Parameters
    ----------
    questions : list[GoldenQuestion]
        Perguntas validadas do golden set.

    Returns
    -------
    Dataset
        Dataset com uma linha por pergunta e ``contexts`` vazios.
    """
    rows = [
        {
            "question": q.question,
            "contexts": [],
            "reference_answer": q.reference_answer,
            "reference_contexts": q.reference_contexts,
            "is_cold_start": q.is_cold_start,
        }
        for q in questions
    ]
    return Dataset.from_list(rows)
