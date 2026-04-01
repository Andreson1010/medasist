FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /app

COPY requirements-ui.txt .
RUN pip install --no-cache-dir -r requirements-ui.txt

COPY src/ ./src/
COPY pyproject.toml .
RUN pip install --no-cache-dir -e . --no-deps

RUN useradd -m -u 1001 appuser \
    && chown -R appuser:appuser /app /opt/venv

USER appuser

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8501/_stcore/health || exit 1

CMD ["python", "-m", "streamlit", "run", "src/medasist/ui/app.py", \
     "--server.address=0.0.0.0", "--server.port=8501"]
