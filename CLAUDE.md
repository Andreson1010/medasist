# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Stack

- Python 3.11 | LangChain (LCEL) + OpenAI GPT-4o | ChromaDB local (persistente)
- FastAPI + Uvicorn | Streamlit | Docker + Docker Compose
- Qualidade: black (line-length 88), flake8 + flake8-bugbear, pytest

## Comandos Comuns

```bash
# Instalar dependências
pip install -r requirements.txt -r requirements-dev.txt

# Formatar código
black src/ tests/

# Lint
flake8 src/ tests/

# Rodar todos os testes
pytest tests/ -v --cov=src --cov-fail-under=80

# Rodar um único arquivo de teste
pytest tests/ingestion/test_chunker.py -v

# Rodar um único teste
pytest tests/ingestion/test_chunker.py::test_chunk_bula_respects_sections -v

# Ingestão de documentos
python scripts/ingest_docs.py --dir data/raw/

# Avaliação do RAG
python scripts/evaluate_rag.py

# Subir ambiente local (API + UI)
cp .env.example .env  # preencher OPENAI_API_KEY
docker compose -f docker-compose.yml -f docker-compose.dev.yml up

# API docs: http://localhost:8000/docs
# UI:       http://localhost:8501
```

## Arquitetura

O sistema é um pipeline RAG em camadas:

```
UI (Streamlit) → API (FastAPI) → Chain (LangChain LCEL) → ChromaDB + OpenAI
                                      ↑
                              Pipeline de Ingestão
```

**`src/medasist/config.py`** — fonte única de configuração via `pydantic-settings`. Todo módulo importa daqui: caminhos, nomes de coleção, tamanhos de chunk, modelos OpenAI, thresholds.

**`src/medasist/ingestion/`** — pipeline de ingestão: `loader.py` extrai texto de PDFs (pdfplumber + fallback PyMuPDF/OCR), `chunker.py` aplica estratégia diferente por `DocType`, `metadata.py` anexa metadados por chunk, `pipeline.py` orquestra tudo de forma idempotente (hash evita re-ingestão).

**`src/medasist/vectorstore/`** — uma coleção ChromaDB por `DocType` (`bulas`, `diretrizes`, `protocolos`, `manuais`). Isso evita contaminação pós-ANN: filtragem por tipo ocorre na seleção da coleção, não via `where` depois do ANN.

**`src/medasist/retrieval/`** — `retriever.py` configura `VectorStoreRetriever` com MMR e score threshold. Se nenhum chunk supera o threshold, a chain curto-circuita antes de chamar o LLM (cold start — zero custo, zero alucinação).

**`src/medasist/generation/`** — `chain.py` monta a chain LCEL `retriever | prompt | ChatOpenAI | parser`. `prompts.py` contém um `PromptRegistry` com template por `UserProfile`. `citations.py` valida que todo `[N]` no texto tem `CitationItem` correspondente; referências órfãs são removidas.

**`src/medasist/profiles/schemas.py`** — enum `UserProfile` (`MEDICO`, `ENFERMEIRO`, `ASSISTENTE`, `PACIENTE`) e `ProfileConfig` com `temperature`, `max_tokens`, `prompt_template`. Temperaturas: médico → 0.1, assistente → 0.2, paciente → 0.3.

**`src/medasist/api/`** — FastAPI com lifespan que aquece todas as chains no startup. `POST /query` recebe `QueryRequest(question, profile, doc_types?)` e retorna `QueryResponse(answer, citations, profile, disclaimer)`. `POST /ingest` requer header `X-Admin-Key`. Rate limiting via `slowapi`.

**`src/medasist/ui/app.py`** — Streamlit que chama `POST /query` via httpx. Nunca acessa OpenAI diretamente.

## Convenções Python Obrigatórias

Todo arquivo `.py` deve começar com:
```python
from __future__ import annotations
```

- Paths: sempre `pathlib.Path`, nunca strings brutas
- Logging: `logger = logging.getLogger(__name__)`, nunca `print()`
- Docstrings: estilo NumPy em todas as funções e classes públicas
- Secrets: apenas em `.env`; referência em `.env.example`

## Regras de Segurança Inegociáveis

1. Toda resposta da API deve incluir o disclaimer: `"Este sistema é um auxiliar informativo e não substitui avaliação médica presencial"`
2. Cold start obrigatório: retrieval vazio → mensagem fixa, nunca resposta gerada
3. Toda resposta deve citar ao menos uma fonte: `[N] <nome_doc> — Seção: <seção>, Pág. <pág>`
4. Nenhum dado real de paciente em código, testes ou logs

## Testes

- Espelham `src/` em `tests/` (ex: `src/medasist/ingestion/chunker.py` → `tests/ingestion/test_chunker.py`)
- Fixtures usam dados sintéticos (nomes de medicamentos e protocolos fictícios)
- Testes de vectorstore usam `chromadb.EphemeralClient`
- Mocks de OpenAI em testes unitários via `pytest-mock`

## Git

- Branches: `feat/`, `fix/`, `refactor/`, `data/`
- Commits em português, imperativo: `feat: adiciona endpoint de consulta RAG`
