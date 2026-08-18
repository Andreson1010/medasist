# Project Structure

**Root:** `C:\Users\ander\8_projetos\medasist`

## Directory Tree

```
medasist/
├── src/
│   └── medasist/
│       ├── __init__.py
│       ├── config.py
│       ├── ingestion/
│       │   ├── __init__.py
│       │   ├── schemas.py
│       │   ├── loader.py
│       │   ├── chunker.py
│       │   ├── metadata.py
│       │   └── pipeline.py
│       ├── vectorstore/
│       │   ├── __init__.py
│       │   └── store.py
│       ├── retrieval/
│       │   ├── __init__.py
│       │   └── retriever.py
│       ├── generation/
│       │   ├── __init__.py
│       │   ├── prompts.py
│       │   ├── citations.py
│       │   └── chain.py
│       ├── profiles/
│       │   ├── __init__.py
│       │   └── schemas.py
│       ├── api/
│       │   ├── __init__.py
│       │   ├── deps.py
│       │   ├── schemas.py
│       │   ├── main.py
│       │   └── routers/
│       │       ├── __init__.py
│       │       ├── query.py
│       │       └── ingest.py
│       ├── evaluation/
│       │   └── __init__.py
│       └── ui/
│           ├── __init__.py
│           ├── client.py
│           └── app.py
├── tests/
│   ├── __init__.py
│   ├── conftest.py
│   ├── api/
│   ├── ingestion/
│   ├── vectorstore/
│   ├── retrieval/
│   ├── generation/
│   ├── profiles/
│   ├── ui/
│   ├── scripts/
│   └── evaluation/
├── scripts/
│   └── ingest_docs.py
├── docker/
│   ├── api.Dockerfile
│   └── ui.Dockerfile
├── data/
│   └── raw/
│       ├── bulas/
│       ├── diretrizes/
│       ├── manuais/
│       └── protocolos/
├── chroma_db/
├── logs/
├── docs/                    # gitignored — not version-controlled
├── todo/                    # gitignored
├── Makefile
├── pyproject.toml
├── .flake8
├── requirements.txt
├── requirements-api.txt
├── requirements-ui.txt
├── requirements-dev.txt
├── .env / .env.example
├── AGENTS.md
└── README.md
```

## Module Organization

### config.py

**Purpose:** Central configuration via pydantic-settings — single source of truth for all settings
**Location:** `src/medasist/config.py`
**Key contents:** `Settings(BaseSettings)` with all env vars, `get_settings()` singleton, `disclaimer` and `cold_start_message` constants

### ingestion/

**Purpose:** Document ETL pipeline — PDF extraction, chunking, metadata, idempotent indexing
**Location:** `src/medasist/ingestion/`
**Key files:**
- `schemas.py` — `DocType` enum, `LoadedDocument`, `PageContent` (frozen dataclasses)
- `loader.py` — `load_pdf()` with pdfplumber + PyMuPDF fallback
- `chunker.py` — `chunk_document()` with per-DocType `RecursiveCharacterTextSplitter`
- `metadata.py` — `build_metadata()` / `build_metadata_batch()` for ChromaDB
- `pipeline.py` — `ingest_document()` / `ingest_directory()` orchestrator (SHA-256 idempotency)

### vectorstore/

**Purpose:** ChromaDB client singleton + per-DocType collection access
**Location:** `src/medasist/vectorstore/`
**Key files:**
- `store.py` — `get_client()` (thread-safe singleton), `build_embeddings()`, `get_vectorstore()`, `get_all_vectorstores()`

### retrieval/

**Purpose:** Multi-collection search with score-threshold cold-start filtering
**Location:** `src/medasist/retrieval/`
**Key files:**
- `retriever.py` — `_MultiStoreRetriever(BaseRetriever)`, `build_retriever()`, `retrieve()` with L2 distance threshold

### generation/

**Purpose:** LLM orchestration, prompt templates, citation validation
**Location:** `src/medasist/generation/`
**Key files:**
- `chain.py` — `run_query()`, `build_chain()`, `GenerationResult` (LCEL chain orchestration)
- `prompts.py` — `PromptRegistry` (lazy thread-safe ChatPromptTemplate cache per UserProfile)
- `citations.py` — `CitationItem`, `build_citations()`, `validate_citations()` (orphan removal)

### profiles/

**Purpose:** User role configuration — enum, ProfileConfig, prompt templates
**Location:** `src/medasist/profiles/`
**Key files:**
- `schemas.py` — `UserProfile` enum, `ProfileConfig` (frozen), `PROMPT_TEMPLATES` dict, `get_profile_config()`

