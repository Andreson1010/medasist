# Plano — Fase 9: Infraestrutura Docker (MedAssist)

## Context

A Fase 9 containeriza o MedAssist para execução reproduzível em qualquer ambiente. Não adiciona lógica de domínio — apenas empacota os serviços API (FastAPI) e UI (Streamlit) já implementados nas Fases 6–7, com suporte a hot reload para desenvolvimento e imagens enxutas para produção.

---

## Estrutura de Arquivos

```
docker/
    api.Dockerfile          (NOVO — multi-stage: builder + runtime)
    ui.Dockerfile           (NOVO — single-stage)

docker-compose.yml          (NOVO — stack de produção)
docker-compose.dev.yml      (NOVO — override de desenvolvimento)
Makefile                    (NOVO — atalhos operacionais)
README.md                   (NOVO — documentação do projeto)
requirements-api.txt        (NOVO — deps exclusivas da API)
requirements-ui.txt         (NOVO — deps exclusivas da UI)
```

---

## Detalhamento por Arquivo

### `docker/api.Dockerfile` — Multi-stage

| Stage | Base | Responsabilidade |
|-------|------|-----------------|
| `builder` | `python:3.11-slim` | Instala `build-essential`, cria venv, instala `requirements-api.txt` |
| `runtime` | `python:3.11-slim` | Copia venv do builder, copia `src/`, `pyproject.toml`, `scripts/` |

**Segurança:**
- Usuário não-root: `appuser` (uid 1001)
- Diretórios criados com ownership correto: `/app/chroma_db`, `/app/data/raw`, `/app/logs`

**Healthcheck:**
```
CMD curl -f http://localhost:8000/health || exit 1
interval: 30s | timeout: 10s | start_period: 60s | retries: 3
```

**Porta exposta:** `8000`

---

### `docker/ui.Dockerfile` — Single-stage

- Base: `python:3.11-slim`
- Instala `requirements-ui.txt` em venv isolado
- Usuário não-root: `appuser` (uid 1001)
- Healthcheck via `/_stcore/health`
- Porta exposta: `8501`

---

### `docker-compose.yml` — Stack de produção

| Serviço | Portas | Volumes | Env |
|---------|--------|---------|-----|
| `api` | `8000:8000` | `chroma_db`, `data/raw` (ro), `logs` | `.env` + `LM_STUDIO_BASE_URL` |
| `ui` | `8501:8501` | — | `.env` + `API_BASE_URL=http://api:8000` |

**Dependência:** `ui` aguarda `api` com `condition: service_healthy`
**Rede:** `medasist_net` (bridge)
**LM Studio:** acessado via `host.docker.internal:1234` com `extra_hosts: host-gateway`

---

### `docker-compose.dev.yml` — Override de desenvolvimento

Sobrescreve apenas o necessário para hot reload:

| Serviço | Volume extra | Command |
|---------|-------------|---------|
| `api` | `./src:/app/src` | uvicorn com `--reload --reload-dir /app/src` |
| `ui` | `./src/medasist/ui:/app/src/medasist/ui` | streamlit com `--server.runOnSave=true` |

---

### `Makefile` — Atalhos operacionais

| Target | Comando | Descrição |
|--------|---------|-----------|
| `up` | `docker compose up -d` | Sobe stack em background |
| `down` | `docker compose down` | Para e remove containers |
| `build` | `docker compose build --no-cache` | Reconstrói imagens |
| `logs` | `docker compose logs -f` | Segue logs em tempo real |
| `dev` | `docker compose -f ... -f ... up` | Sobe com hot reload |
| `ingest` | `docker compose exec api python scripts/ingest_docs.py ...` | Ingestão dentro do container |
| `ingest-local` | `python scripts/ingest_docs.py ...` | Ingestão fora do container |
| `test` | `pytest tests/ -v --cov=src --cov-fail-under=80` | Suite completa |
| `lint` | `flake8 src/ tests/ scripts/` | Lint |
| `format` | `black src/ tests/ scripts/` | Formatação |
| `check` | `curl` em `/health` e `/_stcore/health` | Smoke test E2E |

---

## Decisões de Design

| Decisão | Motivo |
|---------|--------|
| Multi-stage apenas na API | API tem `build-essential` para compilar deps nativas; UI não precisa |
| `requirements-api.txt` e `requirements-ui.txt` separados | Minimiza tamanho das imagens — UI não carrega LangChain/ChromaDB |
| `chroma_db` como volume nomeado | Persiste vetores entre restarts sem comprometer imutabilidade da imagem |
| `data/raw` montado como `:ro` | Documentos são somente-leitura no container — ingestão não modifica os PDFs |
| `host.docker.internal` para LM Studio | LM Studio roda no host; container acessa via gateway da rede bridge |

---

## Verificação

```bash
# Subir em produção
make up

# Subir em desenvolvimento (hot reload)
make dev

# Verificar serviços
make check

# Ingerir documentos (container)
make ingest

# Parar tudo
make down
```

**URLs:**
- API docs: http://localhost:8000/docs
- UI: http://localhost:8501
