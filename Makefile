.PHONY: up down dev build logs ingest test lint format check


## Produção
up:
	docker compose up -d

down:
	docker compose down

build:
	docker compose build --no-cache

logs:
	docker compose logs -f

## Desenvolvimento (hot reload)
.req-hash:
	md5sum requirements-api.txt > .req-hash

dev: .req-hash
	@if ! md5sum -c .req-hash --quiet 2>/dev/null; then \
		echo "requirements alterado — rebuilding..."; \
		docker compose build api; \
		md5sum requirements-api.txt > .req-hash; \
	fi
	docker compose -f docker-compose.yml -f docker-compose.dev.yml up

## Ingestão de documentos (dentro do container da API)
ingest:
	docker compose exec api python scripts/ingest_docs.py --dir /app/data/raw --doc-type bula

## Ingestão local (fora do container)
ingest-local:
	python scripts/ingest_docs.py --dir data/raw --doc-type bula

## Qualidade
test:
	pytest tests/ -v --cov=src --cov-fail-under=80

lint:
	flake8 src/ tests/ scripts/

format:
	black src/ tests/ scripts/

## Verificação end-to-end
check:
	@echo "Verificando API..."
	@curl -sf http://localhost:8000/health && echo " API OK" || echo " API INDISPONÍVEL"
	@echo "Verificando UI..."
	@curl -sf http://localhost:8501/_stcore/health && echo " UI OK" || echo " UI INDISPONÍVEL"
