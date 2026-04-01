# Stage 1: builder — instala dependências com ferramentas de compilação
FROM python:3.11-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /build
COPY requirements-api.txt .
RUN pip install --no-cache-dir -r requirements-api.txt

# Stage 2: runtime — imagem enxuta sem ferramentas de build
FROM python:3.11-slim AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /app

COPY src/ ./src/
COPY pyproject.toml .
COPY scripts/ ./scripts/

RUN pip install --no-cache-dir -e . --no-deps

RUN useradd -m -u 1001 appuser \
    && mkdir -p /app/chroma_db /app/data/raw /app/logs \
    && chown -R appuser:appuser /app /opt/venv

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["python", "-m", "uvicorn", "medasist.api.main:app", \
     "--host", "0.0.0.0", "--port", "8000"]
