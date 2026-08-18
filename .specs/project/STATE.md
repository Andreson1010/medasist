# State

**Last Updated:** 2026-08-18
**Current Work:** OBS-04 (retry/backoff, PR #21) e RAG-04 (citações com página/seção + guarda lexical de fármacos, PR #22) concluídos e mergeados, fechando M3. Próximo passo: M4 — RAG-01 re-ranking ou RAG-02 hybrid search (denso + esparso)

---

## Recent Decisions (Last 60 days)

### AD-001: Migração de flake8 para ruff (2026-08-07)

**Decision:** Substituir flake8 + flake8-bugbear por ruff 0.9.9 como linter único.
**Reason:** Ruff é 10-100x mais rápido, unifica lint + isort + pyupgrade, e lê pyproject.toml nativamente (eliminando o .flake8 e o [tool.flake8] morto).
**Trade-off:** Equipe precisa instalar ruff localmente; flake8 não está mais nos requirements-dev.
**Impact:** Makefile, CLAUDE.md, skills, agentes e gate checks atualizados. 19 issues corrigidas (imports, StrEnum, SIM117, C420).

### AD-002: LLM local (LM Studio) em vez de OpenAI cloud (2026-08-07)

**Decision:** Confirmado que o projeto usa LM Studio (phi-3-mini) e não OpenAI GPT-4o.
**Reason:** Sem custo por token, dados ficam na máquina, adequado para projeto acadêmico.
**Trade-off:** Limitado a modelos locais menores; sem garantia de uptime.
**Impact:** CLAUDE.md precisa ser atualizado (FIX-04 no backlog) — ainda referencia OpenAI GPT-4o.

### AD-003: Brownfield mapping completo (2026-08-07)

**Decision:** Gerados 7 docs de análise do codebase em `.specs/codebase/`.
**Reason:** Necessário para entender o projeto antes de planejar features.
**Trade-off:** ~19k tokens de contexto se carregados simultaneamente.
**Impact:** Todos os concerns (6 HIGH, 8 MEDIUM, 12 LOW) foram priorizados no ROADMAP.md.

### AD-004: Filtro doc_types aplicado por seleção de coleções (2026-08-08)

**Decision:** O filtro `doc_types` do `POST /query` agora é aplicado: `run_query` constrói um subconjunto **read-only** das stores antes de `build_retriever`, limitando a recuperação às coleções solicitadas (PR #13).
**Reason:** O campo era aceito e validado pelo schema mas nunca lido — toda consulta varrava todas as 4 coleções. Como a arquitetura adota uma coleção por `DocType`, a filtragem por seleção de coleção dispensa `where` pós-ANN.
**Trade-off:** Dict compartilhado entre requests nunca é mutado; subset por request garante thread-safety. `None`/`[]`/omitido consultam todas (sem regressão).
**Impact:** `generation/chain.py`, `api/routers/query.py`, `api/schemas.py` + testes unitários e de aceite (`tests/acceptance/test_doc_types_filter.py`). 193 testes, ~96% cov.

### AD-006: Docs realinhadas ao código (FIX-04) (2026-08-08)

**Decision:** CLAUDE.md e README.md corrigidos (PR #14): OpenAI GPT-4o → **LM Studio** (phi-3-mini / nomic-embed-text / `LM_STUDIO_BASE_URL`), retriever **L2** (não MMR), loader pdfplumber+PyMuPDF (sem OCR), removidas referências a `scripts/evaluate_rag.py` (inexistente), linter flake8→ruff, field de citação `document`→`source`, exemplos em minúsculo.
**Reason:** A documentação divergia da implementação real e confundia onboarding e gate checks.
**Trade-off:** Removida a linha "Avaliação RAG | RAGAS + datasets" (módulo de eval é stub vazio), ainda que `ragas`/`datasets` permaneçam em requirements.txt (fora do escopo docs).
**Impact:** 2 arquivos docs; sem mudança de código. FIX-04 fechou também a atualização que o AD-002 marcava como pendente.

### AD-007: Limite de upload no /ingest com streaming early-abort (FIX-05) (2026-08-08)

**Decision:** `POST /ingest` passa a rejeitar uploads acima de `max_upload_mb` (default 25, env `MAX_UPLOAD_MB`) com HTTP 413. O read () integral em memória foi substituído por streaming em chunks de 1MB com early-abort; auth (401) mantém precedência; boundary inclusivo.
**Reason:** O endpoint lia o arquivo inteiro para a memória sem limite — vetor de DoS e sem rejeição clara. Streaming early-abort também limita o pico de memória.
**Trade-off:** Opt por 413 via HTTPException (contrato `detail` dos 4xx existentes) em vez de 200-falso com campo error; `IngestResponse` inalterado. Aceito também verificação por bytes reais (streaming) em vez de confiar em `Content-Length`.
**Impact:** `ingest.py` (helper `_stream_upload_with_limit`), `config.py`, `.env.example`, testes unitários + aceite. 210 testes, ~96% cobertura.

### AD-008: Admin key validation reforçada (FIX-06) (2026-08-08)

**Decision:** `Settings` rejeita chaves fracas/placeholder (<16 chars, `dev-only`, `troque-por-chave-segura`) via primeiro `field_validator` do codebase (helper `admin_key_is_weak`). `verify_admin_key` normaliza header vazio/whitespace → 401 (guard booleano, sem oráculo de timing), ausente → 422 (preservado), errado → 401. `main.py` lifespan emite `logger.warning` sem vazar a chave quando chave fraca em uso.
**Reason:** A chave tinha default inseguro, sem validação em tempo de config, comportamento de header inconsistente e sem aviso de startup — facilitava deploy com credencial fraca.
**Trade-off:** Manter o default `dev-only` no campo faz `Settings()` sem `.env` falhar rápido (fail-fast) — intencional; testes usam `ADMIN_API_KEY` forte via env. Warning do startup é belt-and-suspenders (validador já rejeita o que avisa), testado via Mock que bypassa a construção.
**Impact:** `config.py`, `api/main.py`, `api/routers/ingest.py`, `.env.example`, fixtures/tests. 234 testes, ~96% cobertura. FIX-05 preservado.

### AD-009: CORS middleware configurável (FIX-07) (2026-08-08)

**Decision:** Adicionado `CORSMiddleware` no `api/main.py`, construído a partir de novos campos de `Settings` (`cors_allow_origins`/`cors_allow_methods`/`cors_allow_headers` como CSV e `cors_allow_credentials` bool), com defaults permissivos (`*`) para habilitar integrações browser (Streamlit/ferramentas) sem espera de configuração. Novo helper `csv_list` normaliza CSV→list tratando `*` e vazia como `["*"]`.
**Reason:** A API não tinha CORS — qualquer integração browser (UI em outra porta/origem, ferramentas locais) era bloqueada pelo navegador, inviabilizando uso além do curl.
**Trade-off:** Default `*` é permissivo para dev; o deploy deve restringir via env para origens conhecidas (mitigação de segurança explícita no `.env.example`). `allow_credentials=False` por padrão para evitar o par inválido `credentials + origins=["*"]` do browser.
**Impact:** `config.py` (4 campos + `csv_list`), `api/main.py` (add_middleware), `.env.example`, `tests/api/test_cors.py`. 246 testes, ~96% cobertura. Geometria herdada das FIX anteriores preservada.

### AD-011: Avaliação RAG offline com RAGAS (OBS-03) (2026-08-11)

**Decision:** Implementar avaliação offline do pipeline RAG com RAGAS 0.2.15 (context_precision/context_recall/faithfulness/answer_relevancy) via pacote `src/medasist/evaluation/` (dataset.py + metrics.py), CLI `scripts/evaluate_rag.py` e golden set sintético versionado `evals/dataset/golden_set.json`. LLM/embeddings do judge apontam apenas para o LM Studio (`eval_llm_model`/`eval_embedding_model`, temperatura 0.0). Cold starts são excluídos das agregadas de retrieval e geração (`num_retrieval_evaluated`/`num_generation_evaluated`). Sem alterações em `retriever.py`/`chain.py`/`api/` além do helper compartilhado `select_collections` (retriever.py) usado tanto por `run_query` quanto pelo eval (refatoração revisada e aprovada).
**Reason:** Não havia mecanismo para medir regressões de qualidade no RAG; alterações em retriever/prompts/dados passavam despercebidas. AD-010 já havia decidido adotar `evals/` para golden set + métricas.
**Trade-off:** Judge = mesmo modelo que gera (phi-3-mini) — viés de autogratificação aceito e documentado; `--profile` CLI com default None respeita profile por pergunta do golden set; double-retrieval (retrieve + run_query) por pergunta aceito e documentado (GenerationResult não carrega contexts).
**Impact:** 4 novas settings (`eval_golden_set_path`, `eval_llm_model`, `eval_embedding_model`, `eval_batch_size`), `.env.example`, `.gitignore` (`evals/results/`), docs, 4 arquivos de teste. 378 testes, 97.23% cobertura. PR #20.

### AD-012: RAG-04 section/page nas citações + guarda lexical de fármacos (2026-08-18)

**Decision:** Implementar RAG-04 (PR #22) em duas partes: (1) propagation de página/seção — chunking por página no `chunker.py` (TextChunk ganha `page`/`section`), detecção de títulos de seção por numeração hierárquica ou caixa alta, persistência no ChromaDB (página 0 como sentinela para `None`, que o ChromaDB rejeita) e exibição da página real na citação (sentinela 0 → vazio); (2) guarda lexical no retriever que trata como cold start consultas mencionando um fármaco (termo com sufixo de droga) cujo termo não aparece em nenhum chunk recuperado — impede o LLM de alucinar doses de outro medicamento com embeddings similares. Configs em `Settings`: `section_heading_min_len/max_len`, `retrieval_stopwords`, `retrieval_drug_suffixes`, `retrieval_drug_term_min_len`. Dev tooling: `make dev-local` (`scripts/run_local.ps1`) e `scripts/smoke_test.py`.
**Reason:** Citações exibiam apenas a fonte, sem a seção/página prometidas no roadmap; e o retrieval podia recuperar chunks de outro fármaco por similaridade de embedding, levando o LLM a alucinar (risco médico). A guarda lexical corta isso com custo zero de LLM.
**Trade-off:** Guarda tem falso-negativo esperado — mesmo fármaco com nomes distintos (paracetamol vs acetaminofeno) vira cold start (seguro, sem alucinação). Chunking por página perde overlap entre páginas. Heurística de seção aceita ruído (rodapés numéricos como `12 / 34`, cabeçalhos ALL-CAPS repetidos).
**Impact:** `chunker.py`, `metadata.py`, `pipeline.py`, `citations.py`, `retriever.py`, `config.py`, `Makefile`, `scripts/run_local.ps1`, `scripts/smoke_test.py`, testes. 402 testes, 97.42% cobertura. PR #22. OBS-04 (retry/backoff) mergeado no mesmo dia (PR #21), fechando M3.

### AD-010: Manter estrutura de diretórios atual; adotar apenas `evals/` da estrutura optional (2026-08-11)

**Decision:** Rejeitar a estrutura "optional" do `STRUCTURE.md` (desenhada para sistema agêntico: `agents/`, `tools/web_search.py`, `code_executor.py`, LangGraph) e manter a estrutura atual espelhando as camadas do pipeline RAG (`ingestion/`, `vectorstore/`, `retrieval/`, `generation/`, `profiles/`, `api/`, `ui/`). Adotar da estrutura optional apenas o conceito de `evals/` (golden set + métricas) para OBS-03, sem renomear `evaluation/` nem criar `utils/` (logging_setup já cobre).
**Reason:** MedAssist é um pipeline RAG linear LCEL com LLM local obrigatório — sem orquestração agêntica, sem busca web, sem execução de código. A estrutura atual espelha a arquitetura real documentada no CLAUDE.md, mantém o padrão de testes espelhando `src/` 1:1 e o layout `src/medasist/` exigido pelo `python -m uvicorn medasist.api.main:app`. A optional foi projetada para outro produto (meu-rag-agente).
**Trade-off:** Não ganhamos `config/prompts.py` (prompts continuam em `generation/prompts.py`), `database/vector_store.py` (continua `vectorstore/store.py`) nem `utils/logger.py` (substituído por `logging_setup.py` do OBS-01). Evita migração de ~16 módulos, 267 testes e Dockerfiles sem ganho funcional.
**Impact:** Nenhuma mudança de código. Atualização do `STRUCTURE.md` com a recomendação e registro deste ADR. OBS-03 deve implementar `evals/dataset/golden_set.json` + `scripts/evaluate_rag.py` (citado mas inexistente).

### AD-005: Limites de código e fluxo de review no CLAUDE.md (2026-08-08)

**Decision:** Documentar oficialmente no CLAUDE.md os limites de código (funções até 50 linhas, aninhamento até 4 níveis, arquivos até 800 linhas) e apontar o fluxo de code review para o skill `code-reviewer`.
**Reason:** Os agentes backend-builder e o skill ship-feature já citavam "CLAUDE.md rule" para os limites de 50/4/800, mas o CLAUDE.md não os definia — referência órfã. O CLAUDE.md agora é a fonte canônica.
**Trade-off:** Regra explícita obriga a modularizar acima dos limites; alinhado ao padrão existente.
**Impact:** CLAUDE.md atualizado no PR #13 (commit docs `2340ed0`). O `run_query` inteiro (~100 linhas) excede 50 linhas, mas é pré-existente; refatoração fora do escopo (ver FIX-04/ad-hoc).

---

## Active Blockers

Nenhum blocker ativo.

---

## Lessons Learned

### L-001: Skills e agentes referenciavam projeto errado (2026-08-07)

**Context:** Skills e agentes em `.claude/` faziam referência a `agenticlog` (outro projeto).
**Problem:** Paths, config, pytest commands, LangGraph/AgentState patterns não aplicavam ao medasist.
**Solution:** Adaptados todos os 5 skills e 8 agentes: paths, gate checks, mock patterns, naming conventions.
**Prevents:** Agentes executando comandos errados, editando arquivos inexistentes, ou aplicando padrões que não existem no projeto.

### L-002: [tool.flake8] no pyproject.toml é config fantasma (2026-08-07)

**Context:** flake8 não lê pyproject.toml — a config em [tool.flake8] era ignorada.
**Problem:** Desenvolvedores podiam achar que mudar a config lá teria efeito.
**Solution:** Removido [tool.flake8], migrado para [tool.ruff] que lê pyproject.toml nativamente.
**Prevents:** Config silenciosamente ignorada.

### L-003: Validator pega scope violation — gate checks antes do commit (2026-08-07)

**Context:** FIX-01 commitou 16 arquivos num único commit; o validator apontou que só 2 eram do escopo (os outros 14 eram ruff/black fixes de base).
**Problem:** Misturar mudanças de base com o fix de feature contamina o diff e o histórico.
**Solution:** `git reset --soft HEAD~1` + separar em 2 commits: `fix:` (escopo) e `chore:` (base/formatação).
**Prevents:** Commits com escopo vazado. Emendar (`--amend`) permitido **apenas antes do push**; após push, novo commit.

### L-004: Opção A (def sync) melhor que Opção B (ainvoke) para endpoint bloqueante (2026-08-07)

**Context:** FIX-02 — `/query` era `async def` chamando chain sync, bloqueando o event loop.
**Problem:** Corrigir com `ainvoke` não resolve porque retriever/ChromaDB são síncronos.
**Solution:** Mudar `async def query` para `def query` — FastAPI roda rotas sync em threadpool.
**Prevents:** Complexidade desnecessária; decisão ancorada em evidência (o retriever é sync).

### L-005: Skill code-reviewer ≠ validator; usar ambos (2026-08-08)

**Context:** FIX-03 — o feature-factory exige code review via skill `review-pr`, mas este ambiente não o expõe como subagente; o `code-reviewer` foi adicionado como skill.
**Problem:** Validador e code reviewer têm papéis distintos: o **validator** checa conformidade vs. story/spec (critérios, escopo, riscos); o **code reviewer** checa qualidade de engenharia (clareza, acoplamento, idiomatismo, blast radius). Um não substitui o outro.
**Solution:** Rodar o validator (pipeline) e, em paralelo, aplicar o skill `code-reviewer` para o olhar de engenharia sobre o diff.
**Prevents:** Mergiar código que cumpre o contrato mas tem dívida de design não revisada. Veredicto de merge unificado exigiu os dois.

---

## Quick Tasks Completed

| #   | Description                          | Date       | Status  |
| --- | ------------------------------------ | ---------- | ------- |
| 001 | Migração flake8 -> ruff              | 2026-08-07 | Done    |
| 002 | Brownfield mapping (7 docs)          | 2026-08-07 | Done    |
| 003 | Adaptação skills/agentes (agenticlog -> medasist) | 2026-08-07 | Done    |
| 004 | FIX-01: get_client() sem argumento (PR #11) | 2026-08-07 | Done |
| 005 | FIX-02: endpoint /query síncrono (PR #12) | 2026-08-07 | Done |
| 006 | Sync main + cleanup worktrees/branchs FIX PRs | 2026-08-07 | Done |
| 007 | FIX-03: doc_types filter aplicado (PR #13) | 2026-08-08 | Done |
| 008 | FIX-04: docs realinhadas ao código (PR #14) | 2026-08-08 | Done |
| 009 | FIX-05: limite de upload no /ingest (PR #15) | 2026-08-08 | Done |
| 010 | FIX-06: admin key validation (PR #16) | 2026-08-08 | Done |
| 011 | FIX-07: CORS middleware | 2026-08-08 | Done |
| 012 | OBS-01: logging estruturado JSON (PR #18) | 2026-08-11 | Done |
| 013 | AD-010: manter estrutura atual + adotar evals/ (OBS-03) | 2026-08-11 | Done |
| 014 | OBS-03: avaliação RAG offline com RAGAS (PR #20) | 2026-08-11 | Done |
| 015 | OBS-04: retry/backoff para LM Studio (PR #21) | 2026-08-18 | Done |
| 016 | RAG-04: citações com página/seção + guarda lexical (PR #22) | 2026-08-18 | Done |

---

## Deferred Ideas

- [ ] Streaming de respostas (SSE) — capturado durante: mapeamento
- [ ] Fine-tuning de embeddings com vocabulário médico — capturado durante: mapeamento
- [ ] Internacionalização (EN/ES) — capturado durante: mapeamento
- [ ] Cache de embeddings — capturado durante: mapeamento

---

## Todos

- [x] FIX-01: bug get_client() sem argumento (P0) — PR #11 merged
- [x] FIX-02: sync-in-async event loop (P0) — PR #12 merged
- [x] FIX-03: doc_types filter ignorado (P0) — PR #13 merged (AD-004)
- [x] FIX-04: Atualizar CLAUDE.md/README (AD-002/AD-006) — PR #14 merged
- [x] FIX-05: Limite de upload no /ingest (AD-007) — PR #15 merged
- [x] FIX-06: Admin key validation (AD-008) — PR #16 merged
- [x] FIX-07: CORS middleware (AD-009) — PR #17 merged
- [x] OBS-04: retry/backoff para LM Studio (AD-012) — PR #21 merged
- [x] RAG-04: citações com página/seção + guarda lexical (AD-012) — PR #22 merged

---

## Preferences

**Model Guidance Shown:** never
