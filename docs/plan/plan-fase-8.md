# Plano — Fase 8: Script de Ingestão CLI (MedAssist)

## Context

A Fase 8 expõe o pipeline de ingestão (implementado nas Fases 1–3) como um script CLI reutilizável. Não adiciona nova lógica de domínio — apenas orquestra os módulos existentes (`ingestion.pipeline`, `ingestion.schemas`, `config`) com uma interface de linha de comando ergonômica.

---

## Estrutura de Arquivos

```
scripts/
    ingest_docs.py      (NOVO — CLI de ingestão)

tests/scripts/
    __init__.py         (NOVO)
    test_ingest_docs.py (NOVO — 11 testes unitários)
```

---

## Ordem de Implementação

1. `scripts/ingest_docs.py` — CLI independente de Streamlit/FastAPI
2. `tests/scripts/__init__.py` + `tests/scripts/test_ingest_docs.py`

---

## Detalhamento por Arquivo

### `scripts/ingest_docs.py`

#### Argumentos CLI

| Flag | Tipo | Obrigatório | Descrição |
|------|------|-------------|-----------|
| `--dir` | `Path` | Sim | Diretório com PDFs |
| `--doc-type` | `str` (choices) | Sim | Tipo do documento (`bula`, `diretriz`, `protocolo`, `manual`) |
| `--dry-run` | `bool` (flag) | Não | Lista PDFs sem processar |

#### Funções públicas

```python
def parse_args(argv: list[str] | None = None) -> argparse.Namespace
    # Parseia argumentos; choices validados automaticamente pelo argparse

def main(argv: list[str] | None = None) -> int
    # Entry point; retorna 0 (sucesso) ou 1 (erro)
```

#### Fluxo do `main()`

1. Configura `logging.basicConfig` com nível INFO
2. Parseia args via `parse_args(argv)`
3. Valida que `--dir` existe como diretório → retorna `1` se não existir
4. Coleta `*.pdf` no diretório → log de aviso + retorna `0` se vazio
5. Se `--dry-run`: lista PDFs e retorna `0` sem chamar `ingest_directory`
6. Instancia `PersistentClient`, `embed_fn` e chama `ingest_directory`
7. Agrega resultados: `processed`, `skipped`, `errors`
8. Loga sumário e detalhes de erros → retorna `1` se houver erros, `0` caso contrário

#### Notas de implementação

- `httpx.Client` não usado aqui — acesso direto ao ChromaDB local
- Script é executável dentro e fora do container Docker
- Idempotente: `ingest_directory` já ignora arquivos pelo SHA-256

---

## Testes — `tests/scripts/test_ingest_docs.py`

### Grupos de teste

**parse_args** (2 casos):

| Teste | Assert |
|-------|--------|
| `test_parse_args_valid` | `dir`, `doc_type`, `dry_run=False` corretos |
| `test_parse_args_invalid_doc_type` | `SystemExit` levantado |

**main — verificações de entrada** (2 casos):

| Teste | Assert |
|-------|--------|
| `test_main_dir_not_found` | Retorna `1` |
| `test_main_empty_dir` | Retorna `0` |

**main — dry-run** (1 caso):

| Teste | Assert |
|-------|--------|
| `test_main_dry_run` | Retorna `0`; `ingest_directory` não chamado |

**main — ingestão** (6 casos):

| Teste | Assert |
|-------|--------|
| `test_main_success` | Retorna `0` |
| `test_main_with_errors` | Retorna `1` |
| `test_main_skipped_docs` | Retorna `0` |
| `test_main_all_skipped_no_error` | Retorna `0` |
| `test_main_mixed_results` | Retorna `1` (há erro) |

**Mocking**: `mocker.patch("ingest_docs.ingest_directory")` + `get_settings`, `chromadb.PersistentClient`, `build_embed_fn`.

---

## Módulos Reutilizados

| Módulo | Import |
|--------|--------|
| Config | `from medasist.config import get_settings` |
| Pipeline | `from medasist.ingestion.pipeline import build_embed_fn, ingest_directory` |
| Schemas | `from medasist.ingestion.schemas import DocType` |

---

## Verificação

```bash
# Testes da Fase 8
pytest tests/scripts/ -v --cov=scripts --cov-fail-under=80

# Suite completa (sem regressão)
pytest tests/ -v --cov=src --cov-fail-under=80

# Uso local
python scripts/ingest_docs.py --dir data/raw --doc-type bula
python scripts/ingest_docs.py --dir data/raw --doc-type bula --dry-run
```
