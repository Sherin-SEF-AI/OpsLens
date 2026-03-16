# Stage 1: Build frontend
FROM node:20-slim AS frontend-build

WORKDIR /frontend

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY frontend/ .
RUN npm run build

# Stage 2: Backend
FROM python:3.11-slim AS backend

# Prevent Python from writing .pyc files and enable unbuffered output
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install system dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        curl \
        libpq-dev \
        gcc \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies first for better layer caching
COPY pyproject.toml .
RUN pip install --no-cache-dir -e . && \
    pip install --no-cache-dir \
        google-genai \
        python-multipart \
        slack-sdk \
        celery[redis] \
        python-jose[cryptography] \
        alembic \
        asyncpg \
        psycopg2-binary

# Copy application source
COPY src/ src/
COPY alembic/ alembic/
COPY alembic.ini .

# Copy frontend build from stage 1
COPY --from=frontend-build /frontend/dist src/dashboard/

# Remove gcc after pip install (no longer needed)
RUN apt-get purge -y gcc && \
    apt-get autoremove -y && \
    rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN groupadd --gid 1001 opslens && \
    useradd --uid 1001 --gid opslens --shell /bin/bash --create-home opslens && \
    chown -R opslens:opslens /app

USER opslens

HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -sf http://localhost:8000/healthz || exit 1

EXPOSE 8000

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
