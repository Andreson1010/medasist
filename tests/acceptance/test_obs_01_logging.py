"""Acceptance tests for OBS-01 (logging estruturado).

Verifica o logging estruturado pelo artefato observável — o arquivo JSON em
``LOG_DIR`` — e não pela API. O pipeline Chroma/LLM é substituído por uma chain
mockada (como em ``tests/api/conftest.py``); as métricas de retrieval (AC6/AC7)
são verificadas chamando ``retrieve`` com ChromaDB real em diretório temporário
(como ``tests/retrieval/test_retriever.py``). Nenhum arquivo de ``src/`` é
modificado. Dados sintéticos (Zolatril/Alphazol) — sem dado real de paciente.

Cobertura por critério de aceitação:
- AC1: ``configure_logging`` é o mecanismo único — ligado no lifespan da API
  (verificado via startup do TestClient escrevendo ``api.log`` JSON).
- AC2: ``POST /query`` produz linhas JSON em ``log_dir/api.log`` com
  ``asctime/levelname/logger/message/app``.
- AC3: UI — spawn real do Streamlit fora de escopo; ``configure_logging(..., "ui")``
  cria ``ui.log`` JSON (também coberto por ``tests/ui/test_logging.py``).
- AC4: ``LOG_LEVEL`` respeitado (WARNING suprime info; DEBUG grava debug).
- AC5: ``log_dir`` é criado com ``parents=True, exist_ok=True``.
- AC6: log de retrieval consolidado por query com query/doc_types/chunks/scores/
  latency_ms/cold_start.
- AC7: cold start loga ``cold_start=true, chunks=0, scores=[]``.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import chromadb
from fastapi.testclient import TestClient
from langchain_core.embeddings import Embeddings

from medasist.api.schemas import DependencyHealth, DependencyStatus
from medasist.config import Settings
from medasist.generation.chain import GenerationResult
from medasist.generation.citations import CitationItem
from medasist.ingestion.schemas import DocType
from medasist.logging_setup import configure_logging
from medasist.profiles.schemas import UserProfile
from medasist.retrieval.retriever import retrieve
from medasist.vectorstore.store import get_vectorstore

logger = logging.getLogger(__name__)

_ADMIN_KEY = "test-admin-key-0123456789"

DISCLAIMER = (
    "Este sistema é um auxiliar informativo e não substitui "
    "avaliação médica presencial."
)

_QUESTION = "Qual a dose recomendada do Zolatril?"
_PERFIL = UserProfile.MEDICO


def _make_result(
    answer: str = "A dose recomendada do Zolatril e 500 mg [1].",
    *,
    is_cold_start: bool = False,
) -> GenerationResult:
    """Constrói GenerationResult sintético coerente com o contrato da API.

    Parameters
    ----------
    answer : str
        Texto da resposta.
    is_cold_start : bool
        Se ``True`` devolve mensagem fixa sem citações.

    Returns
    -------
    GenerationResult
        Resultado sintético para a chain mockada.
    """
    citations = (
        []
        if is_cold_start
        else [
            CitationItem(
                index=1,
                source="bula_zolatril.pdf",
                section="Posologia",
                page="3",
            )
        ]
    )
    return GenerationResult(
        answer=answer,
        citations=citations,
        profile=_PERFIL,
        disclaimer=DISCLAIMER,
        is_cold_start=is_cold_start,
    )


def _fixed_chain(result: GenerationResult) -> MagicMock:
    """Retorna chain mock que sempre devolve o mesmo resultado.

    Parameters
    ----------
    result : GenerationResult
        Resultado fixo a retornar em toda chamada.

    Returns
    -------
    MagicMock
        Chain cujo retorno é fixado em ``result``.
    """
    chain = MagicMock()
    chain.return_value = result
    return chain


def _settings(log_dir: Path, log_level: str = "INFO") -> Settings:
    """Settings de teste com admin key forte e log_dir controlado.

    Parameters
    ----------
    log_dir : Path
        Diretório de logs (normalmente ``tmp_path``).
    log_level : str
        Nível de log a testar (ex: ``INFO``, ``WARNING``, ``DEBUG``).

    Returns
    -------
    Settings
        Configurações isoladas para o teste.
    """
    return Settings(
        admin_api_key=_ADMIN_KEY,
        log_dir=log_dir,
        log_level=log_level,
    )


@contextmanager
def _client(settings: Settings, chain: MagicMock) -> Iterator[TestClient]:
    """Constrói TestClient com lifespan mockado e settings controladas.

    Patching espelha ``tests/api/conftest.py``; além disso, ``get_settings`` é
    mockado para que o lifespan configure o logging em ``log_dir`` temporário.

    Parameters
    ----------
    settings : Settings
        Configurações com ``log_dir``/``log_level`` sob controle do teste.
    chain : MagicMock
        Chain a injetar para todos os perfis.

    Yields
    ------
    TestClient
        Cliente de teste com a app já inicializada.
    """
    chains = dict.fromkeys(UserProfile, chain)
    with (
        patch("medasist.api.main.get_settings", return_value=settings),
        patch("medasist.api.main.get_client"),
        patch("medasist.api.main.build_embeddings"),
        patch("medasist.api.main.get_all_vectorstores", return_value={}),
        patch(
            "medasist.api.main.build_chain",
            side_effect=lambda stores, profile, settings: chains[profile],
        ),
        patch(
            "medasist.api.health.check_chromadb",
            return_value=DependencyHealth(
                status=DependencyStatus.OK,
                details="saudável",
                latency_ms=1,
            ),
        ),
        patch(
            "medasist.api.health.check_lm_studio",
            return_value=DependencyHealth(
                status=DependencyStatus.OK,
                details="saudável",
                latency_ms=1,
            ),
        ),
    ):
        from medasist.api.main import app

        with TestClient(app) as c:
            yield c


def _payload(question: str = _QUESTION, profile: str = "medico") -> dict:
    """Monta payload JSON para ``POST /query``.

    Parameters
    ----------
    question : str
        Pergunta sintética.
    profile : str
        Perfil (valor string do enum).

    Returns
    -------
    dict
        Payload para ``POST /query``.
    """
    return {"question": question, "profile": profile}


def _log_records(log_file: Path) -> list[dict]:
    """Lê e parseia todas as linhas JSON do arquivo de log.

    Parameters
    ----------
    log_file : Path
        Arquivo ``api.log``/``ui.log`` a ler.

    Returns
    -------
    list[dict]
        Registros JSON, na ordem de escrita. Vazio se o arquivo não existir.
    """
    if not log_file.exists():
        return []
    return [
        json.loads(line) for line in log_file.read_text(encoding="utf-8").splitlines()
    ]


class _FakeEmbeddings(Embeddings):
    """Embeddings fake com vetores distintos para que a busca funcione."""

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[float(i % 10) * 0.1 + 0.1] * 4 for i, _ in enumerate(texts)]

    def embed_query(self, text: str) -> list[float]:
        return [0.1, 0.1, 0.1, 0.1]


def _bula_store_with_docs(tmp_path: Path) -> chromadb.ClientAPI:
    """Retorna store BULA com um documento sintético indexado.

    Parameters
    ----------
    tmp_path : Path
        Diretório temporário do teste.

    Returns
    -------
    chromadb.ClientAPI
        Cliente ChromaDB com a coleção de bulas populada.
    """
    client = chromadb.PersistentClient(path=str(tmp_path / "chroma"))
    settings = Settings(
        admin_api_key=_ADMIN_KEY,
        retrieval_top_k=10,
        retrieval_score_threshold=10.0,
    )
    store = get_vectorstore(DocType.BULA, client, _FakeEmbeddings(), settings)
    store.add_texts(
        texts=["Alphazol X: indicado para hipertensão arterial sistêmica."],
        metadatas=[{"doc_type": "bula", "source": "alphazol.pdf", "page": 1}],
        ids=["bula_001"],
    )
    return client


# ---------------------------------------------------------------------------
# AC1 — configure_logging é o mecanismo único (ligado no lifespan da API)
# ---------------------------------------------------------------------------


def test_AC1_lifespan_writes_api_log_json_on_startup(tmp_path) -> None:
    """AC1: startup do TestClient configura o logging e escreve ``api.log``."""
    log_dir = tmp_path / "logs"
    settings = _settings(log_dir)
    chain = _fixed_chain(_make_result())

    with _client(settings, chain) as c:
        c.get("/health")

    log_file = log_dir / "api.log"
    assert log_file.exists()
    records = _log_records(log_file)
    assert records, f"api.log deveria conter linhas JSON em {log_file}"
    assert all(
        {"asctime", "levelname", "logger", "message", "app"} <= set(r) for r in records
    )
    assert any(r["app"] == "api" for r in records)


# ---------------------------------------------------------------------------
# AC2 — POST /query produz JSON estruturado em api.log
# ---------------------------------------------------------------------------


def test_AC2_query_writes_json_line_with_required_fields(tmp_path) -> None:
    """AC2: ``POST /query`` grava JSON em ``api.log`` com os campos exigidos."""
    log_dir = tmp_path / "logs"
    settings = _settings(log_dir)
    chain = _fixed_chain(_make_result())

    with _client(settings, chain) as c:
        resp = c.post("/query", json=_payload())

    assert resp.status_code == 200
    records = _log_records(log_dir / "api.log")
    query_records = [r for r in records if r["logger"] == "medasist.api.routers.query"]
    assert query_records, f"nenhum record do router de query em {log_dir / 'api.log'}"
    record = query_records[0]
    assert {"asctime", "levelname", "logger", "message", "app"} <= set(record)
    assert record["app"] == "api"
    assert record["levelname"] == "INFO"
    assert "latency_ms=" in record["message"]


# ---------------------------------------------------------------------------
# AC3 — UI escreve ui.log JSON (spawn do Streamlit fora de escopo)
# ---------------------------------------------------------------------------


def test_AC3_ui_log_file_created_with_json(tmp_path) -> None:
    """AC3: ``configure_logging(settings, 'ui')`` cria ``ui.log`` JSON.

    O spawn completo do Streamlit fica fora de escopo de aceitação; este teste
    verifica o mecanismo que o entry point da UI invoca. Também coberto em
    ``tests/ui/test_logging.py``.
    """
    settings = _settings(tmp_path / "logs")
    log_file = configure_logging(settings, "ui")
    logging.getLogger("medasist.acceptance.ui").info("UI MedAssist iniciada")

    records = _log_records(log_file)
    assert log_file == tmp_path / "logs" / "ui.log"
    assert any(
        r["message"] == "UI MedAssist iniciada" and r["app"] == "ui" for r in records
    )


# ---------------------------------------------------------------------------
# AC4 — LOG_LEVEL controla o nível efetivo (handler + root)
# ---------------------------------------------------------------------------


def test_AC4_warning_suppresses_info_and_debug_in_file(tmp_path) -> None:
    """AC4: ``LOG_LEVEL=WARNING`` suprime records INFO/DEBUG no arquivo."""
    log_dir = tmp_path / "logs"
    settings = _settings(log_dir, log_level="WARNING")
    chain = _fixed_chain(_make_result())

    with _client(settings, chain) as c:
        c.post("/query", json=_payload())
        level_logger = logging.getLogger("medasist.acceptance.level")
        level_logger.warning("aviso sintetico de teste")
        level_logger.info("info sintetico de teste")
        level_logger.debug("debug sintetico de teste")

    records = _log_records(log_dir / "api.log")
    messages = [r["message"] for r in records]
    assert "aviso sintetico de teste" in messages
    assert "info sintetico de teste" not in messages
    assert "debug sintetico de teste" not in messages
    assert all(r["levelname"] != "INFO" for r in records)
    assert not any(m.startswith("query:") for m in messages)


def test_AC4_debug_level_writes_debug_records(tmp_path) -> None:
    """AC4: ``LOG_LEVEL=DEBUG`` grava records DEBUG no arquivo."""
    log_dir = tmp_path / "logs"
    settings = _settings(log_dir, log_level="DEBUG")
    chain = _fixed_chain(_make_result())

    with _client(settings, chain) as c:
        c.post("/query", json=_payload())
        logging.getLogger("medasist.acceptance.level").debug("debug sintetico de teste")

    records = _log_records(log_dir / "api.log")
    assert any(
        r["levelname"] == "DEBUG" and r["message"] == "debug sintetico de teste"
        for r in records
    )


# ---------------------------------------------------------------------------
# AC5 — log_dir é criado automaticamente (parents=True, exist_ok=True)
# ---------------------------------------------------------------------------


def test_AC5_log_dir_is_auto_created(tmp_path) -> None:
    """AC5: ``log_dir`` inexistente é criado (incluindo pais) pelo setup."""
    log_dir = tmp_path / "nao" / "existe" / "logs"
    settings = _settings(log_dir)
    chain = _fixed_chain(_make_result())

    with _client(settings, chain) as c:
        c.get("/health")

    assert log_dir.is_dir()
    assert (log_dir / "api.log").is_file()


# ---------------------------------------------------------------------------
# AC6 — métrica de retrieval consolidada por query
# ---------------------------------------------------------------------------


def test_AC6_retrieval_log_has_consolidated_metric(tmp_path) -> None:
    """AC6: log de retrieval contém query/doc_types/chunks/scores/latency_ms/
    cold_start no arquivo JSON."""
    settings = _settings(tmp_path / "logs")
    log_file = configure_logging(settings, "api")
    client = _bula_store_with_docs(tmp_path)
    retriever_settings = Settings(
        admin_api_key=_ADMIN_KEY,
        retrieval_top_k=10,
        retrieval_score_threshold=10.0,
    )
    store = get_vectorstore(DocType.BULA, client, _FakeEmbeddings(), retriever_settings)

    docs = retrieve("hipertensão", {DocType.BULA: store}, retriever_settings)

    assert docs, "retrieve deveria retornar documentos sintéticos"
    records = _log_records(log_file)
    metric = [r for r in records if r["message"].startswith("retrieve:")]
    assert metric, f"nenhum record de retrieve em {log_file}"
    message = metric[0]["message"]
    assert "query='hipertensão'" in message
    assert "doc_types=['bula']" in message
    assert f"chunks={len(docs)}" in message
    assert "scores=[" in message
    assert "latency_ms=" in message
    assert "cold_start=False" in message


# ---------------------------------------------------------------------------
# AC7 — cold start loga cold_start=true, chunks=0, scores=[]
# ---------------------------------------------------------------------------


def test_AC7_cold_start_logs_zero_chunks_and_empty_scores(tmp_path) -> None:
    """AC7: cold start produz record com ``cold_start=true, chunks=0,
    scores=[]``."""
    settings = _settings(tmp_path / "logs")
    log_file = configure_logging(settings, "api")
    client = chromadb.PersistentClient(path=str(tmp_path / "chroma"))
    retriever_settings = Settings(
        admin_api_key=_ADMIN_KEY,
        retrieval_top_k=10,
        retrieval_score_threshold=0.4,
    )
    store = get_vectorstore(DocType.BULA, client, _FakeEmbeddings(), retriever_settings)

    docs = retrieve(
        "consulta sem documentos", {DocType.BULA: store}, retriever_settings
    )

    assert docs == []
    records = _log_records(log_file)
    metric = [r for r in records if r["message"].startswith("retrieve:")]
    assert metric, f"nenhum record de retrieve em {log_file}"
    message = metric[0]["message"]
    assert "cold_start=True" in message
    assert "chunks=0" in message
    assert "scores=[]" in message
