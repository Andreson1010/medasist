from __future__ import annotations

import logging
from pathlib import Path

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

ADMIN_KEY_MIN_LENGTH: int = 16
_INSECURE_ADMIN_KEYS: tuple[str, ...] = ("dev-only", "troque-por-chave-segura")
_LOG_LEVELS: tuple[str, ...] = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


def admin_key_is_weak(value: str) -> bool:
    """Verifica se uma chave de admin é fraca.

    Uma chave é considerada fraca quando é um placeholder conhecido ou tem
    menos que ``ADMIN_KEY_MIN_LENGTH`` caracteres (após remover espaços).

    Parameters
    ----------
    value : str
        Valor da chave (plaintext) a ser verificado.

    Returns
    -------
    bool
        ``True`` se a chave é fraca, ``False`` caso contrário.
    """
    stripped = value.strip()
    return (stripped in _INSECURE_ADMIN_KEYS) or (len(stripped) < ADMIN_KEY_MIN_LENGTH)


def csv_list(value: str) -> list[str]:
    """Converte uma string CSV (vírgula) em lista de itens.

    Itens vazios são descartados e o placeholder ``"*"`` é preservado
    como item único. Uma string vazia ou só de vírgulas retorna ``["*"]``
    (comportamento permissivo para o middleware CORS).

    Parameters
    ----------
    value : str
        String com itens separados por vírgula (ex: ``"a, b, c"``).

    Returns
    -------
    list[str]
        Lista de itens sem espaços. ``["*"]`` se não houver itens.
    """
    items = [item.strip() for item in value.split(",") if item.strip()]
    return items or ["*"]


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
    llm_max_retries : int
        Número máximo de tentativas nas chamadas ao LLM do LM Studio.
        Retry com backoff exponencial (padrão: 2).
    llm_request_timeout : float
        Timeout em segundos por chamada ao LLM do LM Studio (padrão: 60.0).
    embedding_max_retries : int
        Número máximo de tentativas nas chamadas de embedding do LM Studio.
        Retry com backoff exponencial (padrão: 2).
    embedding_request_timeout : float
        Timeout em segundos por chamada de embedding do LM Studio (padrão: 30.0).
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
    max_upload_mb : int
        Limite de tamanho de upload em MB para o endpoint /ingest (padrão: 25).
    cors_allow_origins : str
        Origens permitidas no CORS, separadas por vírgula (padrão: ``"*"``).
    cors_allow_methods : str
        Métodos HTTP permitidos no CORS, separados por vírgula (padrão: ``"*"``).
    cors_allow_headers : str
        Headers permitidos no CORS, separados por vírgula (padrão: ``"*"``).
    cors_allow_credentials : bool
        Permite credenciais nas requisições CORS (padrão: ``False``).
    api_base_url : str
        URL base da API consumida pelo Streamlit.
    healthcheck_timeout : float
        Tempo limite (em segundos) de cada probe do /health (padrão: 3.0).
    log_level : str
        Nível de log (DEBUG, INFO, WARNING, ERROR, CRITICAL).
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
    eval_golden_set_path : Path
        Caminho do golden set de avaliação RAG
        (padrão: ``evals/dataset/golden_set.json``).
    eval_llm_model : str
        Modelo LLM usado como judge na avaliação; vazio resolve para
        ``lm_studio_llm_model``.
    eval_embedding_model : str
        Modelo de embeddings usado na avaliação; vazio resolve para
        ``lm_studio_embedding_model``.
    eval_batch_size : int
        Tamanho do lote nas chamadas de avaliação RAGAS (padrão: 16).
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

    # Retry/backoff e timeout nas chamadas ao LM Studio
    llm_max_retries: int = Field(default=2, ge=0)
    llm_request_timeout: float = Field(default=60.0, gt=0)
    embedding_max_retries: int = Field(default=2, ge=0)
    embedding_request_timeout: float = Field(default=30.0, gt=0)

    # ChromaDB
    chroma_dir: Path = Field(default=Path("./chroma_db"))

    # Dados
    data_dir: Path = Field(default=Path("./data"))

    # API
    api_host: str = Field(default="0.0.0.0")
    api_port: int = Field(default=8000)
    admin_api_key: SecretStr = Field(default=SecretStr("dev-only"))
    max_upload_mb: int = Field(default=25, gt=0)

    # CORS
    cors_allow_origins: str = Field(default="*")
    cors_allow_methods: str = Field(default="*")
    cors_allow_headers: str = Field(default="*")
    cors_allow_credentials: bool = Field(default=False)

    @field_validator("admin_api_key")
    @classmethod
    def _validate_admin_api_key(cls, v: SecretStr) -> SecretStr:
        """Rejeita chaves fracas/placeholder ou com menos de 16 caracteres.

        Parameters
        ----------
        v : SecretStr
            Valor da chave de admin.

        Returns
        -------
        SecretStr
            A chave validada.

        Raises
        ------
        ValueError
            Se a chave for fraca (placeholder ou < ``ADMIN_KEY_MIN_LENGTH``).
        """
        if admin_key_is_weak(v.get_secret_value()):
            raise ValueError(
                "admin_api_key precisa ter pelo menos "
                f"{ADMIN_KEY_MIN_LENGTH} caracteres e não usar valor "
                "padrão/placeholder."
            )
        return v

    @field_validator("log_level")
    @classmethod
    def _validate_log_level(cls, v: str) -> str:
        """Valida e normaliza o nível de log para letras maiúsculas.

        Aceita ``DEBUG, INFO, WARNING, ERROR, CRITICAL`` (case-insensitive).
        Valores inválidos falham no startup (fail-fast), evitando que a
        aplicação rode com um nível de log desconhecido.

        Parameters
        ----------
        v : str
            Valor do campo ``log_level``.

        Returns
        -------
        str
            Nível normalizado em letras maiúsculas.

        Raises
        ------
        ValueError
            Se o valor não for um nível de log válido.
        """
        normalized = v.strip().upper()
        if normalized not in _LOG_LEVELS:
            raise ValueError(
                f"log_level inválido '{v}'. Use um de: {', '.join(_LOG_LEVELS)}."
            )
        return normalized

    # UI
    api_base_url: str = Field(default="http://localhost:8000")
    ui_request_timeout: float = Field(default=120.0, gt=0)

    # Health check
    healthcheck_timeout: float = Field(default=3.0, gt=0)

    # Logs
    log_level: str = Field(default="INFO")
    log_dir: Path = Field(default=Path("./logs"))

    # Retrieval
    retrieval_top_k: int = Field(default=10)
    retrieval_score_threshold: float = Field(default=0.4)

    # Avaliação RAG (offline)
    eval_golden_set_path: Path = Field(default=Path("evals/dataset/golden_set.json"))
    eval_llm_model: str = Field(default="")
    eval_embedding_model: str = Field(default="")
    eval_batch_size: int = Field(default=16, gt=0)

    @model_validator(mode="after")
    def _resolve_eval_models(self) -> Settings:
        """Resolve modelos de avaliação vazios para os modelos principais.

        Mantém o default vazio para que a resolução acompanhe ``lm_studio_llm_model``
        e ``lm_studio_embedding_model`` (sem duplicar o valor do modelo no código).

        Returns
        -------
        Settings
            Instância com ``eval_llm_model``/``eval_embedding_model`` preenchidos.
        """
        if not self.eval_llm_model:
            self.eval_llm_model = self.lm_studio_llm_model
        if not self.eval_embedding_model:
            self.eval_embedding_model = self.lm_studio_embedding_model
        return self

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