### api/

**Purpose:** FastAPI HTTP interface with lifespan warm-up, rate limiting, admin auth
**Location:** `src/medasist/api/`
**Key files:**
- `main.py` — FastAPI app + lifespan (chain warm-up) + `/health`
- `schemas.py` — `QueryRequest`, `QueryResponse`, `CitationResponse`, `IngestResponse` (Pydantic DTOs)
- `deps.py` — slowapi `Limiter` instance
- `routers/query.py` — `POST /query` (rate-limited 10/min)
- `routers/ingest.py` — `POST /ingest` (admin-key auth, rate-limited 5/min)

### ui/

**Purpose:** Streamlit chat interface — calls API via httpx, never touches LLM/ChromaDB
**Location:** `src/medasist/ui/`
**Key files:**
- `app.py` — Streamlit chat app with profile selector, doc_type filter, citation rendering
- `client.py` — `check_health()`, `query()` via `httpx.Client`; custom exception hierarchy

### evaluation/

**Purpose:** RAG evaluation placeholder (not yet implemented)
**Location:** `src/medasist/evaluation/`
**Key files:** `__init__.py` (empty)

## Where Things Live

### Ingestion

- PDF extraction: `src/medasist/ingestion/loader.py`
- Chunking: `src/medasist/ingestion/chunker.py`
- Metadata: `src/medasist/ingestion/metadata.py`
- Orchestration: `src/medasist/ingestion/pipeline.py`
- CLI script: `scripts/ingest_docs.py`

### RAG Query

- Retriever: `src/medasist/retrieval/retriever.py`
- Chain: `src/medasist/generation/chain.py`
- Prompts: `src/medasist/generation/prompts.py`
- Citations: `src/medasist/generation/citations.py`
- Profile config: `src/medasist/profiles/schemas.py`

### API

- App + lifespan: `src/medasist/api/main.py`
- DTOs: `src/medasist/api/schemas.py`
- Rate limiter: `src/medasist/api/deps.py`
- Routes: `src/medasist/api/routers/`

### UI

- Streamlit app: `src/medasist/ui/app.py`
- HTTP client: `src/medasist/ui/client.py`

### Configuration

- Settings: `src/medasist/config.py`
- Env template: `.env.example`
- Docker: `docker-compose.yml`, `docker-compose.dev.yml`

### Data

- Raw PDFs: `data/raw/{bulas,diretrizes,protocolos,manuais}/`
- Vector DB: `chroma_db/` (gitignored)
- Logs: `logs/` (gitignored)

## Special Directories

**`data/raw/`** — Input PDFs organized by DocType subdirectories. Gitignored (medical documents). Mounted read-only in Docker.

**`chroma_db/`** — ChromaDB persistent storage. Gitignored. Volume-mounted in Docker.

**`docs/`** — Architecture Decision Records, PRD, roadmap, technical docs. Gitignored — not version-controlled. Contains `adr/`, `business/`, `plan/`, `technical/` subdirectories.

**`todo/`** — Phase-based TODO tracking. Gitignored.

**`scripts/`** — CLI tools. `ingest_docs.py` for document ingestion. `evaluate_rag.py` referenced but does not exist.

## directory Tree (optional)

