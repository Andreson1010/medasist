# Plano — Fase 7: UI Streamlit (MedAssist)

## Context

A Fase 7 adiciona a camada de interface ao pipeline RAG MedAssist. As fases anteriores entregaram toda a lógica de negócio e a API REST (Fases 1–6). Esta fase apenas consome o endpoint `POST /query` via httpx — sem nova lógica de domínio, sem acesso direto ao LLM ou ao ChromaDB.

---

## Estrutura de Arquivos

```
src/medasist/ui/
    __init__.py      (existia, vazio)
    client.py        (NOVO — wrapper httpx tipado)
    app.py           (NOVO — interface Streamlit)

tests/ui/
    __init__.py      (NOVO)
    conftest.py      (NOVO — fixture base_url)
    test_client.py   (NOVO — 16 testes unitários)
```

---

## Ordem de Implementação

1. `src/medasist/ui/client.py` — sem deps de Streamlit, testável em isolamento
2. `tests/ui/conftest.py` + `tests/ui/test_client.py` — verificar client antes de seguir
3. `src/medasist/ui/app.py` — depende do client estável

---

## Detalhamento por Arquivo

### `src/medasist/ui/client.py`

#### Hierarquia de exceções

```python
class APIError(Exception): ...
class RateLimitError(APIError): ...      # HTTP 429
class ServerError(APIError): ...         # HTTP 5xx
class RequestTimeoutError(APIError): ... # httpx.TimeoutException
```

> `RequestTimeoutError` (não `TimeoutError`) para evitar shadowing do built-in Python.

#### DTOs — frozen dataclasses

```python
@dataclass(frozen=True)
class CitationResult:
    index: int
    source: str
    section: str
    page: str

@dataclass(frozen=True)
class QueryResult:
    answer: str
    citations: list[CitationResult]
    profile: str
    disclaimer: str
    is_cold_start: bool
```

#### Funções públicas

```python
def check_health(base_url: str | None = None) -> bool
    # GET /health — nunca levanta exceção; retorna False em qualquer falha

def query(
    question: str,
    profile: str,
    doc_types: list[str] | None = None,
    base_url: str | None = None,
    timeout: float = 30.0,
) -> QueryResult
    # POST /query — levanta RateLimitError/ServerError/APIError/RequestTimeoutError
```

#### Notas de implementação

- `base_url` default: `get_settings().api_base_url`
- `httpx.Client` construído **dentro** da função (nunca no nível do módulo — seguro para reruns do Streamlit)
- `check_health`: captura toda exceção → retorna `False`; flag de health marcado após a chamada
- `query`: `with httpx.Client(...) as client:` → 429 → `RateLimitError`, 5xx → `ServerError`, `httpx.TimeoutException` → `RequestTimeoutError`, demais não-2xx → `APIError`

---

### `src/medasist/ui/app.py`

#### Constantes

```python
_MAX_QUESTION_LEN = 500

PROFILE_LABELS: dict[str, str] = {
    "medico": "Médico", "enfermeiro": "Enfermeiro",
    "assistente": "Assistente", "paciente": "Paciente",
}
DOC_TYPE_LABELS: dict[str, str] = {
    "bula": "Bula", "diretriz": "Diretriz",
    "protocolo": "Protocolo", "manual": "Manual",
}

_KEY_MESSAGES      = "messages"
_KEY_HEALTH_CHECKED = "_health_checked"
```

#### Funções

| Função | Responsabilidade |
|--------|-----------------|
| `main()` | Entry point — orquestra tudo |
| `_configure_page()` | `st.set_page_config(...)` |
| `_render_sidebar(settings) -> tuple[str, list[str]]` | Selectbox de perfil + multiselect de DocType + disclaimer |
| `_check_and_warn_health(base_url)` | Chama `check_health` uma vez por sessão; flag marcado após chamada |
| `_render_chat_history(settings)` | Replay do histórico via `_render_response` (garante disclaimer/citações) |
| `_render_response(result, settings)` | Resposta + citações ou cold start |
| `_format_citation(c) -> str` | `[N] source — Seção: section, Pág. page` |
| `_handle_error(exc)` | Mapeia subclasses de `APIError` → `st.error`/`st.warning` (sem vazar detalhes internos) |

#### Regras médicas (INEGOCIÁVEIS)

