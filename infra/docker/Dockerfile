# syntax=docker/dockerfile:1
# MozaiksAI Runtime - Production Dockerfile
# ==========================================
# Build: docker build -t mozaiks/runtime:latest -f infra/docker/Dockerfile .
# Run:   docker run -p 8000:8000 --env-file .env mozaiks/runtime:latest

# ------------------------------------------------------------------------------
# Stage 1: Builder (install deps, can be cached)
# ------------------------------------------------------------------------------
FROM python:3.13-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=100

WORKDIR /build

# System deps needed for some Python packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps into a virtual env we can copy
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt

# ------------------------------------------------------------------------------
# Stage 2: Runtime (minimal image)
# ------------------------------------------------------------------------------
FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    LOGS_BASE_DIR=/app/logs \
    DOCKERIZED=true

WORKDIR /app

# Copy virtual env from builder
COPY --from=builder /opt/venv /opt/venv

# Copy application code (respects .dockerignore)
COPY mozaiksai/ ./mozaiksai/
COPY workflows/ ./workflows/
COPY config/ ./config/
COPY logs/ ./logs/
COPY scripts/ ./scripts/
COPY shared_app.py run_server.py ./

# Create non-root user for security
RUN useradd --create-home --shell /bin/bash mozaiks \
    && chown -R mozaiks:mozaiks /app
USER mozaiks

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')" || exit 1

EXPOSE 8000

# Default command (production)
CMD ["python", "run_server.py"]
