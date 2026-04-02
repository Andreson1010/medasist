# Roadmap — MedAssist

**Versão:** 1.0
**Data:** 2026-03-30
**Horizonte:** 2026-Q1 → 2026-Q3

---

## Visão Geral

```
Q1 2026                  Q2 2026                  Q3 2026
├── Fase 1–4 ✅          ├── Fase 5–6 🔄          ├── Fase 7–8 🔄
│   Fundação RAG         │   Generation + API      │   UI + Deploy
│   Ingestion            │   Chain LCEL            │   Streamlit
│   Vectorstore          │   FastAPI endpoints     │   Docker
│   Profiles             │   Rate limiting         │   Avaliação RAGAS
└──────────────────────  └──────────────────────   └──────────────────
```

---

## Status Atual

| Fase | Descrição | Status | Conclusão |
|------|-----------|--------|-----------|
| Fase 1 | Ingestion pipeline | ✅ Concluída | 2026-03-20 |
| Fase 2 | Vectorstore + Retrieval | ✅ Concluída | 2026-03-24 |
| Fase 3 | Testes e qualidade | ✅ Concluída | 2026-03-26 |
| Fase 4 | Profiles | ✅ Concluída | 2026-03-30 |
| Fase 5 | Generation (Chain LCEL) | 🔄 Próxima | — |
| Fase 6 | API (FastAPI) | ⏳ Planejada | — |
| Fase 7 | UI (Streamlit) | ⏳ Planejada | — |
| Fase 8 | Docker + Deploy local | ⏳ Planejada | — |

---

## Fase 5 — Generation (Chain LCEL)

**Período:** 2026-04-01 → 2026-04-14
**Branch:** `feat/generation`

### Objetivo
Implementar a camada de geração de respostas: montar a chain LCEL que conecta retriever, prompt, LLM e parser; validar citações; aplicar cold start.

### Entregas

| Entrega | Descrição | Prioridade |
|---------|-----------|-----------|
| `generation/chain.py` | Chain LCEL: `retriever \| prompt \| LLM \| parser` | Alta |
| `generation/prompts.py` | `PromptRegistry` com template por `UserProfile` | Alta |
| `generation/citations.py` | Validação de citações `[N]` e remoção de órfãs | Alta |
| Testes unitários | Cobertura ≥ 80% para módulo `generation/` | Alta |
| Cold start integrado | Retrieval vazio → mensagem fixa, zero LLM | Alta |

### Critérios de Conclusão
- [ ] Chain responde com citações válidas para todos os 4 perfis
- [ ] Cold start ativado quando score < threshold configurado
- [ ] Disclaimer médico incluído em toda resposta
- [ ] `pytest tests/generation/ --cov-fail-under=80` passando
- [ ] `black` e `flake8` sem erros

---

## Fase 6 — API (FastAPI)

**Período:** 2026-04-15 → 2026-04-28
**Branch:** `feat/api`

### Objetivo
Expor a chain LCEL via FastAPI com endpoints de consulta e ingestão, rate limiting, lifespan de aquecimento e logging estruturado.

### Entregas

| Entrega | Descrição | Prioridade |
|---------|-----------|-----------|
| `api/routers/query.py` | `POST /query` — recebe `QueryRequest`, retorna `QueryResponse` | Alta |
| `api/routers/ingest.py` | `POST /ingest` — protegido por `X-Admin-Key` | Alta |
| `api/routers/health.py` | `GET /health` — liveness check | Média |
| `api/main.py` | Lifespan: aquece chains no startup; registra routers | Alta |
| Rate limiting | slowapi: limite por IP em `/query` | Alta |
| Schemas Pydantic | `QueryRequest`, `QueryResponse`, `CitationItem` | Alta |
| Testes de integração | Endpoints com `httpx.AsyncClient` + `pytest-asyncio` | Alta |
| Logging JSON | `python-json-logger` em todas as requisições | Média |

### Critérios de Conclusão
- [ ] `POST /query` retorna resposta com citações e disclaimer
- [ ] `POST /ingest` rejeita requisições sem `X-Admin-Key`
- [ ] `GET /health` retorna `{"status": "ok"}`
- [ ] Rate limiting bloqueando requisições acima do limite
- [ ] Swagger UI acessível em `http://localhost:8000/docs`
- [ ] Testes de integração com cobertura ≥ 80%

---

## Fase 7 — UI (Streamlit)

**Período:** 2026-04-29 → 2026-05-12
**Branch:** `feat/ui`

### Objetivo
Implementar interface web Streamlit que consome a API FastAPI. Nunca acessar LM Studio diretamente.

### Entregas

| Entrega | Descrição | Prioridade |
|---------|-----------|-----------|
| `ui/app.py` | App Streamlit principal | Alta |
| `ui/client.py` | Cliente httpx para `POST /query` | Alta |
| Seleção de perfil | Dropdown: Médico / Enfermeiro / Assistente / Paciente | Alta |
| Seleção de DocTypes | Multiselect: Bula / Diretriz / Protocolo / Manual | Média |
| Exibição de resposta | Resposta + citações numeradas + disclaimer | Alta |
| Tratamento de cold start | Exibir mensagem adequada ao usuário | Alta |
| Tratamento de erros | Erros de API exibidos de forma amigável | Média |

