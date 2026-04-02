# Setup — Guia de Configuração do Ambiente

**Versão:** 3.0
**Data:** 2026-04-02
**Stack:** Python 3.11 | FastAPI | LangChain | ChromaDB | LM Studio | Streamlit

> **Legenda:**
> - `🔂 Única vez` — execute apenas na configuração inicial
> - `▶ Sempre` — execute toda vez que iniciar o ambiente
> - `🔁 Quando necessário` — execute apenas quando indicado

---

## Pré-requisitos

| Ferramenta | Versão Mínima | Verificação |
|------------|--------------|-------------|
| Python | 3.11 | `python --version` |
| pip | 23+ | `pip --version` |
| Git | 2.x | `git --version` |
| LM Studio | 0.3+ | Interface gráfica |
| Docker | 24+ | `docker --version` *(apenas modo Docker)* |
| Docker Compose | 2.x | `docker compose version` *(apenas modo Docker)* |

---

## Parte 1 — Configuração Geral

Passos obrigatórios para qualquer forma de execução (local ou Docker).
Execute na ordem abaixo **uma única vez** ao configurar o projeto.

---

### Passo 1 — Clonar o Repositório `🔂 Única vez`

```bash
git clone https://github.com/<seu-usuario>/medasist.git
cd medasist
```

---

### Passo 2 — Criar Estrutura de Diretórios `🔂 Única vez`

Diretórios não versionados que precisam existir antes de rodar o sistema:

```bash
mkdir -p data/raw data/processed logs chroma_db
```

| Diretório | Finalidade |
|-----------|-----------|
| `data/raw/` | PDFs de entrada para ingestão |
| `data/processed/` | Artefatos do pipeline de ingestão |
| `logs/` | Logs JSON estruturados |
| `chroma_db/` | Banco vetorial ChromaDB persistente |

Convenção de subdiretórios por tipo de documento:

```
data/raw/
├── bulas/
├── diretrizes/
├── protocolos/
└── manuais/
```

---

### Passo 3 — Configurar Variáveis de Ambiente `🔂 Única vez`

```bash
cp .env.example .env
```

Edite `.env` com seus valores:

```bash
# ── LM Studio ─────────────────────────────────────────────
# Local: http://localhost:1234/v1
# Docker: sobrescrito automaticamente pelo compose para http://host.docker.internal:1234/v1
LM_STUDIO_BASE_URL=http://localhost:1234/v1
LM_STUDIO_API_KEY=lm-studio
LM_STUDIO_LLM_MODEL=phi-3-mini
LM_STUDIO_EMBEDDING_MODEL=nomic-embed-text

# ── ChromaDB ──────────────────────────────────────────────
CHROMA_DIR=./chroma_db

# ── Dados ─────────────────────────────────────────────────
DATA_DIR=./data

# ── API ───────────────────────────────────────────────────
API_HOST=0.0.0.0
API_PORT=8000
ADMIN_API_KEY=troque-por-chave-segura

# ── UI ────────────────────────────────────────────────────
API_BASE_URL=http://localhost:8000

# ── Logs ──────────────────────────────────────────────────
LOG_LEVEL=INFO
LOG_DIR=./logs

# ── Profiles ──────────────────────────────────────────────
MEDICO_TEMPERATURE=0.1
MEDICO_MAX_TOKENS=1024
ENFERMEIRO_TEMPERATURE=0.15
ENFERMEIRO_MAX_TOKENS=1024
ASSISTENTE_TEMPERATURE=0.2
ASSISTENTE_MAX_TOKENS=512
PACIENTE_TEMPERATURE=0.3
PACIENTE_MAX_TOKENS=512
```

> **Segurança:** nunca versione o arquivo `.env`. Ele já está no `.gitignore`.

---

### Passo 4 — Configurar o LM Studio `🔂 Única vez`

O MedAssist usa **LM Studio** como provedor local de LLM e embeddings.

