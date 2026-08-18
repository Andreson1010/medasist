# AGENTS.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Stack

- Python 3.11 | LangChain (LCEL) + LM Studio | ChromaDB local (persistente)
- FastAPI + Uvicorn | Streamlit | Docker + Docker Compose
- Qualidade: black (line-length 88), ruff (E/W/F/I/B/UP/C4/SIM), pytest

## Comandos Comuns

```bash
# Instalar dependências
pip install -r requirements.txt -r requirements-dev.txt

# Formatar código
black src/ tests/ scripts/

# Lint
ruff check src/ tests/ scripts/

# Lint com auto-fix (imports, unused vars)
ruff check --fix src/ tests/ scripts/

# Rodar todos os testes
pytest tests/ -v --cov=src --cov-fail-under=80

# Rodar um único arquivo de teste
pytest tests/ingestion/test_chunker.py -v

# Rodar um único teste
pytest tests/ingestion/test_chunker.py::test_chunk_bula_respects_sections -v

# Ingestão de documentos
python scripts/ingest_docs.py --dir data/raw/

# Avaliação RAG (offline, requer LM Studio + coleções populadas)
python scripts/evaluate_rag.py --dataset evals/dataset/golden_set.json
python scripts/evaluate_rag.py --n 3 --top-k 5 --doc-types bula diretriz

# Subir ambiente local (API + UI)
cp .env.example .env  # preencher LM_STUDIO_BASE_URL/modelos no .env
docker compose -f docker-compose.yml -f docker-compose.dev.yml up

# API docs: http://localhost:8000/docs
# UI:       http://localhost:8501
```

## Arquitetura

O sistema é um pipeline RAG em camadas:

```
UI (Streamlit) → API (FastAPI) → Chain (LangChain LCEL) → ChromaDB + LM Studio
                                      ↑
                              Pipeline de Ingestão
```

**`src/medasist/config.py`** — fonte única de configuração via `pydantic-settings`. Todo módulo importa daqui: caminhos, nomes de coleção, tamanhos de chunk, modelos LM Studio, thresholds.

**`src/medasist/ingestion/`** — pipeline de ingestão: `loader.py` extrai texto de PDFs (pdfplumber + fallback PyMuPDF), `chunker.py` aplica estratégia diferente por `DocType`, `metadata.py` anexa metadados por chunk, `pipeline.py` orquestra tudo de forma idempotente (hash evita re-ingestão).

**`src/medasist/vectorstore/`** — uma coleção ChromaDB por `DocType` (`bulas`, `diretrizes`, `protocolos`, `manuais`). Isso evita contaminação pós-ANN: filtragem por tipo ocorre na seleção da coleção, não via `where` depois do ANN.

**`src/medasist/retrieval/`** — `retriever.py` filtra por distância L2 (similarity_search_with_score) com score threshold. Se nenhum chunk supera o threshold, a chain curto-circuita antes de chamar o LLM (cold start — zero custo, zero alucinação).

**`src/medasist/generation/`** — `chain.py` monta a chain LCEL `retriever | prompt | ChatOpenAI (LM Studio) | parser`. `prompts.py` contém um `PromptRegistry` com template por `UserProfile`. `citations.py` valida que todo `[N]` no texto tem `CitationItem` correspondente; referências órfãs são removidas.

**`src/medasist/profiles/schemas.py`** — enum `UserProfile` (`MEDICO`, `ENFERMEIRO`, `ASSISTENTE`, `PACIENTE`) e `ProfileConfig` com `temperature`, `max_tokens`, `prompt_template`. Temperaturas: médico → 0.1, enfermeiro → 0.15, assistente → 0.2, paciente → 0.3.

**`src/medasist/evaluation/`** — avaliação offline do RAG via RAGAS: `dataset.py` valida o golden set (`evals/dataset/golden_set.json`) e `metrics.py` executa `evaluate_golden_set` (retrieve + run_query por pergunta, 4 métricas sobre o subconjunto não-cold-start: ContextPrecision/ContextRecall e Faithfulness/AnswerRelevancy). Nunca passa pela API HTTP.

**`src/medasist/api/`** — FastAPI com lifespan que aquece todas as chains no startup. `POST /query` recebe `QueryRequest(question, profile, doc_types?)` e retorna `QueryResponse(answer, citations, profile, disclaimer)`. `POST /ingest` requer header `X-Admin-Key`. Rate limiting via `slowapi`.

**`src/medasist/ui/app.py`** — Streamlit que chama `POST /query` via httpx. Nunca acessa o provider de LLM diretamente (apenas via API).

## Convenções Python Obrigatórias

Todo arquivo `.py` deve começar com:
```python
from __future__ import annotations
```

- Paths: sempre `pathlib.Path`, nunca strings brutas
- Logging: `logger = logging.getLogger(__name__)`, nunca `print()`
- Docstrings: estilo NumPy em todas as funções e classes públicas
- Secrets: apenas em `.env`; referência em `.env.example`
- Limites de código: funções com até 50 linhas, aninhamento até 4 níveis e arquivos com até 800 linhas; acima disso, extrair/módularizar.

## Regras de Segurança Inegociáveis

1. Toda resposta da API deve incluir o disclaimer: `"Este sistema é um auxiliar informativo e não substitui avaliação médica presencial"`
2. Cold start obrigatório: retrieval vazio → mensagem fixa, nunca resposta gerada
3. Toda resposta deve citar ao menos uma fonte: `[N] <nome_doc> — Seção: <seção>, Pág. <pág>`
4. Nenhum dado real de paciente em código, testes ou logs

## Testes

- Espelham `src/` em `tests/` (ex: `src/medasist/ingestion/chunker.py` → `tests/ingestion/test_chunker.py`)
- Fixtures usam dados sintéticos (nomes de medicamentos e protocolos fictícios)
- Testes de vectorstore usam `chromadb.EphemeralClient`
- Mocks de LLM/embeddings em testes unitários via `pytest-mock`

## Git

- Branches: `feat/`, `fix/`, `refactor/`, `data/`
- Commits em português, imperativo: `feat: adiciona endpoint de consulta RAG`
- Utilize a skill git-workflow

## Fluxo de Code Review

  Antes de abrir qualquer PR, executar code review com o skill **code-reviewer**.