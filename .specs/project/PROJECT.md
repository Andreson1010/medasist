# MedAssist

**Vision:** Sistema de assistência clínica digital baseado em RAG que responde perguntas médicas consultando bulas, diretrizes, protocolos e manuais — citando fontes e adaptando a linguagem ao perfil do usuário.

**For:** Profissionais de saúde (médicos, enfermeiros, assistentes administrativos) e pacientes.

**Solves:** Acesso rápido e confiável a informações de medicamentos, diretrizes clínicas e protocolos assistenciais com rastreabilidade de fontes, sem substituir avaliação médica presencial.

## Goals

- Responder perguntas médicas com citações rastreáveis (`[N] fonte — seção, página`)
- Zero alucinação: cold start obrigatório quando retrieval não encontra nada relevante
- Linguagem adaptada por perfil (médico → técnica, paciente → simples)
- LLM local (LM Studio) — sem custo por token, sem dados saindo da máquina

## Tech Stack

**Core:**

- Language: Python 3.11
- LLM: LM Studio (phi-3-mini) via LangChain LCEL (OpenAI-compatible API)
- Vector Store: ChromaDB 1.0.9 (persistente local, 4 coleções por DocType)
- API: FastAPI 0.115.9 + Uvicorn + slowapi (rate limiting)
- UI: Streamlit 1.45.1 + httpx
- Config: pydantic-settings 2.9.1
- PDF: pdfplumber 0.11.6 + PyMuPDF 1.25.5 (fallback)

**Qualidade:**

- black 24.10.0 (line-length 88)
- ruff 0.9.9 (E/W/F/I/B/UP/C4/SIM)
- pytest 8.3.5 + pytest-cov (80% mínimo)
- Docker + Docker Compose (API + UI em containers separados)

## Scope

**v1 inclui:**

- Pipeline de ingestão idempotente (PDF → chunks → embeddings → ChromaDB)
- Retrieval multi-coleção com score threshold (cold start)
- Geração LCEL com prompts por UserProfile
- Validação de citações (orphan removal, hallucinated markers)
- API FastAPI com 3 endpoints (/health, /query, /ingest)
- UI Streamlit com chat, seletor de perfil e filtro de doc_types
- 4 perfis: médico, enfermeiro, assistente, paciente

**Explicitmente out of scope (v1):**

- Dados reais de pacientes
- Uso em ambiente clínico real sem validação médica e regulatória
- Multi-tenancy
- Autenticação de usuários (apenas admin key para /ingest)

## Constraints

- ** Técnico:** LLM local obrigatório (LM Studio) — sem chamadas para OpenAI cloud
- **Segurança:** 4 regras inegociáveis (disclaimer, cold start, citações, sem dados de paciente)
- **Recursos:** Projeto acadêmico/portfolio — sem orçamento para infraestrutura cloud

## estrutura

meu-rag-agentico/
├── .env.example                # Modelo de variáveis de ambiente (Chaves de API, DBs)
├── .gitignore                  # Arquivos ignorados pelo Git (venv, logs, .env)
├── README.md                   # Documentação do projeto, setup e guias de execução
├── pyproject.toml              # Gerenciamento de dependências modernas (Poetry/Rye/Pip)
├── requirements.txt            # Dependências em formato padrão (fallback)
│
├── config/                     # CONFIGURAÇÕES E PARÂMETROS GLOBAIS
│   ├── __init__.py
│   ├── settings.py             # Variáveis de ambiente validadas via Pydantic Settings
│   └── prompts.py              # Centralização de Prompts do Sistema (Router, Grader, Generator)
│
├── logs/                       # DIRETÓRIO ARQUIVOS DE LOGS LOCAL (Gerado automaticamente)
│   ├── app.log                 # Logs de inicialização e rotas HTTP da API
│   └── agent_decisions.log     # Histórico de tomadas de decisão e loops do Agente
│
├── evals/                      # ESTEIRA DE TESTES E AVALIAÇÃO (OFF-LINE / CI-CD)
│   ├── __init__.py
│   ├── run_evals.py            # Script principal para rodar baterias de testes em massa
│   ├── dataset/
│   │   └── golden_set.json     # Dataset de perguntas gabaritadas (Perguntas/Respostas Ideais)
│   └── metrics/
│       ├── __init__.py
│       ├── faithfulness.py     # Métrica: Validação de Alucinação (Ragas/TruLens)
│       └── relevance.py        # Métrica: Relevância do contexto vs Pergunta
│
├── src/                        # CÓDIGO FONTE DA APLICAÇÃO (ON-LINE)
│   ├── __init__.py
│   ├── main.py                 # Ponto de entrada FastAPI (Exposição do agente via API/Streamlit)
│   │
│   ├── agents/                 # O CÉREBRO AGÊNTICO (GRAFOS E DECISÕES)
│   │   ├── __init__.py
│   │   ├── graph.py            # Definição e compilação do Grafo de Estados (LangGraph/LlamaIndex)
│   │   ├── state.py            # Schema do objeto de Estado compartilhado do Agente (Pydantic/TypedDict)
│   │   ├── router.py           # Agente de Roteamento (Decide qual ferramenta chamar)
│   │   └── grader.py           # Agente Crítico (Avalia se o documento retornado é útil)
│   │
│   ├── tools/                  # FERRAMENTAS/AÇÕES DISPONÍVEIS PARA O AGENTE
│   │   ├── __init__.py
│   │   ├── vector_search.py    # Ferramenta para buscar no banco vetorial interno
│   │   ├── web_search.py       # Ferramenta de contingência (Tavily, Serper, BrightData)
│   │   └── code_executor.py    # Opcional: Sandbox para execução local de códigos python
│   │
│   ├── pipeline/               # PIPELINE DE INGESTÃO E PROCESSAMENTO (ASSÍNCRONO/OFF-LINE)
│   │   ├── __init__.py
│   │   ├── ingest.py           # Script principal para rodar carga de novos documentos
│   │   ├── loaders.py          # Adaptadores para carregar PDFs, Notion, Confluence ou S3
│   │   └── splitters.py        # Algoritmos avançados de Chunking (Semantic ou Character Splitter)
│   │
│   ├── database/               # CONEXÕES COM BANCOS DE DADOS
│   │   ├── __init__.py
│   │   └── vector_store.py     # Inicialização e conexões (Chroma, Qdrant, PGVector, Pinecone)
│   │
│   └── utils/                  # AUXILIARES TRANSVERSAIS DA APLICAÇÃO
│       ├── __init__.py
│       ├── logger.py           # Configuração de Logs Estruturados Rotativos (JSON/Console)
│       └── tracer.py           # Configuração e inicialização de Telemetria (Langfuse/OpenTelemetry)
│
└── tests/                      # TESTES UNITÁRIOS E DE INTEGRAÇÃO TRADICIONAIS
    ├── __init__.py
    ├── conftest.py             # Fixtures comuns para os testes
    ├── test_agents.py          # Testes de roteamento de intenção
    └── test_tools.py           # Testes isolados das ferramentas de busca