### Critérios de Conclusão
- [ ] Consulta end-to-end funcionando via UI → API → Chain → ChromaDB
- [ ] Perfis visíveis e funcionais
- [ ] Citações exibidas abaixo da resposta
- [ ] Disclaimer sempre visível
- [ ] Cold start exibe mensagem adequada (não mensagem de erro)
- [ ] UI acessível em `http://localhost:8501`

---

## Fase 8 — Docker + Deploy Local

**Período:** 2026-05-13 → 2026-05-26
**Branch:** `feat/docker`

### Objetivo
Containerizar API e UI; orquestrar com Docker Compose para execução local com um único comando.

### Entregas

| Entrega | Descrição | Prioridade |
|---------|-----------|-----------|
| `Dockerfile.api` | Imagem da API FastAPI | Alta |
| `Dockerfile.ui` | Imagem da UI Streamlit | Alta |
| `docker-compose.yml` | Orquestração base (API + UI + volumes) | Alta |
| `docker-compose.dev.yml` | Override de desenvolvimento (hot-reload) | Média |
| Volume ChromaDB | Persistência do `chroma_db/` entre reinicializações | Alta |
| Volume data/ | Montagem de `data/raw/` para ingestão | Alta |
| Health checks | Docker health check em API e UI | Média |
| `.env` integrado | `env_file` configurado no compose | Alta |

### Critérios de Conclusão
- [ ] `docker compose up` sobe API + UI sem erros
- [ ] `docker compose -f docker-compose.yml -f docker-compose.dev.yml up` com hot-reload
- [ ] ChromaDB persiste entre restarts do container
- [ ] API acessível em `http://localhost:8000/docs`
- [ ] UI acessível em `http://localhost:8501`

---

## Fase 9 — Avaliação RAGAS + Ajustes

**Período:** 2026-05-27 → 2026-06-09
**Branch:** `feat/evaluation`

### Objetivo
Avaliar a qualidade do pipeline RAG com RAGAS. Calibrar threshold, ajustar prompts e estratégias de chunking com base nos resultados.

### Entregas

| Entrega | Descrição | Prioridade |
|---------|-----------|-----------|
| `scripts/evaluate_rag.py` | Script de avaliação com RAGAS | Alta |
| Dataset de avaliação | 20–50 pares pergunta/resposta esperada (sintéticos) | Alta |
| Relatório de métricas | faithfulness, answer_relevancy, context_recall | Alta |
| Calibração de threshold | Ajuste de `RETRIEVAL_SCORE_THRESHOLD` com base nos dados | Alta |
| Ajuste de prompts | Refinamento dos templates por perfil | Média |

### Métricas Alvo

| Métrica RAGAS | Alvo |
|---------------|------|
| Faithfulness | ≥ 0.85 |
| Answer Relevancy | ≥ 0.80 |
| Context Recall | ≥ 0.75 |
| Context Precision | ≥ 0.75 |

---

## Marcos (Milestones)

| Marco | Data Alvo | Critério |
|-------|-----------|---------|
| M1 — RAG End-to-End | 2026-04-28 | Chain + API respondendo com citações |
| M2 — Produto Utilizável | 2026-05-12 | UI funcional + cold start + perfis |
| M3 — Deploy Local | 2026-05-26 | `docker compose up` funciona |
| M4 — Qualidade Validada | 2026-06-09 | RAGAS ≥ alvos definidos |

---

## Dependências entre Fases

```
Fase 1 (Ingestion)
    └── Fase 2 (Vectorstore + Retrieval)
            └── Fase 5 (Generation)
                    └── Fase 6 (API)
                            ├── Fase 7 (UI)
                            └── Fase 8 (Docker)
                                    └── Fase 9 (Avaliação RAGAS)
Fase 4 (Profiles)
    └── Fase 5 (Generation)   ← profiles informa prompts e configs do LLM
```

---

## Riscos e Mitigações

| Risco | Fases Afetadas | Mitigação |
|-------|---------------|-----------|
| Qualidade do LLM local abaixo do esperado | 5, 9 | Avaliar com RAGAS antes de ajustar; considerar modelos maiores |
| OCR ruim em PDFs escaneados | 1 | Rejeitar chunks com < 50 chars; logar documentos problemáticos |
| Cold start muito frequente | 5, 9 | Calibrar threshold na Fase 9; indexar mais documentos |
| Latência alta no LM Studio | 6, 7 | Timeout configurável; streaming de resposta na UI |

---

## Fora do Escopo (v1.0)

- Autenticação de usuários finais
- Deploy em nuvem
- Suporte a formatos além de PDF
- Histórico de conversas por usuário
- Fine-tuning de modelos
