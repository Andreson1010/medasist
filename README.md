# MedAssist

Sistema de assistência clínica digital baseado em RAG (Retrieval-Augmented Generation). Responde perguntas médicas com base em bulas, diretrizes clínicas, protocolos e manuais, citando fontes e adaptando a linguagem ao perfil do usuário.

> **Aviso:** Este sistema é um auxiliar informativo e não substitui avaliação médica presencial.

---

## Visão Geral

```
UI (Streamlit) → API (FastAPI) → Chain (LangChain LCEL) → ChromaDB + LM Studio
                                        ↑
                               Pipeline de Ingestão (PDF → chunks → embeddings)
```

- **LLM local:** LM Studio (API OpenAI-compatível) — sem custo por token, dados ficam na máquina
- **Vector store:** ChromaDB persistente, uma coleção por tipo de documento
- **Cold start:** retrieval sem resultado acima do threshold → mensagem fixa, zero chamada ao LLM
- **Perfis:** médico, enfermeiro, assistente administrativo e paciente — temperatura e prompt distintos por perfil

---

## Stack

| Camada | Tecnologia |
|--------|-----------|
| LLM / Embeddings | LM Studio (Phi-3-mini / nomic-embed-text) via LangChain |
| Orquestração | LangChain LCEL |
| Vector Store | ChromaDB (persistente local) |
| PDF | pdfplumber + PyMuPDF (fallback) |
| API | FastAPI + Uvicorn + slowapi (rate limiting) |
| UI | Streamlit + httpx |
| Config | pydantic-settings |
| Testes | pytest + pytest-cov + pytest-mock |
| Qualidade | black (line-length 88) + ruff |

---

## Pré-requisitos

