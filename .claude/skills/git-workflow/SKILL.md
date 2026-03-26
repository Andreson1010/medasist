# Skill: git-workflow

Padrão global para fluxo Git em qualquer projeto.

## Conventional Commits

Todo commit deve seguir o formato:

```
<type>(<scope>): <descrição imperativa em minúsculas>

[corpo opcional — explica o "por quê", não o "o quê"]

[rodapé opcional: Breaking Changes, closes #issue]
```

### Tipos permitidos

| Tipo       | Quando usar                                      |
|------------|--------------------------------------------------|
| `feat`     | Nova funcionalidade                              |
| `fix`      | Correção de bug                                  |
| `refactor` | Refatoração sem mudança de comportamento         |
| `test`     | Adição ou correção de testes                     |
| `docs`     | Documentação apenas                              |
| `chore`    | Build, CI, dependências, configs                 |
| `perf`     | Melhoria de performance                          |
| `style`    | Formatação, ponto-e-vírgula — sem lógica         |

### Exemplos

```bash
git commit -m "feat(recommendations): add hybrid score weighting"
git commit -m "fix(api): handle cold-start user in recommendations endpoint"
git commit -m "test(collaborative): add edge case for empty interaction matrix"
git commit -m "chore(deps): upgrade sentence-transformers to 2.7"
```

## Nomes de branch

```
<type>/<ticket-ou-descricao-curta>

feat/hybrid-reranking
fix/cold-start-fallback
refactor/svd-training-pipeline
chore/update-ci-python312
```

## Fluxo de trabalho

### 1. Iniciar uma tarefa

```bash
git checkout main && git pull
git checkout -b feat/<descricao>
```

### 2. Durante o desenvolvimento

- Commits pequenos e atômicos — um commit = uma mudança lógica
- Nunca commitar diretamente em `main` ou `develop`
- Rodar testes antes de cada commit:
  ```bash
  pytest tests/ --tb=short -q
  ```

### 3. Antes de abrir PR

```bash
# Atualizar com main para evitar conflitos
git fetch origin
git rebase origin/main

# Conferir o que vai no PR
git diff origin/main...HEAD
git log origin/main...HEAD --oneline
```

### 4. Abrir Pull Request

Título do PR segue o mesmo padrão de commit:
```
feat(scope): descrição curta
```

Corpo do PR deve conter:

```markdown
## O que foi feito
- item 1
- item 2

## Por que foi feito
Contexto da decisão / problema resolvido.

## Como testar
1. passo 1
2. passo 2

## Checklist
- [ ] Testes adicionados/atualizados
- [ ] Docstrings e type hints completos
- [ ] `black` e `flake8` passando
- [ ] Sem `print` ou credenciais hardcoded
```

### 5. Code review — o que verificar

- [ ] A mudança resolve exatamente o que o PR descreve?
- [ ] Há testes para os novos caminhos de código?
- [ ] Algum edge case não tratado?
- [ ] Nomes de variáveis e funções são claros sem precisar de comentário?
- [ ] Alguma dependência nova foi justificada?

### 6. Merge

Preferir **Squash and Merge** para manter histórico limpo em `main`.
Deletar a branch após o merge.

## Checklist rápido antes de cada commit

- [ ] `pytest tests/ -q` passa
- [ ] Nenhum arquivo de segredo ou `.env` staged
- [ ] Mensagem de commit no formato convencional
- [ ] Branch atualizada com `main`
