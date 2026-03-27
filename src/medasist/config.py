from __future__ import annotations

import logging
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    """Configurações centrais do MedAssist, carregadas do .env.

    Usa LM Studio como provider local compatível com a API OpenAI.
    Configure o LM Studio para escutar em LM_STUDIO_BASE_URL antes
    de iniciar a aplicação.

    Parameters
    ----------
    lm_studio_base_url : str
        URL base do servidor LM Studio (ex: http://localhost:1234/v1).
    lm_studio_api_key : SecretStr
        Chave da API — qualquer string; LM Studio não valida.
    lm_studio_llm_model : str
        Nome do modelo LLM carregado no LM Studio.
    lm_studio_embedding_model : str
        Nome do modelo de embedding carregado no LM Studio.
    chroma_dir : Path
        Diretório de persistência do ChromaDB.
    data_dir : Path
        Diretório raiz dos documentos.
    api_host : str
        Host de bind do servidor FastAPI.
    api_port : int
        Porta do servidor FastAPI.
    admin_api_key : SecretStr
        Chave de autenticação do endpoint /ingest.
    api_base_url : str
        URL base da API consumida pelo Streamlit.
    log_level : str
        Nível de log (INFO, DEBUG, WARNING, ERROR).
    log_dir : Path
        Diretório de saída dos logs estruturados.
    retrieval_top_k : int
        Número de chunks recuperados por consulta.
    retrieval_score_threshold : float
        Score mínimo de similaridade; abaixo disso aciona cold start.
    medico_temperature : float
        Temperatura do LLM para o perfil MEDICO (padrão: 0.1).
    medico_max_tokens : int
        Máximo de tokens gerados para o perfil MEDICO (padrão: 1024).
    enfermeiro_temperature : float
        Temperatura do LLM para o perfil ENFERMEIRO (padrão: 0.15).
    enfermeiro_max_tokens : int
        Máximo de tokens gerados para o perfil ENFERMEIRO (padrão: 1024).
    assistente_temperature : float
        Temperatura do LLM para o perfil ASSISTENTE (padrão: 0.2).
    assistente_max_tokens : int
        Máximo de tokens gerados para o perfil ASSISTENTE (padrão: 512).
    paciente_temperature : float
        Temperatura do LLM para o perfil PACIENTE (padrão: 0.3).
    paciente_max_tokens : int
        Máximo de tokens gerados para o perfil PACIENTE (padrão: 512).
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # LM Studio
    lm_studio_base_url: str = Field(default="http://localhost:1234/v1")
    lm_studio_api_key: SecretStr = Field(default=SecretStr("lm-studio"))
    lm_studio_llm_model: str = Field(default="phi-3-mini")
    lm_studio_embedding_model: str = Field(default="nomic-embed-text")

    # ChromaDB
    chroma_dir: Path = Field(default=Path("./chroma_db"))

    # Dados
    data_dir: Path = Field(default=Path("./data"))

    # API
    api_host: str = Field(default="0.0.0.0")
    api_port: int = Field(default=8000)
    admin_api_key: SecretStr = Field(default=SecretStr("dev-only"))

    # UI
    api_base_url: str = Field(default="http://localhost:8000")

    # Logs
    log_level: str = Field(default="INFO")
    log_dir: Path = Field(default=Path("./logs"))

    # Retrieval
    retrieval_top_k: int = Field(default=10)
    retrieval_score_threshold: float = Field(default=0.4)

    # Chunking — bulas
    chunk_size_bula: int = Field(default=600)
    chunk_overlap_bula: int = Field(default=100)

    # Chunking — diretrizes
    chunk_size_diretriz: int = Field(default=800)
    chunk_overlap_diretriz: int = Field(default=150)

    # Chunking — protocolos
    chunk_size_protocolo: int = Field(default=400)
    chunk_overlap_protocolo: int = Field(default=50)

    # Chunking — manuais
    chunk_size_manual: int = Field(default=500)
    chunk_overlap_manual: int = Field(default=100)

    # Nomes de coleções ChromaDB (um por DocType)
    collection_bulas: str = Field(default="bulas")
    collection_diretrizes: str = Field(default="diretrizes")
    collection_protocolos: str = Field(default="protocolos")
    collection_manuais: str = Field(default="manuais")

    # Profiles — temperaturas e max_tokens por papel
    medico_temperature: float = Field(default=0.1, ge=0.0, le=2.0)
    medico_max_tokens: int = Field(default=1024, gt=0)
    enfermeiro_temperature: float = Field(default=0.15, ge=0.0, le=2.0)
    enfermeiro_max_tokens: int = Field(default=1024, gt=0)
    assistente_temperature: float = Field(default=0.2, ge=0.0, le=2.0)
    assistente_max_tokens: int = Field(default=512, gt=0)
    paciente_temperature: float = Field(default=0.3, ge=0.0, le=2.0)
    paciente_max_tokens: int = Field(default=512, gt=0)

    # Textos fixos de segurança
    disclaimer: str = Field(
        default=(
            "Este sistema é um auxiliar informativo e não substitui "
            "avaliação médica presencial."
        )
    )
    cold_start_message: str = Field(
        default=(
            "Não encontrei essa informação nos documentos disponíveis. "
            "Por favor, consulte um profissional de saúde."
        )
    )


_settings: Settings | None = None


def get_settings() -> Settings:
    """Retorna instância singleton das configurações.

    Returns
    -------
    Settings
        Instância carregada do .env (singleton por processo).
    """
    global _settings
    if _settings is None:
        _settings = Settings()  # type: ignore[call-arg]
    return _settings