- Python 3.11+
- [LM Studio](https://lmstudio.ai/) rodando localmente com:
  - Modelo LLM carregado (ex: `phi-3-mini`)
  - Modelo de embeddings carregado (ex: `nomic-embed-text`)
  - Servidor local iniciado (porta padrão: `1234`)

---

## Instalação

```bash
# 1. Clonar o repositório
git clone https://github.com/<seu-usuario>/medasist.git
cd medasist

# 2. Criar e ativar ambiente virtual
python -m venv .venv
source .venv/bin/activate  # Linux/macOS
.venv\Scripts\activate     # Windows

# 3. Instalar dependências
pip install -r requirements.txt -r requirements-dev.txt

# 4. Configurar variáveis de ambiente
cp .env.example .env
# Editar .env conforme necessário (ver seção Configuração)
```

---

## Configuração

Copie `.env.example` para `.env` e ajuste os valores:

```bash
# LM Studio
LM_STUDIO_BASE_URL=http://localhost:1234/v1
LM_STUDIO_API_KEY=lm-studio
LM_STUDIO_LLM_MODEL=phi-3-mini
LM_STUDIO_EMBEDDING_MODEL=nomic-embed-text

# API
API_HOST=0.0.0.0
API_PORT=8000
ADMIN_API_KEY=troque-por-chave-segura

# Retrieval
# Chunks com distância L2 acima do threshold ativam o cold start
RETRIEVAL_SCORE_THRESHOLD=0.4
```

Toda configuração é gerenciada por `src/medasist/config.py` (pydantic-settings). Nunca hardcode valores — use sempre `settings.*`.

---

## Docker

O projeto roda como dois containers orquestrados por Docker Compose:

| Serviço | Porta | Dockerfile |
|---------|-------|-----------|
| API (FastAPI) | 8000 | `docker/api.Dockerfile` |
| UI (Streamlit) | 8501 | `docker/ui.Dockerfile` |

```bash
# Produção (builda e sobe)
docker compose up -d

# Desenvolvimento (hot reload: src/ montado como volume)
docker compose -f docker-compose.yml -f docker-compose.dev.yml up
```

O LM Studio roda na máquina host — o container acessa via `http://host.docker.internal:1234/v1`. Os dados (ChromaDB, PDFs, logs) são persistidos por volumes, fora da imagem.

---

## CI/CD (GitHub Actions)

O pipeline divide **CI** (qualidade + artefato) e **CD** (deploy na VPS) em dois workflows:

```
push/PR ──▶ ci.yml (CI) ──▶ GHCR (imagens api/ui)
                │
merge na main (CI ok)
                ▼
         deploy.yml (CD) ──SSH──▶ VPS: pull + compose up -d
```

### CI — `.github/workflows/ci.yml`

1. **Em toda PR**: lint (`ruff`), formatação (`black --check`) e testes (`pytest` com cobertura ≥ 80%) — o PR fica bloqueado até passar.
2. **No push para `main`**: após os testes, builda e publica as imagens `api` e `ui` no **GitHub Container Registry (GHCR)** com tags `latest` + SHA do commit. O build ocorre no CI — **sem build no servidor**.

### CD — `.github/workflows/deploy.yml`

Disparado automaticamente via `workflow_run` quando o workflow **CI** termina com sucesso na branch `main`:

1. Conecta na VPS por SSH (`VPS_HOST`, `VPS_USER`, `VPS_SSH_KEY`).
2. Atualiza o repositório (`git pull`), baixa as imagens do GHCR (`docker compose pull`) e sobe a stack (`docker compose -f docker-compose.prod.yml up -d`).

Qualquer merge na `main` que passe no CI chega à produção **sem intervenção manual**. Se um teste falhar, o deploy não roda.

Guia completo de implantação e rollback: `docs/technical/deploy.md`.

---

## Comandos Comuns

```bash
# Formatar código
black src/ tests/

# Lint
ruff check src/ tests/

# Rodar todos os testes com cobertura
pytest tests/ -v --cov=src --cov-fail-under=80

# Rodar um único arquivo de teste
pytest tests/ingestion/test_chunker.py -v

# Rodar um único teste
pytest tests/ingestion/test_chunker.py::test_chunk_bula_respects_sections -v

# Ingerir documentos (coloque PDFs em data/raw/ e especifique o tipo)
python scripts/ingest_docs.py --dir data/raw/bulas --doc-type bula

# Avaliação RAG (offline)
python scripts/evaluate_rag.py --dataset evals/dataset/golden_set.json

# Subir API + UI localmente (sem Docker)
python -m uvicorn medasist.api.main:app --reload  # API (outro terminal)
streamlit run src/medasist/ui/app.py              # UI

# Subir API + UI com Docker Compose (produção)
docker compose up -d

# Subir em modo desenvolvimento (hot reload de src/)
docker compose -f docker-compose.yml -f docker-compose.dev.yml up

# Docs da API
# http://localhost:8000/docs
```

---

## Tipos de Documento

| DocType | Coleção ChromaDB | Descrição |
|---------|-----------------|-----------|
| `BULA` | `bulas` | Bulas de medicamentos |
| `DIRETRIZ` | `diretrizes` | Diretrizes clínicas |
| `PROTOCOLO` | `protocolos` | Protocolos assistenciais |
| `MANUAL` | `manuais` | Manuais técnicos |

Cada tipo usa uma estratégia de chunking própria (separadores e tamanhos configurados em `settings`).

---

## Perfis de Usuário

| Perfil | Temperature | Max Tokens | Linguagem |
|--------|-------------|-----------|-----------|
| `MEDICO` | 0.1 | 1024 | Técnica / clínica |
| `ENFERMEIRO` | 0.15 | 1024 | Técnica / assistencial |
| `ASSISTENTE` | 0.2 | 512 | Administrativa |
| `PACIENTE` | 0.3 | 512 | Simples / acessível |

---

## Avaliação RAG (offline)

Avaliação de qualidade do pipeline com [RAGAS 0.2.15](https://github.com/explodinggradients/ragas) sobre um golden set 100% sintético (`evals/dataset/golden_set.json` — medicamentos fictícios, sem dados reais).

```bash
python scripts/evaluate_rag.py --dataset evals/dataset/golden_set.json
```

- **Offline**: sem API HTTP e sem nuvem — LLM e embeddings apontam para o LM Studio local (`EVAL_LLM_MODEL`/`EVAL_EMBEDDING_MODEL`).
- **4 métricas**: Context Precision e Context Recall (retrieval, todas as perguntas) + Faithfulness e Answer Relevancy (geração, excluindo cold starts).
- **Pré-requisitos**: LM Studio disponível e coleções ChromaDB populadas (fail-fast com código de saída `1`).
- Relatório JSON opcional com `--output evals/results/report.json` (não versionado).

---

## API

### `POST /query`

```json
{
  "question": "Qual a dose máxima de dipirona para adultos?",
  "profile": "medico",
  "doc_types": ["bula"]
}
```

Resposta:

```json
{
  "answer": "...[1]",
  "citations": [
    { "index": 1, "source": "bula_dipirona.pdf", "section": "Posologia", "page": 3 }
  ],
  "profile": "medico",
  "disclaimer": "Este sistema é um auxiliar informativo e não substitui avaliação médica presencial",
  "is_cold_start": false,
  "unanswered_sub_questions": []
}
```

### `POST /query/stream`

Variante com **Server-Sent Events (SSE)** para streaming incremental da resposta. Quando `generation_streaming_enabled=True`, entrega eventos tipados (`token`, `citations`, `disclaimer`, `cold_start`, `error`, `done`) que a UI consome com `st.write_stream`. Quando a flag está off, responde 404 e a UI degrada para `/query`.

### `POST /ingest`

Requer header `X-Admin-Key`. Dispara ingestão de documentos PDF.

---

## Arquitetura

### Pipeline de Ingestão

```
PDF → loader.py (pdfplumber / PyMuPDF) → chunker.py (por DocType)
    → metadata.py (anexa metadados) → pipeline.py (hash SHA-256, idempotente)
    → ChromaDB (embeddings via LM Studio)
```

Ingestão é **idempotente**: documentos já processados (mesmo SHA-256) são ignorados.

### Pipeline de Recuperação e Geração

```
QueryRequest → retriever.py (distância L2 + score threshold)
             → [cold start se score < threshold]
             → chain.py (LangChain LCEL: retriever | prompt | LLM | parser)
             → citations.py (valida referências [N])
             → QueryResponse
```

---

## Regras de Segurança (Inegociáveis)

1. **Disclaimer obrigatório** — toda resposta inclui: _"Este sistema é um auxiliar informativo e não substitui avaliação médica presencial"_
2. **Cold start** — retrieval vazio ou abaixo do threshold → mensagem fixa, nunca resposta gerada
3. **Citação obrigatória** — toda resposta cita ao menos uma fonte: `[N] <nome_doc> — Seção: <seção>, Pág. <pág>`
4. **Sem dados reais de pacientes** — código, testes e logs devem usar apenas dados sintéticos

---

## Testes

```bash
# Todos os testes (cobertura mínima: 80%)
pytest tests/ -v --cov=src --cov-fail-under=80
```

- Espelham `src/` em `tests/`
- Fixtures com dados sintéticos (medicamentos e protocolos fictícios)
- Vectorstore: `chromadb.EphemeralClient`
- LLM: mocks via `pytest-mock`

---

## Estrutura do Projeto

```
medasist/
├── src/medasist/
│   ├── config.py           # Fonte única de configuração (pydantic-settings)
│   ├── ingestion/          # loader, chunker, metadata, pipeline
│   ├── vectorstore/        # Cliente ChromaDB + embeddings
│   ├── retrieval/          # Retriever multi-coleção + busca híbrida/rerank
│   ├── generation/         # Chain LCEL, prompts, citações, streaming
│   ├── profiles/           # UserProfile enum + ProfileConfig
│   ├── api/                # FastAPI routers (/query, /query/stream, /ingest)
│   └── ui/                 # Streamlit app
├── tests/                  # Espelho de src/
├── scripts/                # ingest_docs.py, evaluate_rag.py
├── docker/                 # Dockerfiles da API e UI
├── docs/                   # PRD e documentação técnica
├── .github/workflows/      # ci.yml (lint, testes, build GHCR) + deploy.yml (CD na VPS)
├── data/
│   ├── raw/                # PDFs de entrada (não versionado)
│   └── processed/          # Artefatos processados
├── .env.example
├── requirements.txt
├── requirements-api.txt    # Docker da API
├── requirements-ui.txt     # Docker da UI
├── requirements-dev.txt
├── requirements.lock       # Build reproduzível no CI
└── pyproject.toml
```

---

## Git

- Branches: `feat/`, `fix/`, `refactor/`, `data/`
- Commits em português, imperativo: `feat: adiciona endpoint de consulta RAG`
- Antes de abrir PR: executar code review com o skill `code-reviewer`

---

## Licença

Uso acadêmico e de portfólio. Não utilizar em ambiente clínico real sem validação médica e regulatória.
