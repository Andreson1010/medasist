# Métricas de avaliação de RAG

Duas camadas: **Retrieval** (o pipeline trouxe o contexto certo?) e **Generation** (o LLM usou
bem o contexto?). Para cada métrica: o que mede, como computar, threshold de referência. Os
thresholds são pontos de partida — ajuste ao domínio.

Princípio: uma resposta ruim é falha de retrieval OU de generation. Medir as duas camadas
separadamente diz **onde** consertar. Faithfulness baixa com Context Precision alta = problema
de geração (modelo ignora o contexto). Context Recall baixa = problema de retrieval (não achou).

---

## Camada de Retrieval

### Context Precision
Dos chunks recuperados, quantos são **realmente relevantes** à pergunta. Penaliza recuperar lixo.
- **Como:** para cada chunk recuperado, um juiz LLM decide relevante (1) / irrelevante (0).
  Precision = relevantes / total recuperado. Variante ponderada por posição premia relevantes no topo.
- **Threshold:** ≥ 0.7 bom; < 0.5 o contexto está poluído (re-ranking ou chunking melhores ajudam).

### Context Recall
Da informação **necessária** para responder, quanto o retrieval trouxe. Penaliza o que faltou.
- **Como:** requer ground-truth (resposta/contexto de referência do golden set). Quebra a
  referência em fatos e verifica quantos estão cobertos pelos chunks recuperados.
- **Threshold:** ≥ 0.8 bom; baixo = aumente top-k, melhore embeddings, ou adicione hybrid search.
- **Nota:** sem golden set, **não dá** para computar honestamente — reporte "requer golden set".

### MRR (Mean Reciprocal Rank)
Quão **alto** aparece o primeiro chunk relevante. 1/posição do 1º relevante, média sobre as queries.
- **Como:** para cada query, ache a posição do 1º chunk relevante (via juiz ou golden). MRR = média(1/pos).
- **Threshold:** ≥ 0.7 bom; baixo = a ordem do retriever é ruim → **re-ranking** é a alavanca direta.

### Hit Rate (Recall@k)
Com que frequência o retrieval traz **pelo menos um** chunk útil no top-k.
- **Como:** fração de queries com ≥1 chunk relevante no top-k.
- **Threshold:** ≥ 0.9 bom; baixo = falha grave de retrieval (embeddings/chunking/índice).

---

## Camada de Generation

### Faithfulness (groundedness)
A resposta é **sustentada** pelo contexto recuperado, ou o modelo inventou? Mede alucinação.
- **Como:** quebre a resposta em afirmações; juiz LLM verifica cada uma contra o contexto.
  Faithfulness = afirmações suportadas / total.
- **Threshold:** ≥ 0.9 (alucinação é o pecado capital do RAG); < 0.8 é grave.

### Answer Relevancy
A resposta de fato endereça a **pergunta** (vs divagar / responder outra coisa).
- **Como:** similaridade (embeddings do projeto) entre a pergunta e a resposta; ou juiz LLM
  gera perguntas a partir da resposta e mede similaridade com a original.
- **Threshold:** ≥ 0.7 bom.

### Answer Correctness
A resposta está **factualmente certa** vs a referência. Combina similaridade semântica +
sobreposição factual.
- **Como:** requer ground-truth. Compara resposta vs referência (fatos certos/errados/faltando).
- **Threshold:** ≥ 0.7 bom. Sem golden set → "requer golden set".

### Context Utilization
Quanto do contexto **relevante** recuperado a resposta de fato usou (vs ignorar bons chunks).
- **Como:** dos chunks relevantes recuperados, quantos contribuíram para a resposta (juiz LLM).
  Baixa utilização + alta precisão = o modelo desperdiça bom contexto (ordem/prompt/janela).
- **Threshold:** ≥ 0.7 bom.

---

## Como o `rag_eval.py` computa (sem dep externa)

- **Juiz LLM:** usa o cliente OpenAI-compat do projeto (LMStudio) com prompts de
  relevância/grounding que pedem JSON `{"score": 0|1, "motivo": "..."}`.
- **Similaridade:** usa os embeddings do projeto (`_get_rag_embedding_model`) → cosine.
- **Ground-truth:** lê do golden set (`evals/rag_golden.json`): lista de
  `{"pergunta", "resposta_ref", "contexto_ref"?}`. Sem golden, gera perguntas sintéticas de
  chunks amostrados (o chunk-fonte vira o "relevante esperado" → permite Hit Rate/MRR/Precision
  aproximados; Recall/Correctness ficam "requer golden set").
- **Saída:** JSON com agregados por métrica + os piores casos (menor Faithfulness/Precision)
  para servirem de evidência no relatório.

## Distinção que importa no diagnóstico

| Sintoma | Retrieval | Generation | Conserto provável |
|---|---|---|---|
| Não achou a info | Recall/Hit baixos | — | top-k, embeddings, hybrid search |
| Trouxe lixo junto | Precision baixa | — | chunking, re-ranking, metadata filter |
| Achou mas no fundo | MRR baixo | — | re-ranking |
| Alucina com bom contexto | Precision ok | Faithfulness baixa | prompt, modelo, instrução de grounding |
| Ignora bom contexto | Precision ok | Context Util. baixa | ordem dos chunks, "lost in middle", janela |
| Responde outra coisa | — | Answer Relevancy baixa | query transformation, prompt |
