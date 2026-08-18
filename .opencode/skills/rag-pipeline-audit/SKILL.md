---
name: rag-pipeline-audit
description: >
  Audita um sistema RAG de ponta a ponta — os 12 estágios do pipeline (ingestão,
  parsing, chunking, embeddings, vector DB, metadata filtering, hybrid search, query
  transformation, re-ranking, context construction, observabilidade, avaliação), as
  métricas das camadas de Retrieval e Generation, e a estratégia de chunking — e
  produz um relatório em docs/ com recomendações priorizadas por desempenho,
  escalabilidade, confiabilidade e custo. Seja proativo: dispare este skill sempre que
  o usuário pedir para "analisar", "auditar", "avaliar", "revisar", "melhorar",
  "diagnosticar" ou "medir a qualidade" de um RAG, retrieval, busca semântica,
  embeddings, chunking, vector DB ou da qualidade das respostas — mesmo que não diga
  "auditoria" explicitamente. Conhece o MedAssist (LangChain LCEL + ChromaDB +
  RecursiveCharacterTextSplitter per DocType + LM Studio) mas generaliza para qualquer RAG.
---

# RAG Pipeline Audit

Antes do LLM gerar uma resposta, um pipeline inteiro toma decisões. Cada estágio é uma
alavanca de qualidade, custo e latência — e a maioria das falhas de um RAG ("alucinou",
"não achou", "resposta vaga") nasce **antes** da geração, no retrieval. Este skill mapeia
o sistema estágio a estágio, mede o que dá para medir, e devolve um plano de mudança
fundamentado — não palpites.

A regra de ouro: **meça antes de recomendar.** Uma recomendação de chunking sem um número
de Context Recall é chute. Sempre que possível, ancore cada recomendação num sintoma
observado (uma métrica baixa, um trecho de código, um chunk ruim concreto).

## Quando usar

Dispare para qualquer pedido de analisar/auditar/avaliar/melhorar a qualidade de um RAG,
retrieval, busca semântica, embeddings, chunking, vector DB, ou da qualidade das respostas.
Não exige a palavra "auditoria".

## Fluxo (3 fases)

Execute as fases em ordem. As Fases 1 e 2 alimentam o relatório da Fase 3. Se o ambiente
não permitir a Fase 2 (sem LLM no ar, sem corpus indexado), **não aborte** — entregue a
análise estática (Fase 1) + um **plano de avaliação** no lugar das métricas, e diga
claramente no relatório que as métricas estão pendentes.

### Fase 0 — Escopo e pré-checagem

1. Localize os módulos do RAG. No MedAssist: `src/medasist/ingestion/` (loader, chunker, metadata, pipeline), `src/medasist/vectorstore/store.py` (ChromaDB client + embeddings), `src/medasist/retrieval/retriever.py` (multi-store retrieval + cold start), `src/medasist/generation/chain.py` (LCEL chain + LLM), `src/medasist/config.py` (todos os parâmetros). Em outro projeto, procure por
   `chunk`, `embed`, `Chroma|FAISS|Pinecone|Qdrant|Weaviate`, `retriev`, `rerank`.
2. Pré-checagem para a Fase 2 (empírica):
   - LLM disponível? (MedAssist: GET `http://localhost:1234/v1/models` deve listar `phi-3-mini`.)
   - Vector DB populado? (MedAssist: `chroma_db/` existe e tem chunks.)
   - Golden set existe? Procure `evals/rag_golden.json` (ou similar). Se não, a Fase 2 gera
     um conjunto sintético — rotule os resultados como **baseline sintético**, não verdade absoluta.
3. Anote no relatório quais pré-condições faltaram (afeta a confiança de cada número).

### Fase 1 — Análise estática (arquitetura)

Leia o código e preencha, **para cada um dos 12 estágios**, a tabela:
`estágio | implementação atual | técnica | parâmetros-chave | risco/gap`.

Use `references/pipeline-stages.md` — ele descreve, por estágio, como é "bom" vs "frágil",
o que procurar no código, e as armadilhas comuns. Para chunking, aprofunde com
`references/chunking-techniques.md` (as 5 técnicas e quando cada uma ganha).

Não invente: se um estágio **não existe** no sistema (ex.: sem re-ranking, sem hybrid
search, sem observabilidade), isso é um achado de primeira ordem — registre como gap, não
como "N/A" silencioso. A ausência de um estágio costuma ser a maior alavanca.

### Fase 2 — Avaliação empírica (métricas)

Meça as duas camadas. Use `references/eval-metrics.md` para a definição exata, como computar
e os thresholds de cada métrica:

- **Retrieval:** Context Precision, Context Recall, MRR, Hit Rate.
- **Generation:** Faithfulness, Answer Relevancy, Answer Correctness, Context Utilization.