**4.1 Baixar e instalar:** acesse [lmstudio.ai](https://lmstudio.ai) e instale para seu sistema.

**4.2 Baixar os modelos** (aba **Discover**): `🔂 Única vez`

| Finalidade | Modelo recomendado | Tamanho aprox. |
|------------|-------------------|---------------|
| LLM | `microsoft/phi-3-mini-4k-instruct` | ~2 GB |
| Embeddings | `nomic-ai/nomic-embed-text-v1.5` | ~270 MB |

> Para máquinas com ≥ 16 GB RAM: considere `llama-3-8b-instruct` ou `mistral-7b`.

**4.3 Iniciar o servidor:** `▶ Sempre` (antes de rodar a API)

1. Abra o LM Studio
2. Vá para a aba **Developer** (ícone `</>`)
3. Carregue o modelo LLM
4. Carregue o modelo de embeddings (aba **Embeddings**)
5. Clique em **Start Server** — deve ficar em `http://localhost:1234`

**4.4 Verificar o servidor:**

```bash
curl http://localhost:1234/v1/models
# Deve retornar JSON com os modelos carregados
```

---

## Parte 2 — Execução Local (sem Docker)

Execute os passos na sequência abaixo.

---

### Passo 1 — Criar Ambiente Virtual Python `🔂 Única vez`

**Linux / macOS:**
```bash
python3.11 -m venv .venv
```

**Windows (PowerShell):**
```powershell
python -m venv .venv
```

**Windows (Git Bash):**
```bash
python -m venv .venv
```

---

### Passo 2 — Ativar o Ambiente Virtual `▶ Sempre`

Execute este passo **toda vez** que abrir um novo terminal.

**Linux / macOS:**
```bash
source .venv/bin/activate
```

**Windows (PowerShell):**
```powershell
.venv\Scripts\Activate.ps1
```

**Windows (Git Bash):**
```bash
source .venv/Scripts/activate
```

Verificar que o ambiente está ativo:
```bash
which python      # deve apontar para .venv/
python --version  # deve ser 3.11.x
```

---

### Passo 3 — Instalar Dependências `🔂 Única vez`

> Repita este passo se o `requirements*.txt` for atualizado (`🔁 Quando necessário`).

```bash
pip install -r requirements.txt
pip install -r requirements-api.txt
pip install -r requirements-ui.txt
pip install -r requirements-dev.txt
pip install -e .
```

| Grupo | Pacotes principais |
|-------|-------------------|
| LLM / RAG | langchain 0.3.25, langchain-openai 0.3.16, langchain-chroma 0.2.4 |
| Vector Store | chromadb 1.0.9 |
| PDF | pdfplumber 0.11.6, PyMuPDF 1.25.5 |
| API | fastapi 0.115.9, uvicorn 0.34.3, slowapi 0.1.9 |
| UI | streamlit 1.45.1, httpx 0.28.1 |
| Dev | pytest 8.3.5, pytest-cov, pytest-mock, black 24.10.0, flake8 7.2.0 |

---

### Passo 4 — Ingerir Documentos `🔁 Quando necessário`

Execute sempre que adicionar novos PDFs em `data/raw/`.
O processo é **idempotente** — documentos já indexados (mesmo SHA-256) não são reprocessados.

```bash
python scripts/ingest_docs.py --dir data/raw/ --doc-type bula
```

Valores válidos para `--doc-type`: `bula`, `diretriz`, `protocolo`, `manual`.

O pipeline:
1. Extrai texto dos PDFs (pdfplumber + fallback PyMuPDF)
2. Divide em chunks por tipo de documento
3. Gera embeddings via LM Studio
4. Indexa no ChromaDB

---

### Passo 5 — Subir os Serviços `▶ Sempre`

Abra **dois terminais** com o `.venv` ativado (Passo 2).

**Terminal 1 — API (FastAPI):**
```bash
uvicorn src.medasist.api.main:app --host 0.0.0.0 --port 8000 --reload
```

- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`

**Terminal 2 — UI (Streamlit):**
```bash
streamlit run src/medasist/ui/app.py
```

- Interface: `http://localhost:8501`

---

### Passo 6 — Qualidade de Código `🔁 Quando necessário`

Execute antes de commitar ou abrir um PR.

```bash
# 1. Formatar
black src/ tests/

# 2. Lint
flake8 src/ tests/

# 3. Testes com cobertura (mínimo 80%)
pytest tests/ -v --cov=src --cov-fail-under=80
```

Comandos adicionais:
```bash
# Arquivo ou teste específico
pytest tests/ingestion/test_chunker.py -v
pytest tests/ingestion/test_chunker.py::test_chunk_bula_respects_sections -v

# Relatório HTML de cobertura
pytest tests/ --cov=src --cov-report=html
# Abre htmlcov/index.html no browser

# Avaliação RAG
python scripts/evaluate_rag.py
```

---

## Parte 3 — Execução via Docker

Execute os passos na sequência abaixo.

---

### Passo 1 — Build das Imagens `🔂 Única vez`

> Repita se o `Dockerfile` ou `requirements*.txt` forem alterados (`🔁 Quando necessário`).

```bash
make build
```

---

### Passo 2 — Ingerir Documentos `🔁 Quando necessário`

Execute sempre que adicionar novos PDFs. Pode ser feito localmente (sem container) ou dentro do container da API.

**Fora do container (recomendado antes do primeiro `make up`):**
```bash
make ingest-local
# equivalente a: python scripts/ingest_docs.py --dir data/raw --doc-type bula
```

**Dentro do container (API já em execução):**
```bash
make ingest
# equivalente a: docker compose exec api python scripts/ingest_docs.py --dir /app/data/raw --doc-type bula
```

---

### Passo 3 — Subir os Serviços `▶ Sempre`

```bash
# Produção (em background)
make up

# Desenvolvimento com hot reload (terminal em foreground)
make dev
```

---

### Passo 4 — Acompanhar os Logs `🔁 Quando necessário`

```bash
make logs
```

---

### Passo 5 — Parar os Serviços `🔁 Quando necessário`

```bash
make down
```

---

### Referência Completa — Comandos Makefile

| Comando | Descrição | Frequência |
|---------|-----------|-----------|
| `make build` | Reconstrói as imagens sem cache | `🔂 Única vez` / alterações no Dockerfile |
| `make up` | Sobe API + UI em modo produção (`-d`) | `▶ Sempre` |
| `make dev` | Sobe com hot reload (monta `src/` como volume) | `▶ Sempre` (dev) |
| `make down` | Para e remove os containers | `🔁 Quando necessário` |
| `make logs` | Acompanha logs de todos os serviços (`-f`) | `🔁 Quando necessário` |
| `make ingest` | Ingestão dentro do container da API | `🔁 Quando necessário` |
| `make ingest-local` | Ingestão localmente (fora do container) | `🔁 Quando necessário` |
| `make test` | Executa suite de testes com cobertura | `🔁 Quando necessário` |
| `make lint` | Executa flake8 | `🔁 Quando necessário` |
| `make format` | Formata com black | `🔁 Quando necessário` |
| `make check` | Health check de API e UI | `🔁 Quando necessário` |

### Notas Importantes

- O **LM Studio deve estar rodando na máquina host** antes de subir os containers.
- O compose sobrescreve `LM_STUDIO_BASE_URL` para `http://host.docker.internal:1234/v1` automaticamente.
- Os volumes `./chroma_db`, `./data/raw` e `./logs` são montados nos containers — dados persistem entre reinicializações.
- O container da UI sobe apenas após a API passar no healthcheck (`/health`).

---

## Verificação End-to-End

```bash
# Health check
make check

# Ou manualmente:
curl http://localhost:8000/health

# Consulta de teste
curl -X POST http://localhost:8000/query \
  -H "Content-Type: application/json" \
  -d '{
    "question": "Qual a indicação principal do medicamento X?",
    "profile": "MEDICO",
    "doc_types": ["BULA"]
  }'
```

Resposta esperada:
```json
{
  "answer": "...[1]",
  "citations": [
    { "index": 1, "document": "bula_x.pdf", "section": "Indicações", "page": 2 }
  ],
  "profile": "MEDICO",
  "disclaimer": "Este sistema é um auxiliar informativo e não substitui avaliação médica presencial"
}
```

---

## Referência — Variáveis de Ambiente

| Variável | Padrão | Obrigatória | Descrição |
|----------|--------|-------------|-----------|
| `LM_STUDIO_BASE_URL` | `http://localhost:1234/v1` | Sim | URL do servidor LM Studio |
| `LM_STUDIO_API_KEY` | `lm-studio` | Sim | Chave (valor fixo) |
| `LM_STUDIO_LLM_MODEL` | `phi-3-mini` | Sim | Modelo LLM carregado |
| `LM_STUDIO_EMBEDDING_MODEL` | `nomic-embed-text` | Sim | Modelo de embeddings |
| `CHROMA_DIR` | `./chroma_db` | Não | Diretório do ChromaDB |
| `DATA_DIR` | `./data` | Não | Diretório de dados |
| `API_HOST` | `0.0.0.0` | Não | Host da API |
| `API_PORT` | `8000` | Não | Porta da API |
| `ADMIN_API_KEY` | — | Sim | Chave do endpoint `/ingest` |
| `API_BASE_URL` | `http://localhost:8000` | Não | URL da API (usada pela UI) |
| `LOG_LEVEL` | `INFO` | Não | Nível de log |
| `LOG_DIR` | `./logs` | Não | Diretório de logs |

---

## Problemas Comuns

### `ModuleNotFoundError: No module named 'medasist'`
```bash
pip install -e .
```

### `ConnectionRefusedError` ao chamar o LLM
1. LM Studio aberto com modelo carregado
2. Servidor iniciado na aba Developer
3. `LM_STUDIO_BASE_URL` no `.env` aponta para `http://localhost:1234/v1`

### Testes falhando por cobertura abaixo de 80%
```bash
pytest tests/ --cov=src --cov-report=term-missing
```

### `chromadb.errors.InvalidCollectionException`
```bash
rm -rf chroma_db/
mkdir chroma_db
python scripts/ingest_docs.py --dir data/raw/
```

### Porta 8000 já em uso

**Linux/macOS:**
```bash
lsof -i :8000 | grep LISTEN
kill -9 <PID>
```

**Windows:**
```bash
netstat -ano | findstr :8000
taskkill /PID <PID> /F
```