meu-rag-agentico/
├── .env.example                # Modelo de variáveis de ambiente (Chaves de API, DBs)
├── .gitignore                  # Arquivos ignorados pelo Git (venv, logs, .env)
├── README.md                   # Documentação do projeto, setup e guias de execução
├── pyproject.toml              # Gerenciamento de dependências modernas (Poetry/Rye/Pip)
├── requirements.txt            # Dependências em formato padrão (fallback)
│
├── config/                     # CONFIGURAÇÕES E PARÂMETROS GLOBAIS
│   ├── __init__.py
│   ├── settings.py             # Variáveis de ambiente validadas via Pydantic Settings
│   └── prompts.py              # Centralização de Prompts do Sistema (Router, Grader, Generator)
│
├── logs/                       # DIRETÓRIO ARQUIVOS DE LOGS LOCAL (Gerado automaticamente)
│   ├── app.log                 # Logs de inicialização e rotas HTTP da API
│   └── agent_decisions.log     # Histórico de tomadas de decisão e loops do Agente
│
├── evals/                      # ESTEIRA DE TESTES E AVALIAÇÃO (OFF-LINE / CI-CD)
│   ├── __init__.py
│   ├── run_evals.py            # Script principal para rodar baterias de testes em massa
│   ├── dataset/
│   │   └── golden_set.json     # Dataset de perguntas gabaritadas (Perguntas/Respostas Ideais)
│   └── metrics/
│       ├── __init__.py
│       ├── faithfulness.py     # Métrica: Validação de Alucinação (Ragas/TruLens)
│       └── relevance.py        # Métrica: Relevância do contexto vs Pergunta
│
├── src/                        # CÓDIGO FONTE DA APLICAÇÃO (ON-LINE)
│   ├── __init__.py
│   ├── main.py                 # Ponto de entrada FastAPI (Exposição do agente via API/Streamlit)
│   │
│   ├── agents/                 # O CÉREBRO AGÊNTICO (GRAFOS E DECISÕES)
│   │   ├── __init__.py
│   │   ├── graph.py            # Definição e compilação do Grafo de Estados (LangGraph/LlamaIndex)
│   │   ├── state.py            # Schema do objeto de Estado compartilhado do Agente (Pydantic/TypedDict)
│   │   ├── router.py           # Agente de Roteamento (Decide qual ferramenta chamar)
│   │   └── grader.py           # Agente Crítico (Avalia se o documento retornado é útil)
│   │
│   ├── tools/                  # FERRAMENTAS/AÇÕES DISPONÍVEIS PARA O AGENTE
│   │   ├── __init__.py
│   │   ├── vector_search.py    # Ferramenta para buscar no banco vetorial interno
│   │   ├── web_search.py       # Ferramenta de contingência (Tavily, Serper, BrightData)
│   │   └── code_executor.py    # Opcional: Sandbox para execução local de códigos python
│   │
│   ├── pipeline/               # PIPELINE DE INGESTÃO E PROCESSAMENTO (ASSÍNCRONO/OFF-LINE)
│   │   ├── __init__.py
│   │   ├── ingest.py           # Script principal para rodar carga de novos documentos
│   │   ├── loaders.py          # Adaptadores para carregar PDFs, Notion, Confluence ou S3
│   │   └── splitters.py        # Algoritmos avançados de Chunking (Semantic ou Character Splitter)
│   │
│   ├── database/               # CONEXÕES COM BANCOS DE DADOS
│   │   ├── __init__.py
│   │   └── vector_store.py     # Inicialização e conexões (Chroma, Qdrant, PGVector, Pinecone)
│   │
│   └── utils/                  # AUXILIARES TRANSVERSAIS DA APLICAÇÃO
│       ├── __init__.py
│       ├── logger.py           # Configuração de Logs Estruturados Rotativos (JSON/Console)
│       └── tracer.py           # Configuração e inicialização de Telemetria (Langfuse/OpenTelemetry)
│
└── tests/                      # TESTES UNITÁRIOS E DE INTEGRAÇÃO TRADICIONAIS
    ├── __init__.py
    ├── conftest.py             # Fixtures comuns para os testes
    ├── test_agents.py          # Testes de roteamento de intenção
    └── test_tools.py           # Testes isolados das ferramentas de busca

## Decisão de estrutura (AD-010, 2026-08-11)

**Veredito: manter a estrutura atual.** A "directory Tree (optional)" acima foi projetada para um
sistema **agêntico** (`agents/` com LangGraph, `tools/web_search.py`, `code_executor.py`) — não
aplica ao MedAssist, que é um pipeline RAG linear LCEL com LLM local obrigatório (sem orquestração
agêntica, sem busca web, sem execução de código).

### Motivos

1. A estrutura atual espelha as camadas reais do pipeline (`ingestion/ → vectorstore/ → retrieval/ → generation/`), conforme AGENTS.md.
2. `tests/` espelha `src/` 1:1 — migrar quebraria o padrão de espelhamento e o fluxo de review.
3. Layout `src/medasist/` (single package) é exigido pelo `python -m uvicorn medasist.api.main:app` e pelo Streamlit.
4. Custo de migração alto (~16 módulos, 267+ testes, Dockerfiles) para benefício zero.

### Única adoção da optional

O conceito de `evals/` será adotado no **OBS-03 (Avaliação RAGAS)**, encaixando no `evaluation/` atual (stub vazio):
- `evals/dataset/golden_set.json` — perguntas sintéticas gabaritadas
- `evals/metrics/` — faithfulness, relevance, context precision/recall
- `scripts/evaluate_rag.py` — citado no README mas inexistente

Elementos da optional **rejeitados**: `config/prompts.py` (prompts ficam em `generation/prompts.py`),
`database/vector_store.py` (fica `vectorstore/store.py`), `utils/logger.py` (substituído por
`logging_setup.py` do OBS-01), `utils/tracer.py` (sem telemetria distribuída no escopo atual).

