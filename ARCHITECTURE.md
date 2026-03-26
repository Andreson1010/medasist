# ARCHITECTURE.md — MedAssist

> Documento de arquitetura técnica do MedAssist.
> Destinado a qualquer pessoa com conhecimento básico de Python e IA.
> Cobre teoria, decisões de design, fluxo de dados e explicação do código módulo a módulo.

---

## Sumário

1. [Visão Geral do Projeto](#1-visão-geral-do-projeto)
2. [Glossário](#2-glossário)
3. [Arquitetura Geral](#3-arquitetura-geral)
4. [Fluxo de Dados — Passo a Passo](#4-fluxo-de-dados--passo-a-passo)
5. [Módulo: `src/medasist/ingestion/schemas.py`](#5-módulo-srcmedasistingestionschemaspy)
6. [Módulo: `src/medasist/ingestion/loader.py`](#6-módulo-srcmedasistingestionloaderpy)
7. [Módulo: `src/medasist/ingestion/chunker.py`](#7-módulo-srcmedasistingestionchunkerpy)
8. [Módulo: `src/medasist/ingestion/metadata.py`](#8-módulo-srcmedasistingestionmetadatapy)
9. [Módulo: `src/medasist/ingestion/pipeline.py`](#9-módulo-srcmedasistingestionpipelinepy)
10. [Módulo: `src/medasist/vectorstore/`](#10-módulo-srcmedasistvectorstore)
11. [Módulo: `src/medasist/retrieval/retriever.py`](#11-módulo-srcmedasistretrievalretrieverpy)
12. [Módulo: `src/medasist/profiles/schemas.py`](#12-módulo-srcmedasistprofilesschemspy)
13. [Módulo: `src/medasist/generation/`](#13-módulo-srcmedasistgeneration)
14. [Módulo: `src/medasist/api/`](#14-módulo-srcmedasistapi)
15. [Módulo: `src/medasist/ui/app.py`](#15-módulo-srcmedasistui-apppy)
16. [Módulo: `src/medasist/config.py`](#16-módulo-srcmedasistconfigpy)
17. [Decisões de Arquitetura (ADRs)](#17-decisões-de-arquitetura-adrs)
18. [Infra e Deploy](#18-infra-e-deploy)
19. [Referências e Leituras](#19-referências-e-leituras)

---

## 1. Visão Geral do Projeto

### O que é o MedAssist?

O MedAssist é um **sistema RAG (Retrieval-Augmented Generation) para o domínio médico**: um software que recebe uma pergunta em linguagem natural e retorna uma resposta fundamentada em documentos médicos reais — bulas, diretrizes clínicas, protocolos e manuais. A resposta é exposta via uma **API REST** e consumida por uma interface Streamlit.

### Qual problema ele resolve?

Profissionais de saúde e pacientes frequentemente precisam consultar informações em documentos técnicos extensos (bulas com 40 páginas, diretrizes com centenas de tópicos). Fazer isso manualmente é lento e propenso a erros. O MedAssist permite perguntas diretas como:

> "Quais são as contraindicações da dipirona para pacientes com insuficiência renal?"

e recebe uma resposta citada, rastreável até a seção exata do documento-fonte.

### Por que RAG e não fine-tuning?

O RAG combina duas etapas: **recuperação** (busca os trechos mais relevantes no banco vetorial) e **geração** (usa um LLM para formular uma resposta baseada nesses trechos). A alternativa seria fazer fine-tuning de um LLM com os documentos médicos.

O RAG é preferível aqui por três razões:
- **Rastreabilidade:** cada resposta cita a fonte exata — essencial em contexto médico
- **Atualização:** adicionar novos documentos não exige retreinar o modelo
- **Custo:** inferência local com LM Studio — zero custo de API, zero dependência de conectividade

### Stack

| Camada | Tecnologia |
|--------|-----------|
| LLM + Embeddings | LM Studio (local, compatível OpenAI API) |
| Orquestração | LangChain LCEL |
| Banco vetorial | ChromaDB (persistente em disco) |
| API | FastAPI + Uvicorn |
| Interface | Streamlit |
| Ingestão de PDF | pdfplumber + PyMuPDF (fallback) |
| Configuração | pydantic-settings |
| Qualidade | black, flake8, pytest (≥80% cobertura) |

### Regras de segurança médica (inegociáveis)

O sistema tem quatro invariantes que nunca podem ser violados:

1. **Disclaimer obrigatório:** toda resposta inclui `"Este sistema é um auxiliar informativo e não substitui avaliação médica presencial"`
2. **Cold start obrigatório:** se nenhum chunk superar o threshold de similaridade, a resposta é uma mensagem fixa — o LLM não é chamado
3. **Citação obrigatória:** toda resposta cita ao menos uma fonte no formato `[N] <nome_doc> — Seção: <seção>, Pág. <pág>`
4. **Sem dados reais de pacientes:** nenhum dado pessoal em código, testes ou logs

---

## 2. Glossário

Todos os termos técnicos usados no projeto, explicados no contexto do MedAssist.

---

### ANN (Approximate Nearest Neighbor)

Algoritmo de busca que encontra os vetores mais próximos de uma consulta sem percorrer todos os documentos. O ChromaDB usa HNSW internamente — em vez de calcular a distância para cada um dos milhares de chunks, ele percorre um grafo hierárquico e chega nos vizinhos mais próximos em O(log n). Isso torna a busca viável mesmo com dezenas de milhares de chunks.

---

### Chunk

Trecho de texto extraído de um documento PDF, com tamanho controlado, usado como unidade de indexação no banco vetorial. Cada chunk tem um embedding associado. O tamanho ideal varia por tipo de documento: bulas têm seções mais longas e suportam chunks maiores; protocolos têm passos curtos e precisam de chunks menores para manter a granularidade.

---

### ChromaDB

Banco de dados vetorial open-source, local e sem servidor. No MedAssist, é usado com persistência em disco (`./chroma_db`). Cada `DocType` tem sua própria coleção — isso evita que uma busca em bulas traga resultados de protocolos, mesmo que os embeddings sejam próximos no espaço vetorial.

---

### Citação

Referência estruturada a uma fonte usada na resposta. O MedAssist exige o formato `[N] <nome_doc> — Seção: <seção>, Pág. <pág>`. O módulo `citations.py` valida que todo número `[N]` no texto da resposta tem uma `CitationItem` correspondente — referências geradas pelo LLM mas sem fonte real são removidas.

---

### Cold Start (RAG)

No MedAssist, cold start ocorre quando nenhum chunk retornado pelo retriever supera o `retrieval_score_threshold` (padrão: 0.4). Nesse caso, a chain **curto-circuita antes de chamar o LLM** e retorna a `cold_start_message` configurada. Isso é crítico: sem essa proteção, o LLM responderia com alucinações quando não há informação relevante nos documentos.

```
retrieval vazio ou score < 0.4  →  mensagem fixa  (zero custo, zero alucinação)
retrieval com score ≥ 0.4       →  LLM gera resposta baseada nos chunks
```

---

### Coleção ChromaDB

Unidade de isolamento do ChromaDB equivalente a uma tabela. O MedAssist mantém quatro coleções: `bulas`, `diretrizes`, `protocolos`, `manuais`. Quando o usuário especifica `doc_types` na requisição, a busca ocorre apenas nas coleções correspondentes — sem filtragem pós-ANN via `where`, o que preserva a qualidade do recall.

---

### DocType

Enum que classifica o tipo de documento médico. Cada `DocType` determina: a estratégia de chunking (tamanho e overlap), a coleção ChromaDB de destino, e o prefixo dos campos de configuração em `Settings`. Os valores são `BULA`, `DIRETRIZ`, `PROTOCOLO`, `MANUAL`.

---

### Disclaimer

Texto fixo anexado ao final de toda resposta da API: `"Este sistema é um auxiliar informativo e não substitui avaliação médica presencial."` É uma regra de segurança inegociável — sem ele, o sistema poderia ser interpretado como substituto de consulta médica, o que é ilegal e perigoso.

---

### Embedding

Representação numérica de um texto como vetor de números reais. No MedAssist, os embeddings são gerados pelo modelo configurado em `lm_studio_embedding_model` (padrão: `nomic-embed-text`), via LM Studio. Chunks semanticamente similares ficam próximos no espaço vetorial — isso é o que permite a busca por significado, não por palavras-chave.

---

### FastAPI Lifespan

Mecanismo do FastAPI para executar código no startup e shutdown da aplicação. No MedAssist, o `lifespan` aquece todas as chains LangChain no startup — isso inclui instanciar o retriever, conectar ao ChromaDB e ao LM Studio. Sem o pré-aquecimento, a primeira requisição sofreria latência alta de cold-start de conexão.

---

### Hash SHA-256

Impressão digital do conteúdo de um arquivo. O `loader.py` calcula o SHA-256 de cada PDF antes de processar. O `pipeline.py` armazena esses hashes e, em re-execuções, pula arquivos cujo hash já está registrado — **idempotência**: ingerir o mesmo documento duas vezes não gera duplicatas no ChromaDB.

---

### HNSW (Hierarchical Navigable Small World)

Algoritmo de indexação ANN usado pelo ChromaDB internamente. Constrói um grafo em múltiplos níveis de granularidade — níveis altos conectam pontos distantes (navegação rápida), níveis baixos conectam vizinhos próximos (precisão alta). Oferece boa precisão com latência de busca em milissegundos.

---

### Idempotência

Propriedade de uma operação que pode ser executada múltiplas vezes com o mesmo resultado. No MedAssist, a ingestão é idempotente via SHA-256: se o arquivo já foi processado (hash conhecido), o pipeline o ignora. Isso torna seguro rodar `python scripts/ingest_docs.py` várias vezes sem duplicar dados.

---

### LangChain LCEL

LCEL (LangChain Expression Language) é a sintaxe declarativa do LangChain para compor pipelines. No MedAssist, a chain é expressa como:

```python
chain = retriever | prompt | llm | parser
```

Cada `|` conecta um componente ao próximo, passando o output como input. Isso torna o pipeline legível e facilita a substituição de componentes (ex: trocar o LLM sem mudar o retriever).

---

### LM Studio

Servidor local que hospeda LLMs e modelos de embedding com uma API compatível com OpenAI. O MedAssist usa LM Studio em vez da API OpenAI diretamente — isso garante: zero custo de tokens, funcionamento offline, privacidade total (dados nunca saem da máquina) e flexibilidade para trocar o modelo sem mudar código.

---

### MMR (Maximal Marginal Relevance)

Estratégia de recuperação que balanceia **relevância** (o chunk é pertinente à pergunta?) com **diversidade** (o chunk traz informação nova?). Sem MMR, o retriever pode retornar 10 chunks quase idênticos da mesma seção do documento. Com MMR, os chunks são escolhidos para maximizar a cobertura de perspectivas diferentes.

---

### Overlap de Chunks

Quantidade de caracteres que dois chunks consecutivos compartilham. Um overlap de 100 caracteres significa que o final do chunk N é igual ao início do chunk N+1. Isso evita que informações que aparecem na fronteira entre dois chunks sejam perdidas — frases que começam no fim de um chunk e terminam no início do próximo são capturadas por ambos.

---

### Perfil de Usuário (UserProfile)

Classificação do tipo de usuário que faz a consulta. O MedAssist suporta quatro perfis: `MEDICO`, `ENFERMEIRO`, `ASSISTENTE`, `PACIENTE`. Cada perfil tem uma `temperature` diferente (médico → 0.1, paciente → 0.3) e um `prompt_template` específico — a linguagem da resposta se adapta ao nível técnico esperado.

---

### Pipeline de Ingestão

Sequência de etapas que transforma um PDF bruto em chunks indexados no ChromaDB: carregamento → chunking → anexação de metadados → inserção no banco vetorial. O pipeline é idempotente (via SHA-256) e opera em lote sobre um diretório de documentos.

---

### pdfplumber

Biblioteca Python especializada em extração de texto de PDFs com layout preservado. É o extrator primário do MedAssist — tem melhor desempenho com PDFs nativos (gerados digitalmente). Para PDFs escaneados ou com estrutura complexa, falha graciosamente e o sistema recorre ao PyMuPDF.

---

### PyMuPDF (fitz)

Biblioteca Python baseada na engine MuPDF, mais robusta para PDFs problemáticos. No MedAssist, é o extrator de fallback: usado quando pdfplumber falha em abrir o arquivo ou quando uma página retorna menos de 20 caracteres. Suporta PDFs corrompidos, criptografados com senha em branco e layouts complexos.

---

### RAG (Retrieval-Augmented Generation)

Padrão arquitetural que combina busca vetorial com geração de linguagem natural. Em vez de o LLM responder "do zero" (com risco de alucinação), ele recebe os chunks mais relevantes como contexto e os usa como base para a resposta. O MedAssist implementa RAG com ChromaDB como retriever e LM Studio como gerador.

---

### Rate Limiting

Controle de quantas requisições um cliente pode fazer por unidade de tempo. No MedAssist, implementado via `slowapi` na API FastAPI. Previne abuso, garante disponibilidade para todos os usuários e protege o LM Studio local (que tem recursos limitados) de sobrecarga.

---

### Score Threshold

Valor mínimo de similaridade que um chunk precisa ter para ser considerado relevante. No MedAssist, `retrieval_score_threshold = 0.4` (configurável). Chunks com score abaixo desse valor são descartados. Se nenhum chunk sobrar, o sistema aciona o cold start — nunca gera uma resposta sem evidência documental.

---

### Streamlit

Framework Python para construir interfaces web de forma declarativa. O `ui/app.py` do MedAssist é um cliente Streamlit que chama `POST /query` via httpx — **nunca** acessa o LM Studio diretamente. Essa separação garante que toda a lógica de negócio (disclaimer, cold start, citações) seja controlada pela API.

---

## 3. Arquitetura Geral

### Diagrama de Componentes

```
┌─────────────────────────────────────────────────────────────────┐
│                     Usuário (Browser)                           │
└────────────────────────────┬────────────────────────────────────┘
                             │ HTTP
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                  ui/app.py  (Streamlit :8501)                   │
│                                                                 │
│   Seletor de perfil → Campo de pergunta → Exibição de resposta  │
│                  ↕ httpx.post("/query")                         │
└────────────────────────────┬────────────────────────────────────┘
                             │ HTTP POST /query
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│                 api/main.py  (FastAPI :8000)                    │
│                                                                 │
│   RateLimitMiddleware → Router → QueryRequest                   │
│        ↓ lifespan (aquece chains no startup)                    │
│   POST /query  →  Chain LCEL  →  QueryResponse                  │
│   POST /ingest →  Pipeline    (header X-Admin-Key)              │
└───────────┬─────────────────────────┬───────────────────────────┘
            │                         │
            ▼                         ▼
┌───────────────────────┐   ┌─────────────────────────────────────┐
│  generation/chain.py  │   │      ingestion/pipeline.py          │
│                       │   │                                     │
│  retriever            │   │  loader → chunker → metadata        │
│    │ MMR + threshold  │   │       ↓ idempotente (SHA-256)       │
│    ▼                  │   │  vectorstore.upsert(chunks)         │
│  prompt (por perfil)  │   └──────────────┬──────────────────────┘
│    ▼                  │                  │
│  ChatOpenAI (LMStudio)│                  │
│    ▼                  │                  │
│  citations.py         │                  │
└───────────┬───────────┘                  │
            │                             │
            ▼                             ▼
┌─────────────────────────────────────────────────────────────────┐
│              vectorstore/  (ChromaDB local ./chroma_db)         │
│                                                                 │
│   coleção: bulas       coleção: diretrizes                      │
│   coleção: protocolos  coleção: manuais                         │
│                                                                 │
│   (uma coleção por DocType — sem contaminação entre tipos)      │
└─────────────────────────────────────────────────────────────────┘
                             ▲
                             │ embeddings via
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│              LM Studio  (local :1234)                           │
│                                                                 │
│   /v1/embeddings  →  nomic-embed-text                           │
│   /v1/chat/completions  →  phi-3-mini (ou modelo configurado)   │
└─────────────────────────────────────────────────────────────────┘
```

### Separação entre ingestão e inferência

O MedAssist tem dois momentos distintos:

**Ingestão (offline):** `python scripts/ingest_docs.py --dir data/raw/` — processa PDFs, gera chunks com metadados, calcula embeddings via LM Studio e persiste no ChromaDB. Acontece uma vez por lote de documentos (ou quando há documentos novos). Documentos já processados são ignorados pelo hash SHA-256.

**Inferência (online):** `uvicorn api.main:app` — carrega as chains no startup e responde perguntas em tempo real. Não relê PDFs durante as requisições — tudo já está indexado no ChromaDB.

---

## 4. Fluxo de Dados — Passo a Passo

### Fluxo 1: Ingestão de Documentos

```
data/raw/bulas/dipirona.pdf
        ↓ ingestion/loader.py
1. _validate_path(path)             → verifica existência e extensão .pdf
2. _compute_sha256(path)            → "a3f8b2..." (digest de 64 hex chars)
3. _extract_pages(path)
   ├─ tenta pdfplumber              → lista[PageContent(page_number, text)]
   └─ fallback PyMuPDF por página   → se text < 20 chars na página

        ↓ ingestion/chunker.py
4. chunk_document(doc, DocType.BULA)
   → RecursiveCharacterTextSplitter(
       chunk_size=600, chunk_overlap=100   ← de settings.chunk_size_bula
     )
   → lista[str] (os chunks de texto)

        ↓ ingestion/metadata.py
5. attach_metadata(chunks, doc)
   → lista[ChunkMetadata(
       doc_name, doc_type, sha256,
       page_number, chunk_index,
       section                            ← inferida por heurística
     )]

        ↓ ingestion/pipeline.py
6. sha256 já em hashes.json?
   ├─ sim  → ignora documento (idempotência)
   └─ não  → vectorstore.upsert(chunks_with_metadata)
              hashes.json ← adiciona sha256

        ↓ vectorstore/
7. ChromaDB coleção "bulas"
   → embeddings gerados via LM Studio /v1/embeddings
   → HNSW index atualizado
```

### Fluxo 2: Consulta (online)

```
POST /query
{"question": "Quais são as contraindicações da dipirona?",
 "profile": "MEDICO",
 "doc_types": ["bula"]}
        ↓ Pydantic valida QueryRequest
        ↓ RateLimitMiddleware verifica limite
        ↓ Router injeta chain (singleton do lifespan)
        ↓ chain.invoke({"question": ..., "profile": ...})

1. retrieval/retriever.py
   → VectorStoreRetriever(
       search_type="mmr",
       search_kwargs={"k": 10, "score_threshold": 0.4}
     )
   → ChromaDB coleção "bulas"
   → embedding da pergunta via LM Studio
   → ANN (HNSW) → top-N candidatos
   → MMR filtra para k=10 chunks diversos
   → score_threshold descarta chunks < 0.4

2. chunks vazios ou todos abaixo do threshold?
   └─ sim → cold_start_message + disclaimer  (LLM não é chamado)

3. generation/prompts.py
   → PromptRegistry.get(UserProfile.MEDICO)
   → template técnico com instruções de citação

4. generation/chain.py
   → ChatOpenAI(base_url="http://localhost:1234/v1", model="phi-3-mini")
   → LLM recebe: question + chunks formatados + template de perfil
   → LLM gera resposta com marcadores [1], [2], ...

5. generation/citations.py
   → extrai citações do texto: [1], [2], ...
   → valida que cada [N] tem CitationItem correspondente
   → remove referências órfãs (sem fonte real)

        ↓ QueryResponse(...)
        ↓ Pydantic serializa para JSON

HTTP 200
{"answer": "...[1]...[2]...",
 "citations": [
   {"index": 1, "doc_name": "dipirona.pdf", "section": "Contraindicações", "page": 12},
   {"index": 2, "doc_name": "dipirona.pdf", "section": "Reações adversas",  "page": 14}
 ],
 "profile": "MEDICO",
 "disclaimer": "Este sistema é um auxiliar informativo..."}
```

### Fluxo 3: Cold Start

```
POST /query
{"question": "Qual é o resultado do jogo de ontem?", "profile": "PACIENTE"}

1. retrieval/retriever.py
   → embedding da pergunta
   → ANN no ChromaDB
   → nenhum chunk com score ≥ 0.4

2. chain detecta lista de chunks vazia
   → curto-circuito: LLM não é invocado

HTTP 200
{"answer": "Não encontrei essa informação nos documentos disponíveis.
            Por favor, consulte um profissional de saúde.",
 "citations": [],
 "profile": "PACIENTE",
 "disclaimer": "Este sistema é um auxiliar informativo..."}
```

---

## 5. Módulo: `src/medasist/ingestion/schemas.py`

### Responsabilidade

Define os tipos de dados imutáveis que fluem pelo pipeline de ingestão: o enum `DocType` e os dataclasses `PageContent` e `LoadedDocument`.

### `DocType`

```python
class DocType(str, Enum):
    BULA      = "bula"
    DIRETRIZ  = "diretriz"
    PROTOCOLO = "protocolo"
    MANUAL    = "manual"
```

Herda de `str` — isso permite usar `DocType.BULA` diretamente como chave em dicionários e como sufixo de configuração: `settings.chunk_size_bula`, `settings.collection_bulas`. Qualquer novo tipo de documento exige apenas adicionar um valor aqui e os campos correspondentes em `Settings`.

### Por que `frozen=True` nos dataclasses?

```python
@dataclass(frozen=True)
class PageContent:
    page_number: int
    text: str
```

`frozen=True` torna o objeto imutável após criação — equivalente a uma `namedtuple` com tipagem explícita. Isso evita bugs onde código downstream modifica acidentalmente os dados de uma página já processada. Seguindo a regra de imutabilidade do projeto: **crie novo objeto, nunca mute o existente**.

### `LoadedDocument.full_text`

```python
@property
def full_text(self) -> str:
    return "\n".join(p.text for p in self.pages if p.text.strip())
```

Propriedade computada que concatena apenas páginas com texto (ignora páginas em branco). Usada pelo chunker como entrada. Não é armazenada como campo — evita duplicação de dados no objeto.

### Por que SHA-256 no `LoadedDocument`?

O hash é calculado no `loader.py` e propagado como campo do `LoadedDocument`. O `pipeline.py` usa esse hash para idempotência — verifica antes de enviar para o ChromaDB se aquele documento já foi processado. Propagar o hash pelo dataclass (em vez de recalcular) evita I/O redundante.

---

## 6. Módulo: `src/medasist/ingestion/loader.py`

### Responsabilidade

Receber o caminho de um PDF e retornar um `LoadedDocument` com o texto extraído de cada página, o `DocType` e o SHA-256 do arquivo.

### Estratégia de extração dual

O loader usa dois extratores com fallback explícito:

```
pdfplumber  →  primário   (PDFs nativos, layout estruturado)
PyMuPDF     →  fallback   (PDFs escaneados, corrompidos, layout complexo)
```

A lógica de fallback opera em dois níveis:

1. **Nível de arquivo:** se `pdfplumber` lançar exceção ao abrir o PDF, cai para PyMuPDF imediatamente para todas as páginas
2. **Nível de página:** se `pdfplumber` abrir o arquivo mas uma página retornar menos de `_MIN_PAGE_CHARS = 20` caracteres, PyMuPDF tenta apenas essa página

```python
for page in pages:
    if len(page.text.strip()) >= _MIN_PAGE_CHARS:
        result.append(page)          # pdfplumber OK
    else:
        fallback = _extract_page_with_pymupdf(path, page.page_number)
        result.append(fallback if fallback else page)  # PyMuPDF ou página vazia
```

### Por que não tentar OCR?

OCR (Tesseract, etc.) foi considerado mas não incluído como terceiro nível. PDFs médicos raramente são imagens puras — o mais comum é PDF com texto em layer invisível (gerado por scanners modernos). PyMuPDF extrai esse layer diretamente. OCR seria mais lento, exigiria dependência adicional e cobriria um caso raro.

### SHA-256 em blocos de 64 KB

```python
def _compute_sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            hasher.update(chunk)
    return hasher.hexdigest()
```

Leitura em blocos (`65536` bytes = 64 KB) evita carregar o PDF inteiro na memória para calcular o hash — importante para bulas e manuais que podem ter 50+ MB. O idioma `iter(callable, sentinel)` itera até `fh.read()` retornar `b""` (EOF).

### `_validate_path()` — fail fast

```python
def _validate_path(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(f"Arquivo não encontrado: {path}")
    if path.suffix.lower() != ".pdf":
        raise ValueError(f"Arquivo não é um PDF: {path}")
```

Validação na fronteira do sistema (entrada de dados externos). Falha imediata com mensagem clara — melhor do que deixar `pdfplumber` lançar uma exceção genérica lá dentro.

---

## 7. Módulo: `src/medasist/ingestion/chunker.py`

### Responsabilidade

Dividir o texto de um `LoadedDocument` em chunks de tamanho controlado, usando estratégia diferente por `DocType`.

### Por que tamanhos diferentes por tipo?

Cada tipo de documento tem uma unidade de informação natural:

| DocType | `chunk_size` | `chunk_overlap` | Justificativa |
|---------|:---:|:---:|---------------|
| BULA | 600 | 100 | Seções longas (contraindicações, posologia) — chunk maior preserva contexto |
| DIRETRIZ | 800 | 150 | Argumentação clínica extensa — chunk maior para manter raciocínio completo |
| PROTOCOLO | 400 | 50 | Passos numerados curtos — chunk menor para granularidade precisa |
| MANUAL | 500 | 100 | Equilíbrio entre procedimentos e explicações |

### `RecursiveCharacterTextSplitter`

O LangChain oferece vários splitters. O `RecursiveCharacterTextSplitter` é o preferido porque tenta manter quebras em fronteiras naturais do texto:

```
Tentativas de split (ordem de prioridade):
1. \n\n  (parágrafo)
2. \n    (linha)
3. " "   (palavra)
4. ""    (caractere)
```

Só passa para o próximo separador se o chunk ainda não couber no `chunk_size`. Isso evita cortar no meio de uma palavra ou de um número de dosagem.

### Interface esperada

```python
def chunk_document(doc: LoadedDocument) -> list[str]:
    """
    Divide o texto completo do documento em chunks.

    Parameters
    ----------
    doc : LoadedDocument
        Documento carregado pelo loader.

    Returns
    -------
    list[str]
        Lista de chunks de texto prontos para anexação de metadados.
    """
    settings = get_settings()
    size    = getattr(settings, f"chunk_size_{doc.doc_type.value}")
    overlap = getattr(settings, f"chunk_overlap_{doc.doc_type.value}")

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=size,
        chunk_overlap=overlap,
    )
    return splitter.split_text(doc.full_text)
```

---

## 8. Módulo: `src/medasist/ingestion/metadata.py`

### Responsabilidade

Enriquecer cada chunk de texto com metadados estruturados necessários para as citações da resposta: nome do documento, tipo, seção, página e índice.

### Por que metadados são críticos?

O ChromaDB armazena junto com cada embedding um dicionário de metadados. Quando o retriever recupera chunks, esses metadados são o que permite ao `citations.py` gerar `[N] dipirona.pdf — Seção: Contraindicações, Pág. 12`. Sem metadados, a resposta seria rastreável apenas ao texto do chunk — inaceitável em contexto médico.

### Estrutura de `ChunkMetadata`

```python
@dataclass(frozen=True)
class ChunkMetadata:
    doc_name:    str      # "dipirona.pdf"
    doc_type:    str      # "bula"
    sha256:      str      # hash do arquivo de origem
    chunk_index: int      # posição do chunk no documento (0-based)
    page_number: int      # página aproximada (baseada em proporção)
    section:     str      # "Contraindicações" (inferida por heurística)
```

### Inferência de seção

A seção é inferida por heurística: o chunk é pesquisado em sentido reverso por um padrão de cabeçalho (linha em maiúsculas, linha seguida de `\n---`, texto que precede `:` no início de linha). Para documentos sem cabeçalhos estruturados, o fallback é `"Conteúdo"`.

### Mapeamento chunk → página

Como o chunking opera sobre o `full_text` (texto concatenado de todas as páginas), a página de cada chunk é aproximada pela proporção de caracteres:

```python
char_offset  = full_text.index(chunk_text)
page_number  = round(char_offset / len(full_text) * total_pages) + 1
```

Não é exato, mas é suficientemente preciso para as citações.

---

## 9. Módulo: `src/medasist/ingestion/pipeline.py`

### Responsabilidade

Orquestrar o pipeline completo de ingestão de forma idempotente: ler um diretório de PDFs, processar cada um (loader → chunker → metadata), inserir no ChromaDB e registrar os hashes processados.

### Idempotência via `hashes.json`

```
data/processed/hashes.json
{
  "a3f8b2...": "bulas/dipirona.pdf",
  "f19c44...": "diretrizes/sepse_2024.pdf"
}
```

Antes de processar cada PDF, o pipeline verifica se o SHA-256 já consta no arquivo. Se sim, o arquivo é ignorado com log de info. Isso torna seguro re-executar o script sobre um diretório com documentos novos e antigos misturados — apenas os novos serão processados.

### Roteamento por diretório

A estrutura de `data/raw/` reflete os `DocType`:

```
data/raw/
├── bulas/
├── diretrizes/
├── protocolos/
└── manuais/
```

O pipeline infere o `DocType` pelo nome do diretório pai do arquivo, sem precisar que o usuário especifique manualmente.

### Tratamento de erros por arquivo

Se um arquivo falhar (PDF corrompido, sem permissão de leitura), o pipeline registra o erro, continua para o próximo arquivo e ao final reporta o total de sucessos e falhas. Não aborta o lote por causa de um único arquivo ruim.

---

## 10. Módulo: `src/medasist/vectorstore/`

### Responsabilidade

Encapsular o acesso ao ChromaDB: criar/carregar coleções, inserir chunks com embeddings, expor coleções para o retriever.

### Uma coleção por DocType

```python
COLLECTION_MAP = {
    DocType.BULA:      settings.collection_bulas,      # "bulas"
    DocType.DIRETRIZ:  settings.collection_diretrizes,  # "diretrizes"
    DocType.PROTOCOLO: settings.collection_protocolos,  # "protocolos"
    DocType.MANUAL:    settings.collection_manuais,     # "manuais"
}
```

A separação por coleção elimina contaminação pós-ANN. Quando o usuário pergunta sobre uma bula e especifica `doc_types=["bula"]`, a busca acontece diretamente na coleção `bulas` — o ChromaDB só considera esses chunks. Alternativa seria usar uma única coleção com filtro `where={"doc_type": "bula"}`, mas isso aumenta a latência porque o ANN é calculado sobre todos os documentos e só então filtrado.

### Embedding function

```python
from langchain_openai import OpenAIEmbeddings

embedding_fn = OpenAIEmbeddings(
    base_url=settings.lm_studio_base_url,
    api_key=settings.lm_studio_api_key.get_secret_value(),
    model=settings.lm_studio_embedding_model,
)
```

O `OpenAIEmbeddings` do LangChain é compatível com LM Studio porque o LM Studio expõe a mesma interface REST que a OpenAI (`POST /v1/embeddings`).

### `upsert` vs `add`

O pipeline usa `upsert` (insert or update) em vez de `add`. Se um documento for re-ingerido (ex: bula com nova versão, mesmo nome de arquivo mas hash diferente), os chunks antigos são substituídos pelos novos. O ID de cada chunk é derivado do SHA-256 + índice, garantindo determinismo.

---

## 11. Módulo: `src/medasist/retrieval/retriever.py`

### Responsabilidade

Configurar e expor o `VectorStoreRetriever` do LangChain com MMR e score threshold aplicados sobre as coleções ChromaDB.

### MMR — por que não busca simples por cosseno?

Busca simples retorna os K chunks com maior similaridade à pergunta. Para uma pergunta sobre contraindicações de dipirona, isso pode retornar 10 chunks da mesma seção, quase idênticos. O MMR resolve isso:

```
Para cada posição de 1 a K:
    Escolhe o chunk que maximiza:
    λ * sim(chunk, pergunta) - (1-λ) * max_sim(chunk, já_selecionados)
```

O segundo termo penaliza chunks similares aos já escolhidos. `λ = 0.5` (padrão LangChain) equilibra relevância e diversidade.

### Score threshold como guardião do cold start

```python
retriever = vectorstore.as_retriever(
    search_type="mmr",
    search_kwargs={
        "k": settings.retrieval_top_k,
        "score_threshold": settings.retrieval_score_threshold,
    },
)
```

O `score_threshold=0.4` faz com que o retriever retorne lista vazia se nenhum chunk tiver similaridade ≥ 0.4 com a pergunta. A chain verifica se a lista está vazia e, se sim, retorna a mensagem de cold start. Esse é o mecanismo central de segurança contra alucinações.

### Retriever por `doc_types`

Quando o `QueryRequest` especifica `doc_types`, o retriever é configurado para consultar apenas as coleções correspondentes. Quando `doc_types` está ausente, consulta todas as quatro coleções e une os resultados antes do MMR.

---

## 12. Módulo: `src/medasist/profiles/schemas.py`

### Responsabilidade

Definir os perfis de usuário e suas configurações de LLM: temperatura, tamanho máximo de resposta e identificador do template de prompt.

### `UserProfile`

```python
class UserProfile(str, Enum):
    MEDICO      = "medico"
    ENFERMEIRO  = "enfermeiro"
    ASSISTENTE  = "assistente"
    PACIENTE    = "paciente"
```

### `ProfileConfig`

```python
@dataclass(frozen=True)
class ProfileConfig:
    temperature:     float  # controla criatividade do LLM
    max_tokens:      int    # limite de tokens na resposta
    prompt_template: str    # chave no PromptRegistry
```

### Por que temperaturas diferentes?

| Perfil | Temperature | Justificativa |
|--------|:-----------:|---------------|
| `MEDICO` | 0.1 | Precisão máxima — médico precisa de informação exata, sem variação |
| `ENFERMEIRO` | 0.15 | Levemente mais narrativo — procedimentos e contexto clínico |
| `ASSISTENTE` | 0.2 | Linguagem intermediária — administrativo + clínico |
| `PACIENTE` | 0.3 | Linguagem acessível — permite alguma paráfrase para clareza |

Temperature = 0 seria determinístico. Temperature = 1 seria muito criativo (risco de imprecisão médica). A faixa 0.1–0.3 mantém fidelidade ao contexto enquanto permite adaptar a linguagem.

---

## 13. Módulo: `src/medasist/generation/`

### Estrutura

```
generation/
├── chain.py      # Monta e expõe a chain LCEL completa
├── prompts.py    # PromptRegistry com template por UserProfile
└── citations.py  # Valida e filtra citações [N] da resposta
```

### `generation/chain.py`

Monta a chain LCEL:

```python
chain = retriever | format_docs | prompt | llm | StrOutputParser()
```

O `format_docs` é um `RunnableLambda` que formata os chunks recuperados como contexto numerado:

```
[1] dipirona.pdf — Seção: Contraindicações, Pág. 12
Dipirona é contraindicada em pacientes com...

[2] dipirona.pdf — Seção: Interações, Pág. 15
A administração concomitante com...
```

O LLM recebe esse contexto numerado e é instruído a referenciar apenas os números `[N]` fornecidos.

**Aquecimento no lifespan:**

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    chains = {profile: build_chain(profile) for profile in UserProfile}
    app.state.chains = chains
    yield
    # shutdown: sem cleanup necessário (ChromaDB fecha automaticamente)
```

Todas as quatro chains (uma por perfil) são instanciadas no startup. Isso valida a conexão com LM Studio e com o ChromaDB antes de aceitar requisições.

### `generation/prompts.py` — `PromptRegistry`

```python
class PromptRegistry:
    _templates: dict[UserProfile, ChatPromptTemplate] = {}

    @classmethod
    def get(cls, profile: UserProfile) -> ChatPromptTemplate:
        return cls._templates[profile]
```

Cada template instrui o LLM a:
1. Responder **somente** com base no contexto fornecido
2. Citar cada afirmação com o número `[N]` correspondente
3. Adaptar o vocabulário ao perfil (técnico para MEDICO, acessível para PACIENTE)
4. Jamais inventar informações não presentes no contexto

**Exemplo de instrução de perfil (PACIENTE):**

```
Você é um assistente de saúde. Responda de forma clara e sem termos técnicos.
Use apenas as informações dos documentos abaixo. Cite as fontes com [N].
Se a informação não estiver nos documentos, diga que não encontrou.
```

### `generation/citations.py` — validação de citações

O LLM pode, ocasionalmente, gerar uma referência `[5]` quando o contexto só tem `[1]` e `[2]`. O `citations.py` previne isso:

```python
def validate_citations(answer: str, available: list[CitationItem]) -> tuple[str, list[CitationItem]]:
    """Remove do texto citações sem CitationItem correspondente."""
    available_indices = {c.index for c in available}
    found = set(re.findall(r'\[(\d+)\]', answer))

    orphans = found - available_indices
    for idx in orphans:
        answer = answer.replace(f"[{idx}]", "")  # remove referência órfã

    used = [c for c in available if c.index in found - orphans]
    return answer.strip(), used
```

Garante que a lista de `citations` na resposta contenha exatamente os documentos referenciados no texto — nem mais, nem menos.

---

## 14. Módulo: `src/medasist/api/`

### Estrutura e responsabilidades

```
api/
├── main.py              # App FastAPI, lifespan, rate limiting, /health
└── routers/
    ├── query.py         # POST /query
    └── ingest.py        # POST /ingest  (requer X-Admin-Key)
```

### `api/main.py`

**Rate limiting via `slowapi`:**

```python
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)

@app.get("/query")
@limiter.limit("30/minute")
async def query(...): ...
```

Protege o endpoint `/query` de abuso. 30 requisições/minuto por IP é suficiente para uso legítimo e bloqueia scraping automatizado.

**`/health`:** endpoint de diagnóstico que retorna status de conexão com LM Studio e ChromaDB, versão da aplicação e uptime. Essencial para monitoramento em produção e para verificar se o LM Studio está rodando antes de aceitar tráfego.

### `api/routers/query.py`

```python
class QueryRequest(BaseModel):
    question:  str            = Field(..., min_length=3, max_length=500)
    profile:   UserProfile    = Field(default=UserProfile.PACIENTE)
    doc_types: list[DocType] | None = Field(default=None)

class CitationItem(BaseModel):
    index:    int
    doc_name: str
    section:  str
    page:     int

class QueryResponse(BaseModel):
    answer:     str
    citations:  list[CitationItem]
    profile:    UserProfile
    disclaimer: str
```

O `disclaimer` sempre está presente no response — é adicionado pela API, não pelo LLM. Isso garante que mesmo que o LLM não inclua o aviso, ele aparece na resposta final.

### `api/routers/ingest.py`

```python
@router.post("/ingest")
async def ingest(
    request: Request,
    x_admin_key: str = Header(...),
):
    if x_admin_key != settings.admin_api_key.get_secret_value():
        raise HTTPException(status_code=403, detail="Chave inválida")
    # dispara pipeline assíncrono
```

O endpoint `/ingest` requer o header `X-Admin-Key` com o valor de `settings.admin_api_key`. Sem ele, retorna 403. Isso evita que qualquer usuário da interface possa adicionar documentos ao sistema — apenas administradores com a chave.

### Regra de dependência

```
Router → chain (geração) → retriever → vectorstore
Router → pipeline (ingestão) → loader → chunker → vectorstore
```

Os routers **nunca** importam do LangChain ou ChromaDB diretamente. Toda a lógica de negócio fica em `generation/` e `ingestion/` — a API é apenas o ponto de entrada HTTP.

---

## 15. Módulo: `src/medasist/ui/app.py`

### Responsabilidade

Interface web Streamlit que permite ao usuário fazer perguntas ao MedAssist e visualizar as respostas com citações formatadas.

### Princípio: UI é apenas um cliente HTTP

```python
import httpx

response = httpx.post(
    f"{settings.api_base_url}/query",
    json={"question": question, "profile": profile.value},
    timeout=60.0,
)
```

O Streamlit **nunca** acessa o LM Studio, o ChromaDB ou qualquer módulo Python do backend diretamente. Toda a lógica — cold start, disclaimer, citações — é controlada pela API. Isso garante que as regras de segurança médica sejam aplicadas independentemente do cliente.

### Componentes da interface

1. **Seletor de perfil** (`st.selectbox`): escolhe entre MEDICO, ENFERMEIRO, ASSISTENTE, PACIENTE
2. **Seletor de tipos de documento** (`st.multiselect`): filtra bulas, diretrizes, protocolos, manuais
3. **Campo de pergunta** (`st.text_area`): entrada da consulta
4. **Área de resposta**: exibe `answer` com markdown renderizado
5. **Seção de citações** (`st.expander`): lista expandível com cada `CitationItem`
6. **Disclaimer** (`st.warning`): sempre visível abaixo da resposta

---

## 16. Módulo: `src/medasist/config.py`

### Responsabilidade

Fonte única de configuração de toda a aplicação via `pydantic-settings`. Todos os módulos importam `get_settings()` em vez de ler variáveis de ambiente diretamente.

### Singleton thread-safe

```python
_settings: Settings | None = None

def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
```

O singleton garante que o `.env` é lido apenas uma vez por processo. Em testes, pode-se injetar um `Settings` mock sem alterar variáveis de ambiente globais.

### Configurações de chunking por DocType

```python
chunk_size_bula:       int = Field(default=600)
chunk_overlap_bula:    int = Field(default=100)
chunk_size_diretriz:   int = Field(default=800)
chunk_overlap_diretriz: int = Field(default=150)
# ...
```

O padrão de nomenclatura `chunk_size_{doc_type.value}` permite que o `chunker.py` acesse dinamicamente:

```python
size = getattr(settings, f"chunk_size_{doc.doc_type.value}")
```

### Textos fixos de segurança em config

```python
disclaimer: str = Field(
    default="Este sistema é um auxiliar informativo e não substitui avaliação médica presencial."
)
cold_start_message: str = Field(
    default="Não encontrei essa informação nos documentos disponíveis. Por favor, consulte um profissional de saúde."
)
```

Os textos de segurança estão em `Settings` — não hardcodados nos módulos. Isso permite que o administrador os personalize via `.env` (ex: versão em inglês para um hospital internacional) sem alterar código.

### `SecretStr` para segredos

```python
lm_studio_api_key: SecretStr = Field(default=SecretStr("lm-studio"))
admin_api_key:     SecretStr = Field(default=SecretStr("dev-only"))
```

`SecretStr` do Pydantic oculta o valor em logs e `repr()`. Se alguém imprime `settings` por engano, a API key não aparece em texto claro. Para acessar o valor real: `settings.admin_api_key.get_secret_value()`.

---

## 17. Decisões de Arquitetura (ADRs)

### ADR-001: LM Studio em vez de API OpenAI

**Contexto:** O MedAssist precisa de LLM para geração e de modelo de embeddings para indexação vetorial.

**Opções consideradas:**
- `openai` API direta: simples, modelos poderosos
- LM Studio local: zero custo, privacidade total, offline

**Decisão:** LM Studio via interface compatível com OpenAI

**Justificativa:** Em contexto médico, privacidade de dados é crítica — perguntas e documentos jamais devem sair da infraestrutura local. Além disso, o projeto é de portfólio: custo zero de API permite rodar indefinidamente. O LangChain `OpenAI`-compatible client funciona sem modificação de código — apenas muda `base_url`.

**Consequência:** Desempenho inferior a GPT-4o. Mitigation: escolha de modelos adequados (phi-3-mini para LLM, nomic-embed-text para embeddings) e ajuste de temperature por perfil.

---

### ADR-002: Uma coleção ChromaDB por DocType

**Contexto:** Os documentos médicos têm tipos bem distintos (bulas vs diretrizes vs protocolos). A busca vetorial pode misturar tipos se todos estiverem na mesma coleção.

**Opções consideradas:**
- Uma única coleção com filtro `where={"doc_type": "bula"}` pós-ANN
- Coleções separadas por DocType

**Decisão:** Coleções separadas

**Justificativa:** O filtro pós-ANN degrada o recall — o ANN calcula vizinhos em todo o espaço e só depois descarta os do tipo errado. Com coleções separadas, o ANN opera apenas sobre os documentos relevantes, maximizando a qualidade dos chunks recuperados. O custo é manter quatro índices HNSW em vez de um.

**Consequência:** Quando `doc_types` não é especificado na requisição, é necessário consultar as quatro coleções e unir os resultados antes do MMR — complexidade adicional no retriever.

---

### ADR-003: Cold start obrigatório via score threshold

**Contexto:** Em domínio médico, responder com informação incorreta é mais perigoso do que não responder.

**Opções consideradas:**
- Deixar o LLM responder sempre, mesmo sem contexto relevante
- Score threshold: LLM só é chamado se houver chunks com score ≥ threshold

**Decisão:** Score threshold com mensagem fixa de cold start

**Justificativa:** Um LLM sem contexto relevante alucinará — inventará contraindicações, dosagens ou interações inexistentes. Em contexto médico, uma alucinação pode causar dano real. A mensagem fixa "Não encontrei essa informação" é mais honesta e segura do que uma resposta inventada. Custo adicional: zero — o LLM não é invocado, sem token cost.

**Consequência:** Threshold muito alto (ex: 0.7) tornaria o sistema inutilizável — muito cold start. Threshold muito baixo (ex: 0.1) permitiria alucinações. O valor padrão 0.4 foi calibrado para o modelo nomic-embed-text com documentos em português.

---

### ADR-004: Perfis de usuário com temperature diferente

**Contexto:** Médicos precisam de respostas técnicas precisas; pacientes precisam de linguagem acessível.

**Opções consideradas:**
- Uma única configuração de LLM para todos os usuários
- Perfis com temperature e prompt template diferentes

**Decisão:** Quatro perfis (MEDICO, ENFERMEIRO, ASSISTENTE, PACIENTE)

**Justificativa:** Temperature baixa (0.1) para médicos minimiza variação e prioriza fidelidade ao contexto. Temperature ligeiramente maior (0.3) para pacientes permite paráfrase na linguagem, tornando a resposta mais compreensível sem sacrificar a precisão das informações. Templates diferentes ajustam o vocabulário e o nível de detalhe técnico.

**Consequência:** Quatro chains LCEL instanciadas no startup (uma por perfil). Overhead de memória e tempo de startup mínimos — cada chain é uma composição de objetos leves.

---

### ADR-005: Idempotência via SHA-256 no pipeline de ingestão

**Contexto:** O diretório `data/raw/` pode ter documentos novos misturados com documentos já processados. Re-processar documentos já indexados geraria duplicatas no ChromaDB.

**Opções consideradas:**
- Limpar o ChromaDB antes de cada ingestão (re-ingerir tudo)
- Rastrear hashes SHA-256 dos arquivos processados

**Decisão:** Hash SHA-256 persistido em `data/processed/hashes.json`

**Justificativa:** Limpar e re-ingerir tudo é O(n_total) a cada execução — inaceitável quando o acervo tem centenas de documentos. Com hashes, cada execução é O(n_novos). O SHA-256 detecta mudanças no conteúdo do arquivo — se uma bula é atualizada (mesmo nome, conteúdo diferente), o novo hash não coincide e o documento é re-processado.

**Consequência:** O arquivo `hashes.json` precisa ser persistido entre execuções do pipeline — não pode ser efêmero. Em deploy com Docker, deve ser montado como volume.

---

### ADR-006: Disclaimer adicionado pela API, não pelo LLM

**Contexto:** O disclaimer médico deve aparecer em todas as respostas, sem exceção.

**Opções consideradas:**
- Incluir o disclaimer no prompt e esperar que o LLM o reproduza
- API adiciona o disclaimer programaticamente ao `QueryResponse`

**Decisão:** API adiciona o disclaimer como campo `disclaimer` no JSON de resposta

**Justificativa:** Depender do LLM para incluir o disclaimer é não-determinístico — o modelo pode reformulá-lo, omiti-lo ou posicioná-lo diferente. A API garante que o campo sempre existe, sempre tem o texto exato de `settings.disclaimer`, e a UI sempre o exibe.

**Consequência:** O disclaimer é uma responsabilidade da API, não do LLM. Qualquer cliente que consome a API (Streamlit, mobile, terceiros) recebe o disclaimer e é responsável por exibi-lo.

---

## 18. Infra e Deploy

### Docker

```dockerfile
# Dockerfile — API
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY src/ src/
CMD ["uvicorn", "medasist.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

```dockerfile
# Dockerfile — UI
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY src/ src/
CMD ["streamlit", "run", "src/medasist/ui/app.py", "--server.port", "8501"]
```

### Docker Compose

```yaml
# docker-compose.yml (simplificado)
services:
  api:
    build: .
    ports: ["8000:8000"]
    volumes:
      - ./chroma_db:/app/chroma_db      # ChromaDB persiste entre restarts
      - ./data:/app/data                 # documentos e hashes.json
    environment:
      - LM_STUDIO_BASE_URL=http://host.docker.internal:1234/v1

  ui:
    build: .
    ports: ["8501:8501"]
    environment:
      - API_BASE_URL=http://api:8000
    depends_on: [api]
```

O ChromaDB e o `hashes.json` são montados como volumes — não ficam dentro da imagem. Isso permite atualizar a imagem sem perder o índice vetorial ou o histórico de ingestão.

O LM Studio roda na máquina host — `host.docker.internal` resolve para o host em Docker Desktop (Windows/Mac). Em Linux, usar `--add-host=host.docker.internal:host-gateway`.

### Docker Compose de desenvolvimento

```yaml
# docker-compose.dev.yml
services:
  api:
    volumes:
      - ./src:/app/src   # hot reload do código
    command: ["uvicorn", "medasist.api.main:app", "--reload", "--host", "0.0.0.0"]
```

### Variáveis de Ambiente (`.env`)

```bash
# LM Studio
LM_STUDIO_BASE_URL=http://localhost:1234/v1
LM_STUDIO_API_KEY=lm-studio
LM_STUDIO_LLM_MODEL=phi-3-mini
LM_STUDIO_EMBEDDING_MODEL=nomic-embed-text

# API
API_HOST=0.0.0.0
API_PORT=8000
ADMIN_API_KEY=troque-em-producao

# UI
API_BASE_URL=http://localhost:8000

# Retrieval
RETRIEVAL_TOP_K=10
RETRIEVAL_SCORE_THRESHOLD=0.4

# Logs
LOG_LEVEL=INFO
```

Copiado de `.env.example` (versionado) e preenchido localmente (não versionado).

### CI com GitHub Actions

A cada push, executa automaticamente:

```
1. pip install -r requirements.txt -r requirements-dev.txt
2. black --check src/ tests/        (formatação)
3. flake8 src/ tests/               (lint + bugbear)
4. pytest tests/ -v --cov=src --cov-fail-under=80
```

Se qualquer passo falhar, o commit é marcado como vermelho. A cobertura mínima de 80% é obrigatória — o CI rejeita PRs que reduzam cobertura abaixo desse limite.

---

## 19. Referências e Leituras

### RAG (Retrieval-Augmented Generation)

- **Paper original:** Lewis, P. et al. (2020). *Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks.* NeurIPS 2020. Fundamento teórico do padrão RAG.
- **LangChain RAG docs:** [python.langchain.com/docs/use_cases/question_answering](https://python.langchain.com/docs/use_cases/question_answering)

### ChromaDB

- **Documentação oficial:** [docs.trychroma.com](https://docs.trychroma.com) — coleções, embeddings, filtros, persistência
- **HNSW paper:** Malkov, Y. A. & Yashunin, D. A. (2018). *Efficient and robust approximate nearest neighbor search using Hierarchical Navigable Small World graphs.* IEEE TPAMI.

### LM Studio

- **Site oficial:** [lmstudio.ai](https://lmstudio.ai) — download, modelos suportados, configuração de servidor local

### Modelos recomendados para LM Studio

- **LLM:** `phi-3-mini` (Microsoft) — 3.8B params, rápido, bom em português
- **Embeddings:** `nomic-embed-text` — 768 dims, alta qualidade para recuperação em português

### LangChain LCEL

- **Documentação:** [python.langchain.com/docs/expression_language](https://python.langchain.com/docs/expression_language) — composição de chains, RunnableLambda, passthrough

### FastAPI

- **Documentação oficial:** [fastapi.tiangolo.com](https://fastapi.tiangolo.com) — Pydantic, lifespan, Depends, middleware

### Extração de PDF

- **pdfplumber:** [github.com/jsvine/pdfplumber](https://github.com/jsvine/pdfplumber) — extração de texto com layout
- **PyMuPDF:** [pymupdf.readthedocs.io](https://pymupdf.readthedocs.io) — engine MuPDF, fallback robusto

### Boas práticas Python

- **pathlib:** [PEP 428](https://peps.python.org/pep-0428/) — motivação para usar `Path` em vez de `str`
- **from __future__ import annotations:** [PEP 563](https://peps.python.org/pep-0563/) — avaliação lazy de anotações de tipo
- **pydantic-settings:** [docs.pydantic.dev/latest/concepts/pydantic_settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings) — configuração tipada via `.env`

### Segurança em sistemas médicos

- **OWASP Top 10:** [owasp.org/www-project-top-ten](https://owasp.org/www-project-top-ten) — vulnerabilidades comuns em APIs
- **Alucinações em LLMs médicos:** Singhal, K. et al. (2023). *Large Language Models Encode Clinical Knowledge.* Nature. Contexto sobre riscos de LLMs sem RAG em domínio médico.
