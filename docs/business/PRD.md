# PRD — MedAssist

**Versão:** 1.0
**Data:** 2026-03-30
**Status:** Em desenvolvimento

---

## 1. Visão do Produto

MedAssist é um sistema de assistência clínica digital baseado em RAG (Retrieval-Augmented Generation) que permite a profissionais de saúde e pacientes consultarem informações médicas confiáveis — bulas, diretrizes clínicas, protocolos e manuais — de forma rápida, citada e adaptada ao perfil do usuário.

O sistema opera inteiramente com LLM local (LM Studio), garantindo que dados sensíveis não trafeguem para serviços externos.

---

## 2. Problema

Profissionais de saúde e pacientes enfrentam dificuldades para acessar informações médicas precisas no momento em que precisam:

- Bulas e diretrizes são documentos extensos e técnicos, difíceis de consultar rapidamente
- Motores de busca genéricos retornam resultados não-confiáveis ou desatualizados
- Sistemas existentes não adaptam a linguagem ao perfil do usuário (médico vs. paciente)
- Respostas sem citação de fonte não permitem verificação da informação

---

## 3. Objetivos

| # | Objetivo | Métrica de Sucesso |
|---|----------|-------------------|
| O1 | Fornecer respostas baseadas em documentos oficiais | 100% das respostas com ao menos 1 citação |
| O2 | Adaptar linguagem ao perfil do usuário | 4 perfis distintos implementados |
| O3 | Evitar alucinações em contexto sem informação | Cold start ativo para score < 0.4 |
| O4 | Garantir privacidade dos dados | Zero chamadas externas — LLM 100% local |
| O5 | Manter qualidade de código | Cobertura de testes ≥ 80% |

---

## 4. Usuários-Alvo

| Perfil | Descrição | Necessidade Principal |
|--------|-----------|----------------------|
| **Médico** | Clínicos, especialistas | Informação técnica precisa, dose, interações |
| **Enfermeiro** | Equipe de enfermagem | Protocolos, procedimentos assistenciais |
| **Assistente Administrativo** | Recepção, administrativo | Informações básicas de agendamento e triagem |
| **Paciente** | Usuário final leigo | Linguagem acessível, orientações gerais |

---

## 5. Funcionalidades

### 5.1 Consulta RAG (MVP)

**Descrição:** O usuário faz uma pergunta em linguagem natural. O sistema recupera os trechos mais relevantes dos documentos indexados e gera uma resposta citada, adaptada ao perfil.

**Critérios de Aceite:**
- [ ] Resposta gerada em menos de 10 segundos
- [ ] Toda resposta inclui ao menos uma citação `[N] <doc> — Seção: <seção>, Pág. <pág>`
- [ ] Toda resposta inclui o disclaimer médico obrigatório
- [ ] Retrieval sem resultado acima do threshold retorna mensagem de cold start (sem chamada ao LLM)
- [ ] Perfil do usuário altera temperatura, max_tokens e template de prompt

### 5.2 Ingestão de Documentos

**Descrição:** Administrador faz upload de PDFs. O sistema extrai texto, divide em chunks, gera embeddings e indexa no ChromaDB.

**Critérios de Aceite:**
- [ ] Suporte a PDFs com texto nativo (pdfplumber) e escaneados (PyMuPDF/OCR)
- [ ] Ingestão idempotente: mesmo documento (SHA-256) não é re-processado
- [ ] Metadados por chunk: tipo, fonte, seção, página
- [ ] Endpoint protegido por `X-Admin-Key`

### 5.3 Perfis de Usuário

**Descrição:** Cada consulta é feita com um perfil que determina o comportamento do LLM.

| Perfil | Temperature | Max Tokens | Estilo |
|--------|-------------|-----------|--------|
| MEDICO | 0.1 | 1024 | Técnico / clínico |
| ENFERMEIRO | 0.15 | 1024 | Técnico / assistencial |
| ASSISTENTE | 0.2 | 512 | Administrativo |
| PACIENTE | 0.3 | 512 | Simples / acessível |