Rode `scripts/rag_eval.py` — ele gera (ou carrega) o conjunto de perguntas, executa o
pipeline real do projeto, e computa as métricas que o ambiente permite (juiz LLM para
Faithfulness/Precision; embeddings do projeto para Answer Relevancy; ground-truth do golden
set para Recall/Correctness). Métricas que exigem golden set e não têm ground-truth são
reportadas como "requer golden set", não inventadas.

```bash
python .opencode/skills/rag-pipeline-audit/scripts/rag_eval.py \
  --out <workspace>/rag_eval_results.json \
  [--golden evals/rag_golden.json] [--n 15] [--k 5]
```

Leia o JSON de saída e traga os números para o relatório. Destaque os **piores casos
concretos** (a pergunta com pior Faithfulness, o chunk irrelevante recuperado) — eles são
a evidência que sustenta cada recomendação.

### Fase 3 — Relatório com recomendações

Grave em `docs/rag-audit-YYYY-MM-DD.md` (data de hoje). Use **exatamente** esta estrutura:

```markdown
# Auditoria de RAG — <sistema> — <YYYY-MM-DD>

## Sumário executivo
<3-6 linhas: estado geral, os 2-3 maiores riscos, o ganho esperado das top recomendações.>

## Pré-condições da auditoria
<O que rodou: LLM no ar? corpus indexado? golden set real ou sintético? — define a confiança.>

## Mapa do pipeline (12 estágios)
| Estágio | Implementação atual | Técnica | Parâmetros | Risco/Gap |
|---|---|---|---|---|
<uma linha por estágio; estágio ausente = linha com "AUSENTE" no gap.>

## Métricas
### Retrieval
| Métrica | Valor | Threshold | Veredito |
|---|---|---|---|
<Context Precision, Context Recall, MRR, Hit Rate. "n/d (requer golden set)" quando aplicável.>
### Generation
| Métrica | Valor | Threshold | Veredito |
|---|---|---|---|
<Faithfulness, Answer Relevancy, Answer Correctness, Context Utilization.>
### Piores casos (evidência)
<2-4 exemplos concretos: pergunta, o que saiu errado, métrica afetada.>

## Recomendações priorizadas
<Tabela ordenada por prioridade. Veja o rubrica de pontuação abaixo.>
| # | Recomendação | Estágio | Evidência | Desemp. | Escalab. | Confiab. | Custo | Esforço | Prioridade |
|---|---|---|---|---|---|---|---|---|---|

## Roadmap sugerido
<Quick wins (baixo esforço, alto impacto) → médio prazo → estrutural. 3 ondas.>
```

#### Rubrica de pontuação das recomendações

Cada recomendação é avaliada nos 4 critérios + esforço, para que a priorização seja
transparente e não um achismo. Pontue cada critério como **impacto esperado** de adotar a
mudança: `+ +` (forte ganho), `+` (ganho), `0` (neutro), `−` (piora/risco). Custo é o
**impacto no custo de operação** (`+ +` = reduz bastante o custo; `−` = encarece). Esforço
de implementação: `S` / `M` / `L`.

**Prioridade** = alavanca de impacto ÷ esforço. Uma mudança `S` que dá `+ +` em
confiabilidade e desempenho é **P0**. Uma `L` que dá `+` marginal é **P2/P3**. Sempre
prefira ancorar a prioridade na evidência da Fase 2 — recomendar re-ranking porque o MRR
está em 0.4 é forte; recomendar porque "é boa prática" é fraco.

Exemplo de linha:
`1 | Adicionar re-ranking cross-encoder no top-20 | Re-ranking | MRR 0.41, 1º relevante só na pos. 3 | + + | + | + + | − | M | P0`

## Princípios

- **Evidência > opinião.** Toda recomendação aponta para um número da Fase 2 ou um trecho
  de código da Fase 1. Sem evidência, rebaixe a recomendação ou marque-a como hipótese.
- **Estágio ausente é achado.** A maior alavanca quase sempre é um estágio que não existe
  (re-ranking, hybrid search, observabilidade), não um parâmetro mal ajustado.
- **Custo e latência contam.** Toda melhoria de qualidade tem preço (mais tokens, mais
  chamadas, mais infra). O relatório torna esse trade-off explícito nos 4 critérios.
- **Não quebre o que funciona.** Recomende mudanças incrementais e mensuráveis; para cada
  uma, diga **como medir** que melhorou (qual métrica deve subir).

## Arquivos do skill

- `references/pipeline-stages.md` — os 12 estágios em detalhe (bom/frágil, o que procurar).
- `references/eval-metrics.md` — as 8 métricas: definição, cálculo, thresholds.
- `references/chunking-techniques.md` — as 5 técnicas de chunking e quando cada uma ganha.
- `scripts/rag_eval.py` — harness de avaliação (Q&A sintético/golden + métricas via LLM local).
