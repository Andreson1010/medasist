# Spec — OBS-01: Configurar logging estruturado

**Slug:** `obs-01-logging-estruturado`
**Escopo TLC:** medium
**Story aprovada:** Checkpoint 1 (2026-08-11) — ver STATE.story.
**Decisiones das perguntas em aberto (aprovadas):**
1. Um arquivo por entry point: `api.log` e `ui.log` dentro de `LOG_DIR`.
2. Campo fixo `app` presente (valor `medassist`); sem `environment`.
3. Registro de retrieval **consolidado por query** (um único JSON por query com a lista de scores), não por store.
4. `doc_types` filtrado do `QueryRequest` é refletido no log.
5. UI respeita `LOG_LEVEL` (mesmo mecanismo da API, sem nível fixo).

---

## Contexto

Hoje não há configuração centralizada de logging: cada módulo faz `logging.getLogger(__name__)` contra um root logger não configurado (handler lastResort do Python → WARNING+ em stderr). `scripts/ingest_docs.py` é o único que chama `basicConfig`. Os campos `log_level`/`log_dir` já existem no `Settings` (`src/medasist/config.py:184-185`) mas ninguém os consome. `python-json-logger==3.3.0` está em `requirements.txt` e `requirements-api.txt`, mas não em `requirements-ui.txt`. Não há medição de latência em lugar nenhum. `retrieve()` calcula scores (L2) mas descarta (`retriever.py:164`).

## Objetivo

Centralizar a configuração de logging estruturado (JSON via `python-json-logger`), aplicá-la nos entry points (API + UI), respeitar `LOG_LEVEL`/`LOG_DIR` do `Settings`, e registrar métricas de retrieval (chunks + scores + latência) por query — preservando cold start e as regras de segurança (sem dados de paciente em logs, lazy `%s` formatting).

## Arquitetura da solução

Novo módulo `src/medasist/logging_setup.py` com função `configure_logging(settings, app_name) -> str | None` (idempotente, thread-safe, retorna o path do arquivo ou `None`). Chamado no `lifespan` da API (`api/main.py`) e no `main()` da UI (`ui/app.py`) — antes de qualquer log relevante.

### Design do handler

- Formatter: `pythonjsonlogger.json.JsonFormatter` com campos: `asctime`, `levelname`, `logger` (`name`), `message` (`message`), `app`.
- Handler: `logging.FileHandler(log_dir / f"{app_name}.log", encoding="utf-8")` em modo append.
- Nível: `settings.log_level` (validado) aplicado no handler **e** no root logger.
- Stdout/stderr: preserva o handler lastResort do Python? Não — um `StreamHandler` com formato texto simples é adicionado para manter logs visíveis no console (dev/container), com o mesmo nível. Decisão: adicionar `StreamHandler` texto (não-JSON) para não poluir o stdout de ferramentas que esperam texto; o arquivo é JSON.
- Idempotência: guard por atributo no módulo (`_configured: dict[str, bool]` por `app_name`) + remoção explícita de handlers repetidos. `logging.getLogger()` de cada módulo é filho do root; configurar o root uma única vez por entry point.

### Path de arquivo

`LOG_DIR` default `Path("./logs")` — pasta **na raiz do projeto, fora de `src/`** (caminho relativo ao cwd do processo, não dentro do pacote). O setup é uma utilidade centralizada no código (`src/medasist/logging_setup.py`) e os arquivos são **locais** (`api.log`, `ui.log`), não uma infra externa de coleta. `mkdir(parents=True, exist_ok=True)` no setup. Arquivo: `api.log` para API, `ui.log` para UI. Em `docker-compose.yml` o volume `./logs:/app/logs` já monta o dir para a API.

### Validação de LOG_LEVEL

Campo `log_level` do `Settings` validado via `field_validator`: aceita `DEBUG, INFO, WARNING, ERROR, CRITICAL` (case-insensitive, normaliza para upper). Valor inválido → `ValueError` (fail fast). Reuso do padrão do AD-008 (primeiro `field_validator`).

## Arquivos que serão alterados

| Arquivo | Mudança |
|---|---|
| `src/medasist/config.py` | Novo `field_validator` para `log_level` (validação + normalização). |
| `src/medasist/logging_setup.py` | **Novo** — `configure_logging(settings, app_name)` idempotente. |
| `src/medasist/api/main.py` | Chamar `configure_logging(settings, "api")` no `lifespan`. |
| `src/medasist/ui/app.py` | Chamar `configure_logging(settings, "ui")` no `main()`. |
| `src/medasist/retrieval/retriever.py` | Medir latência e logar métrica consolidada por query (chunks, scores, latency_ms, cold_start). |
| `src/medasist/api/routers/query.py` | Incluir `latency_ms` total e `doc_types` no log por query (ou garantir consistência com o novo log de retrieval). |
| `requirements-ui.txt` | Adicionar `python-json-logger==3.3.0`. |
| `docker/ui.Dockerfile` | Criar `/app/logs` (mkdir) para a UI gravar JSON. |
| `docker-compose.yml` | Opcional/avaliar: montar `./logs` na UI. |

