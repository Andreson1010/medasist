---
description: Executa o suite de testes do MedAssist com cobertura.
---

Executa o suite de testes do MedAssist com cobertura.

```bash
pytest tests/ -v --cov=src --cov-fail-under=80
```

Para um módulo específico:

```bash
pytest tests/$ARGUMENTS -v
```
