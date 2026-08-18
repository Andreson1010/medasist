# Técnicas de chunking

Chunking é a maior alavanca de retrieval: define a unidade que será embeddada, indexada e
recuperada. Chunk ruim = embedding ruim = retrieval ruim, independente do resto do pipeline.
Não existe "melhor" universal — casa a técnica ao conteúdo e meça (Context Precision/Recall).

## 1. Fixed / Sliding Window
Divide por tamanho fixo (N tokens/caracteres), opcionalmente com overlap (janela deslizante).
- **Prós:** simples, rápido, previsível, barato; overlap reduz corte de contexto na fronteira.
- **Contras:** ignora fronteiras semânticas — corta no meio de frase/tabela/parágrafo; dilui
  embeddings se o tamanho não casa com a coesão do texto.
- **Quando ganha:** corpus homogêneo e bem-estruturado; quando custo/simplicidade importam;
  baseline para comparar.
- **Sinais de problema:** Context Precision baixa, chunks cortando ideias ao meio.

## 2. Semantic Chunking
Quebra onde a **similaridade entre sentenças cai** (breakpoint), agrupando frases coerentes.
- **Prós:** chunks semanticamente coesos → embeddings mais "puros" → melhor precisão; respeita
  o fluxo do texto.
- **Contras:** mais caro (precisa embeddar para decidir os cortes); sensível ao threshold
  (percentile/stddev) — threshold ruim gera chunks gigantes ou picados.
- **Quando ganha:** texto narrativo/heterogêneo onde tópicos mudam; quando a precisão importa
  mais que o custo de ingestão.
- **MedAssist:** usa `RecursiveCharacterTextSplitter` com configs por `DocType` (chunk_size/overlap variando entre bulas, diretrizes, protocolos, manuais). Auditar: os tamanhos geram
  chunks coerentes? Medir Context Precision e inspecionar tamanhos.

## 3. Hierarchical Chunking
Múltiplos níveis (documento → seção → parágrafo). Recupera o nível fino e expande para o pai
para contexto (parent-document / small-to-big / auto-merging).
- **Prós:** recupera com precisão (chunk pequeno) mas responde com contexto (pai grande);
  ótimo p/ documentos longos e estruturados.
- **Contras:** mais complexo (índice multi-nível, lógica de expansão); mais armazenamento.
- **Quando ganha:** PDFs longos com hierarquia clara (manuais, leis, relatórios, contratos) —
  caso comum em logística/jurídico.
- **Sinal de oportunidade:** docs longos + Context Precision boa mas respostas sem contexto
  suficiente (chunk certo, pequeno demais).

## 4. LLM-Based Chunking
Um LLM decide as fronteiras (ou reescreve/resume cada chunk para ficar autossuficiente).
- **Prós:** fronteiras de altíssima qualidade; pode enriquecer cada chunk (título, resumo,
  contexto implícito) → recuperação muito melhor.
- **Contras:** caro e lento na ingestão (1 chamada LLM por trecho); precisa governança de custo.
- **Quando ganha:** corpus de alto valor, relativamente estável (vale o custo único); quando a
  qualidade de retrieval é crítica e o volume de ingestão é moderado.

## 5. Agentic Chunking
Um agente decide **dinamicamente** a estratégia por documento/seção — escolhe fixo aqui,
semântico ali, hierárquico acolá, conforme o tipo de conteúdo detectado (tabela vs prosa vs código).
- **Prós:** adapta-se a corpus heterogêneo (mistura de tabelas, prosa, listas, código); teto de
  qualidade mais alto.
- **Contras:** o mais complexo e caro; mais difícil de depurar e tornar determinístico/reprodutível.
- **Quando ganha:** corpus muito heterogêneo onde nenhuma estratégia única serve; estágio
  avançado, depois de esgotar ganhos mais baratos.

---

## Como recomendar chunking na auditoria

1. **Meça primeiro.** Context Precision baixa + inspeção de chunks (cortando ideias?) justifica
   trocar de técnica. Sem isso, é chute.
2. **Suba a escada pelo custo.** Fixed → Semantic → Hierarchical → LLM/Agentic. Cada degrau
   melhora qualidade e encarece ingestão. Recomende o degrau mínimo que resolve o sintoma medido.
3. **Case ao conteúdo.** PDFs longos e estruturados → Hierarchical costuma ser o melhor
   custo-benefício. Corpus heterogêneo (tabelas+prosa) → considere Agentic/LLM-based só se os
   ganhos mais baratos já se esgotaram.
4. **Toda troca de chunking exige rebuild** do índice e **re-medição** — diga isso na recomendação
   (custo de migração) e qual métrica deve subir (Context Precision/Recall).
