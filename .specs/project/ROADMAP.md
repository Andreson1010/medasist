# Roadmap

**Current Milestone:** M2 — Estabilização e Correções Críticas
**Status:** Planning

---

## M1 — MVP RAG Médico — COMPLETE

**Goal:** Pipeline RAG funcional end-to-end com ingestão de PDFs, retrieval, geração e UI de chat.
**Target:** Concluído

### Features

**Pipeline de Ingestão** - COMPLETE
- Loader PDF (pdfplumber + PyMuPDF fallback)
- Chunker por DocType (RecursiveCharacterTextSplitter)
- Metadata builder
- Pipeline idempotente (SHA-256)

**Vector Store** - COMPLETE
- ChromaDB PersistentClient singleton (thread-safe)
- 4 coleções por DocType
- OpenAIEmbeddings via LM Studio

**Retrieval** - COMPLETE
- Multi-store retriever (BaseRetriever)
- Score threshold + cold start
- Dedup por page_content

**Geração** - COMPLETE
- Chain LCEL (prompt | ChatOpenAI | StrOutputParser)
- PromptRegistry lazy thread-safe por UserProfile
- Citation validation (orphan removal)

**API** - COMPLETE
- FastAPI + lifespan (chain warm-up)
- POST /query, POST /ingest, GET /health
- Rate limiting (slowapi)
- Admin key auth (/ingest)

**UI** - COMPLETE
- Streamlit chat com histórico
- Seletor de perfil
- Filtro de doc_types
- Renderização de citações + disclaimer

**Qualidade** - COMPLETE
- black + ruff + pytest (96% cobertura)
- 160 testes (unit + integration)
- Docker multi-stage (API + UI)

---

## M2 — Estabilização e Correções Críticas

**Goal:** Corrigir bugs, gaps de segurança e divergências de documentação que impedem uso confiável.
**Target:** Concluído

### Features

**FIX-01: Bug get_client() sem argumento** - PLANNED
- `ingest.py:96` chama `get_client()` sem `settings` → TypeError em runtime
- Prioridade: P0 (bug que bloqueia /ingest em produção)

**FIX-02: Sync-in-async bloqueia event loop** - PLANNED
- `query.py` endpoint async chama chain sincrona → bloqueia todas as requisições
- Prioridade: P0 (concorrência de 1 request por vez)

**FIX-03: doc_types filter ignorado** - PLANNED
- API aceita `doc_types` mas `run_query()` busca em todas as coleções
- Prioridade: P0 (arquitetura prometida não funciona)

**FIX-04: Atualizar CLAUDE.md** - PLANNED
- Diz OpenAI GPT-4o (é LM Studio), diz MMR (é L2), diz OCR (não tem), diz evaluate_rag.py (não existe)
- Prioridade: P1 (documentação enganosa)

**FIX-05: Limite de upload no /ingest** - PLANNED
- `await file.read()` sem size check → OOM com arquivo grande
- Prioridade: P1 (segurança)

**FIX-06: Admin key validation** - PLANNED
- Default "dev-only" sem validação de complexidade
- Prioridade: P1 (segurança)

**FIX-07: CORS middleware** - DONE
- Sem CORSMiddleware → bloqueia integrações browser
- Prioridade: P2

---

## M3 — Observabilidade e Qualidade RAG

**Goal:** Adicionar visibilidade e capacidade de medir qualidade do pipeline RAG.
**Target:** Concluído

### Features

**OBS-01: Configurar logging estruturado** - DONE
- Wire `python-json-logger` nos entry points (API + UI)
- Aplicar `LOG_LEVEL` e `LOG_DIR` do Settings
- Logar retrieval (chunks + scores + latência) por query

**OBS-02: Health check de dependências** - DONE
- `/health` verificar ChromaDB + LM Studio
- Retornar status por dependência

**OBS-03: Avaliação RAG (RAGAS)** - DONE
- Criar `scripts/evaluate_rag.py`
- Implementar `src/medasist/evaluation/`
- Golden set com perguntas sintéticas
- Métricas: Context Precision, Recall, Faithfulness, Answer Relevancy

**OBS-04: Retry/backoff para LM Studio** - DONE
- Adicionar retry com backoff exponencial nas chamadas ao LM Studio
- Configurável via Settings

---

## M4 — Melhorias do Pipeline RAG

**Goal:** Elevar qualidade de retrieval e geração com técnicas avançadas.

### Features

**RAG-01: Re-ranking de chunks** - DONE
- Cross-encoder reranker no top-N recuperado
- Subir MRR e Context Precision
- PR #23 (AD-013)

**RAG-02: Hybrid search (denso + esparso)** - DONE
- Combinar similarity_search com BM25/keyword
- Fusão via RRF
- Crítico para nomes de medicamentos e dosagens exatas
- PR #24 (AD-014)

**RAG-03: Query transformation** - DONE
- Reescrever/expandir queries curtas antes do retrieval (sub-feature escolhida; decomposição de multi-parte adiada para o backlog)
- PR #25 (AD-014)

**RAG-04: Section/page nas citações** - DONE
- Propagar page_number do PageContent → TextChunk → ChunkMetadata
- Extrair section headings do documento
- Citação completa: `[N] doc — Seção: X, Pág. Y`
- Bônus: guarda lexical anti-contaminação cruzada de fármacos no retriever

---

## M5 — CI/CD e Deploy

**Goal:** Automatizar qualidade e deploy.

### Features

**CICD-01: GitHub Actions CI** - PLANNED
- Workflow: ruff + black --check + pytest --cov-fail-under=80
- Rodar em cada PR

**CICD-02: Lock file de dependências** - PLANNED
- Adicionar `langchain-text-splitters` aos requirements
- Gerar lock file para transitive deps
- Remover deps não usadas (avaliar langchain-community)

**CICD-03: Coverage gate no pyproject.toml** - PLANNED
- Mover `--cov-fail-under=80` para `addopts`
- Garantir que `pytest` bare também enforce 80%

**CICD-04: Testes de rate limiting** - PLANNED
- Testar slowapi 429 em /query e /ingest

---

## M6 — Documentação e Convenções

**Goal:** Alinhar documentação com implementação e versionar decisões.

### Features

**DOC-01: Desun-igore docs/** - PLANNED
- Avaliar quais docs versionar (ADRs, architecture)
- Remover `docs/` do .gitignore seletivamente

**DOC-02: Preencher api-spec.yaml** - PLANNED
- OpenAPI spec completo para os 3 endpoints

**DOC-03: Padronizar __init__.py exports** - PLANNED
- Adicionar `__all__` em generation/ e api/
- Consistente com ingestion/, vectorstore/, retrieval/, profiles/

---

## Future Considerations

- Cache de embeddings para evitar re-computação
- Multi-tenancy para múltiplas instituições
- Streaming de respostas (SSE) no /query
- Auth de usuários (JWT/OAuth) além do admin key
- Indexação incremental agendada (cron/watch)
- Suporte a outros formatos além de PDF (DOCX, HTML, XML)
- Internacionalização (EN/ES) além de PT-BR
- Fine-tuning de embeddings com vocabulário médico
- Avaliação contínua em CI com golden set versionado
