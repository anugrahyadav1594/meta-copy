# MetaScale API image
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/packages:/app/services/shard-router:/app/apps:/app

WORKDIR /app

# psycopg[binary] ships self-contained libpq -> no build toolchain required.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Application code
COPY packages ./packages
COPY services/shard-router ./services/shard-router
COPY apps ./apps
COPY scripts ./scripts
COPY migrations ./migrations
COPY alembic.ini ./
COPY infrastructure ./infrastructure

EXPOSE 8000

# Simple container-level liveness probe using the already-installed httpx.
HEALTHCHECK --interval=10s --timeout=5s --start-period=20s --retries=5 \
    CMD python -c "import httpx,sys; sys.exit(0 if httpx.get('http://localhost:8000/health', timeout=3).status_code==200 else 1)"

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]