### 5.4 Interface Web (Streamlit)

**Descrição:** Interface web que consome a API FastAPI. O usuário seleciona o perfil, digita a pergunta e visualiza a resposta com as citações.

**Critérios de Aceite:**
- [ ] Seleção de perfil antes da consulta
- [ ] Exibição de resposta com citações numeradas
- [ ] Exibição do disclaimer
- [ ] Nunca acessa OpenAI/LM Studio diretamente — sempre via API

---

## 6. Tipos de Documento Suportados

| DocType | Coleção | Exemplos |
|---------|---------|---------|
| `BULA` | `bulas` | Bulas de medicamentos (ANVISA) |
| `DIRETRIZ` | `diretrizes` | Diretrizes clínicas (CFM, SBEM, etc.) |
| `PROTOCOLO` | `protocolos` | Protocolos assistenciais hospitalares |
| `MANUAL` | `manuais` | Manuais técnicos e operacionais |

---

## 7. Regras de Negócio (Inegociáveis)

| # | Regra |
|---|-------|
| RN1 | Toda resposta da API deve incluir o disclaimer médico |
| RN2 | Retrieval com score abaixo do threshold → cold start (mensagem fixa, zero LLM) |
| RN3 | Toda resposta deve citar ao menos uma fonte com documento, seção e página |
| RN4 | Nenhum dado real de paciente pode aparecer em código, testes ou logs |

---

## 8. Requisitos Não-Funcionais

| Requisito | Valor Alvo |
|-----------|-----------|
| Latência de resposta | < 10s (P95) |
| Cobertura de testes | ≥ 80% |
| LLM | 100% local (LM Studio) — zero custo por token |
| Privacidade | Dados não trafegam para serviços externos |
| Rate limiting | Habilitado via slowapi |
| Logs | JSON estruturado, sem dados de pacientes |

---

## 9. Fora do Escopo (v1.0)

- Autenticação de usuários finais (login/senha)
- Multitenancy
- Suporte a outros formatos além de PDF
- Fine-tuning de modelos
- Deploy em nuvem (v1.0 é local/on-premise)
- Histórico de conversas persistido por usuário

---

## 10. Fases de Desenvolvimento

| Fase | Escopo | Status |
|------|--------|--------|
| Fase 1 | Ingestion pipeline (loader, chunker, metadata, pipeline) | ✅ Concluída |
| Fase 2 | Vectorstore + Retrieval (ChromaDB, retriever MMR) | ✅ Concluída |
| Fase 3 | Testes e qualidade (pytest ≥ 80%, black, flake8) | ✅ Concluída |
| Fase 4 | Profiles (UserProfile enum, ProfileConfig, prompt templates) | ✅ Concluída |
| Fase 5 | Generation (chain LCEL, citations, cold start) | 🔄 Pendente |
| Fase 6 | API (FastAPI endpoints, rate limiting, lifespan) | 🔄 Pendente |
| Fase 7 | UI (Streamlit) | 🔄 Pendente |
| Fase 8 | Docker + deploy local | 🔄 Pendente |

---

## 11. Dependências Técnicas

- LM Studio instalado e rodando localmente (porta 1234)
- Python 3.11+
- Modelos: LLM (ex: Phi-3-mini) + Embeddings (ex: nomic-embed-text) carregados no LM Studio
- PDFs de documentos médicos para ingestão

---

## 12. Riscos

| Risco | Probabilidade | Impacto | Mitigação |
|-------|--------------|---------|-----------|
| Qualidade do LLM local inferior ao GPT-4 | Alta | Médio | Benchmark com RAGAS; ajuste de prompts por perfil |
| OCR com baixa acurácia em PDFs escaneados | Média | Alto | Fallback PyMuPDF; rejeitar chunks com texto ininteligível |
| Cold start frequente com threshold alto | Média | Médio | Calibrar threshold via avaliação RAGAS |
| Uso em contexto clínico real sem validação | Baixa | Crítico | Disclaimer obrigatório; documentar limitações |