## Mudanças de modelo de dados

Nenhuma mudança em schema de API ou banco. Apenas:
- `Settings.log_level` ganha validação (quebra de contrato: valor inválido falha no startup — intencional, fail-fast).
- Novo campo opcional no log (não em API): `app`, `latency_ms`, `scores`, `doc_types`, `cold_start`.

## Detalhe da métrica de retrieval (AC6)

Em `retriever.retrieve()`:
- `start = time.perf_counter()` antes do loop de stores; `elapsed_ms` após.
- Log consolidado por query (após dedup e corte em `top_k`):
  - `query` truncada a 50 chars (padrão existente do retriever),
  - `doc_types` (listas de nomes das stores consultadas),
  - `chunks` = `len(top_docs)`,
  - `scores` = lista de distâncias L2 dos documentos retornados (mantidas de `seen`/candidates),
  - `latency_ms` = inteiro (ms),
  - `cold_start` = `chunks == 0`.
- O retorno público de `retrieve()` permanece `list[Document]` (sem breaking change de contrato; scores passam a ser capturados antes do descarte). O `top_docs` atual é construído descartando scores (`retriever.py:164`); a mudança mantém o par `(doc, score)` paralelo ao `top_docs` para alimentar o log.

O endpoint `query.py` mantém seu log existente e adiciona `latency_ms` (total da chain) e `doc_types` do request, mantendo `profile`, `cold_start`, `citations`.

## Requisitos (IDs traçam aos ACs da story)

| ID | AC | Requisito |
|---|---|---|
| REQ-1 | AC1 | `logging_setup.configure_logging` é o único mecanismo de setup (chamado por API e UI). |
| REQ-2 | AC2 | API grava JSON em `log_dir/api.log` com `asctime/levelname/logger/message/app`. |
| REQ-3 | AC3 | UI grava JSON em `log_dir/ui.log` com o mesmo formato. |
| REQ-4 | AC4 | `LOG_LEVEL` do Settings controla o nível efetivo (handler + root). |
| REQ-5 | AC5 | `log_dir` é criado com `parents=True, exist_ok=True` se não existir. |
| REQ-6 | AC6 | `retrieve()` loga métrica consolidada por query: `query` (50), `doc_types`, `chunks`, `scores`, `latency_ms`, `cold_start`. |
| REQ-7 | AC7 | Cold start: log `cold_start=true, chunks=0, scores=[]`; resposta fixa sem LLM preservada. |
| REQ-8 | Edge 3 | Escrita em UTF-8 (`encoding="utf-8"`) — unicode/emoji não quebram JSON. |
| REQ-9 | Edge 4 | Latência medida também quando um store falha; falha logada com `failed_stores`, sem interromper o fluxo. |
| REQ-10 | Edge 6 | Nenhum dado de paciente em logs; apenas `query` do usuário e metadados sintéticos. |
| REQ-11 | Edge 7 | `configure_logging` é idempotente (guarda por `app_name`; re-exec not Streamlit não empilha handlers). |
| REQ-12 | Edge 1 | `LOG_DIR` ausente → default `Path("./logs")` do Settings. |
| REQ-13 | Edge 2 | `LOG_LEVEL` inválido → `ValueError` fail-fast. |

## Riscos

| Risco | Nível | Mitigação |
|---|---|---|
| Empilhar handlers no root (Streamlit re-executa script a cada interação) | Médio | Guard idempotente por `app_name`; remover handlers com mesmo `_name` antes de adicionar. |
| Quebra de contrato no `Settings` (log_level inválido) | Baixo | Fail-fast intencional; `.env.example` já documenta valores válidos. |
| Scores perdidos no retriever | Médio | Capturar pares `(doc, score)` antes do descarte em `retriever.py:164`; sem mudar assinatura pública. |
| UI em Docker sem `/app/logs` | Baixo | `mkdir` no `ui.Dockerfile`; avaliar volume no compose. |
| Log de `query` pode conter PII | Baixo | Regra de segurança: apenas texto da pergunta, truncado; nenhum metadado de paciente logado. |

## Perguntas em aberto

Nenhuma (as 5 da story foram decididas e aprovadas no Checkpoint 1).

---

**APROVAÇÃO:** conforme decisão do Checkpoint 2.
