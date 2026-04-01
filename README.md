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
| PDF | pdfplumber + PyMuPDF (fallback OCR) |
| API | FastAPI + Uvicorn + slowapi (rate limiting) |
| UI | Streamlit + httpx |
| Config | pydantic-settings |
| Testes | pytest + pytest-cov + pytest-mock |
| Qualidade | black (line-length 88) + flake8 + flake8-bugbear |
| Avaliação RAG | RAGAS + datasets |

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
# Chunks com score abaixo do threshold ativam o cold start
# RETRIEVAL_SCORE_THRESHOLD=0.4
```

Toda configuração é gerenciada por `src/medasist/config.py` (pydantic-settings). Nunca hardcode valores — use sempre `settings.*`.

---

## Comandos Comuns

```bash
# Formatar código
black src/ tests/

# Lint
flake8 src/ tests/

# Rodar todos os testes com cobertura
pytest tests/ -v --cov=src --cov-fail-under=80

# Rodar um único arquivo de teste
pytest tests/ingestion/test_chunker.py -v

# Rodar um único teste
pytest tests/ingestion/test_chunker.py::test_chunk_bula_respects_sections -v

# Ingerir documentos (coloque PDFs em data/raw/)
python scripts/ingest_docs.py --dir data/raw/

# Avaliar qualidade do RAG
python scripts/evaluate_rag.py

# Subir API + UI
uvicorn src.medasist.api.main:app --reload  # API
streamlit run src/medasist/ui/app.py        # UI (outro terminal)

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

## API

### `POST /query`

```json
{
  "question": "Qual a dose máxima de dipirona para adultos?",
  "profile": "MEDICO",
  "doc_types": ["BULA"]
}
```

Resposta:

```json
{
  "answer": "...[1]",
  "citations": [
    { "index": 1, "document": "bula_dipirona.pdf", "section": "Posologia", "page": 3 }
  ],
  "profile": "MEDICO",
  "disclaimer": "Este sistema é um auxiliar informativo e não substitui avaliação médica presencial"
}
```

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
QueryRequest → retriever.py (MMR + score threshold)
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
│   ├── retrieval/          # Retriever multi-coleção com score threshold
│   ├── generation/         # Chain LCEL, prompts, citações
│   ├── profiles/           # UserProfile enum + ProfileConfig
│   ├── api/                # FastAPI routers
│   └── ui/                 # Streamlit app
├── tests/                  # Espelho de src/
├── scripts/                # ingest_docs.py, evaluate_rag.py
├── data/
│   ├── raw/                # PDFs de entrada (não versionado)
│   └── processed/          # Artefatos processados
├── docs/adr/               # Architecture Decision Records
├── .env.example
├── requirements.txt
├── requirements-dev.txt
└── pyproject.toml
```

---

## Git

- Branches: `feat/`, `fix/`, `refactor/`, `data/`
- Commits em português, imperativo: `feat: adiciona endpoint de consulta RAG`
- Antes de abrir PR: executar code review com o agente `code-reviewer`

---

## Licença

Uso acadêmico e de portfólio. Não utilizar em ambiente clínico real sem validação médica e regulatória.