- `disclaimer` e `cold_start_message` lidos de `settings` — nunca hardcoded
- Disclaimer sempre visível no sidebar via `st.caption`
- Cold start (`is_cold_start=True`): exibir `settings.cold_start_message` em `st.warning` + disclaimer em `st.info`; **nunca exibir `result.answer` como mensagem principal**
- Histórico re-renderizado via `_render_response` — disclaimer e citações presentes em toda a sessão
- Validação de tamanho no frontend antes de chamar a API (`len(prompt) > 500` → aviso)

#### Fluxo do `main()`

1. `_configure_page()`
2. `settings = get_settings()`
3. Inicializa `st.session_state[_KEY_MESSAGES] = []` se ausente
4. `profile_key, doc_type_keys = _render_sidebar(settings)`
5. `_check_and_warn_health(settings.api_base_url)`
6. `st.title("MedAssist")` + subtítulo
7. `_render_chat_history(settings)`
8. `if prompt := st.chat_input(...):`
   - Validar `len(prompt) <= 500`
   - Append `{role: user, content: prompt, result: None}` → session state
   - `with st.chat_message("assistant"), st.spinner(...):`
     - `result = query(prompt, profile_key, doc_type_keys or None, settings.api_base_url)`
     - `_render_response(result, settings)`
     - Append `{role: assistant, content: result.answer, result: result}`
     - `except APIError` → `_handle_error(exc)`

---

## Testes

### `tests/ui/conftest.py`

```python
@pytest.fixture
def base_url() -> str:
    return "http://test-api"
```

### `tests/ui/test_client.py` — 16 casos

**TestCheckHealth** (5 casos):

| Teste | Setup | Assert |
|-------|-------|--------|
| `test_returns_true_when_ok` | Mock GET 200 `{"status":"ok"}` | `True` |
| `test_returns_false_when_unhealthy` | Mock GET 200 `{"status":"degraded"}` | `False` |
| `test_returns_false_on_connection_error` | `httpx.ConnectError` | `False` |
| `test_returns_false_on_timeout` | `httpx.TimeoutException` | `False` |
| `test_returns_false_on_500` | Mock GET 500 | `False` |

**TestQuery** (6 casos):

| Teste | Assert |
|-------|--------|
| `test_returns_query_result` | Retorna `QueryResult` com campos corretos |
| `test_citations_parsed_correctly` | 2 citações, campos validados |
| `test_cold_start_flag_propagated` | `is_cold_start=True`, `citations=[]` |
| `test_sends_doc_types_when_provided` | `body["doc_types"] == ["bula"]` |
| `test_sends_null_doc_types_when_none` | `body["doc_types"] is None` |
| `test_profile_sent_correctly` | `body["profile"] == "enfermeiro"` |

**TestQueryErrors** (5 casos):

| HTTP / Exceção | Esperado |
|----------------|----------|
| 429 | `RateLimitError` |
| 500 | `ServerError` |
| 503 | `ServerError` |
| 400 | `APIError` |
| `httpx.TimeoutException` | `RequestTimeoutError` |

**Mocking**: `patch("medasist.ui.client.httpx.Client")` com context manager mock.
Acesso ao body: `mock_instance.post.call_args.kwargs["json"]` (sem fallback posicional).

---

## Módulos a Reutilizar

| Módulo | Import |
|--------|--------|
| Config | `from medasist.config import Settings, get_settings` |
| Client | `from medasist.ui.client import query, check_health, QueryResult, ...` |

---

## Riscos e Mitigações

| Risco | Mitigação |
|-------|-----------|
| `httpx.Client` persistido no módulo causa conflito entre sessões Streamlit | Client criado dentro da função, nunca em module-level |
| `check_health` disparado a cada rerun do Streamlit | Flag `_KEY_HEALTH_CHECKED` em `st.session_state` (marcado após a chamada) |
| Disclaimer ausente no histórico de chat | `_render_chat_history` usa `_render_response` — não `st.markdown` puro |
| `TimeoutError` shadowing built-in | Classe renomeada para `RequestTimeoutError` |
| Mensagem de erro interno vaza ao usuário | `_handle_error` usa strings fixas; detalhes só no `logger.warning` |

---

## Verificação

```bash
# Testes da Fase 7
pytest tests/ui/ -v --cov=src/medasist/ui --cov-fail-under=80

# Suite completa (sem regressão)
pytest tests/ -v --cov=src --cov-fail-under=80

# Subir a UI (API deve estar rodando em localhost:8000)
streamlit run src/medasist/ui/app.py
```
