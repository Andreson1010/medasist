# Python Rules — MedAssist

Stack: Python 3.11 | FastAPI | LangChain LCEL | ChromaDB | Streamlit

## Obrigatório em todo arquivo `.py`

```python
from __future__ import annotations
```

## Paths

Sempre `pathlib.Path`, nunca strings brutas:
```python
# CORRETO
from pathlib import Path
data_dir = Path("data/raw")

# ERRADO
data_dir = "data/raw"
```

## Logging

```python
import logging
logger = logging.getLogger(__name__)

# CORRETO
logger.info("Processando documento %s", doc_id)

# ERRADO
print(f"Processando {doc_id}")
```

## Docstrings

Estilo NumPy em todas as funções e classes públicas:
```python
def chunk_document(text: str, doc_type: DocType) -> list[str]:
    """
    Divide documento em chunks por estratégia de DocType.

    Parameters
    ----------
    text : str
        Texto extraído do PDF.
    doc_type : DocType
        Tipo do documento para selecionar estratégia de chunking.

    Returns
    -------
    list[str]
        Lista de chunks de texto.
    """
```

## Configuração

Toda configuração via `src/medasist/config.py` (pydantic-settings).
Nunca hardcodar valores mágicos — referenciar `settings.*`.

## LLM / LangChain

LM Studio como provider (não OpenAI diretamente):
```python
from langchain_openai import ChatOpenAI

llm = ChatOpenAI(
    base_url="http://localhost:1234/v1",
    api_key="lm-studio",
    model="phi-3-mini",
)
```

## Regras de Segurança Médica (INEGOCIÁVEIS)

1. Toda resposta da API inclui o disclaimer médico
2. Retrieval vazio → mensagem fixa, nunca resposta gerada (cold start)
3. Toda resposta cita ao menos uma fonte `[N]`
4. Nenhum dado real de paciente em código, testes ou logs
