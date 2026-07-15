# syntax=docker/dockerfile:1
# Mozaiks OSS Repo Host Image
# ============================
# Builds the installable `mozaiks` package and serves the first-party
# factory_app/ workspace through the `mozaiks serve` CLI entrypoint.
#
# This image is repo-local operational scaffolding for running Mozaiks OSS
# itself (Studio / platform / runtime hosts). It is not the generated-app
# deployment artifact family — see
# docs/architecture/deployment/oss-infra-and-generated-app-deployment.md.
#
# Build: docker build -t mozaiks-repo-host:latest -f infra/docker/Dockerfile .
# Run:   docker run -p 8000:8000 --env-file .env mozaiks-repo-host:latest

# ------------------------------------------------------------------------------
# Stage 1: Builder — install the mozaiks package into a venv
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

# Install into a virtual env we can copy into the runtime stage
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Package metadata required by pyproject.toml (dynamic version reads
# mozaiksai/version.py; readme/license-files reference README.md and LICENSE).
COPY pyproject.toml setup.py MANIFEST.in README.md LICENSE ./

# Canonical installable packages (mirrors setup.py PACKAGE_INCLUDES plus the
# chat-ui package_dir mapping to mozaiks_chat_ui).
COPY mozaiks/ ./mozaiks/
COPY mozaiksai/ ./mozaiksai/
COPY mozaiks_cli/ ./mozaiks_cli/
COPY logs/ ./logs/
COPY factory_app/ ./factory_app/
COPY web_shell/ ./web_shell/
COPY chat-ui/ ./chat-ui/

# Install from pyproject.toml (the canonical dependency contract), not the
# standalone root requirements.txt, so the image always matches the package's
# real runtime dependencies (e.g. jsonschema, limits, jinja2).
RUN pip install --upgrade pip && pip install .

# ------------------------------------------------------------------------------
# Stage 2: Runtime — minimal image serving the first-party factory_app workspace
# ------------------------------------------------------------------------------
FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    LOGS_BASE_DIR=/app/logs \
    DOCKERIZED=true

WORKDIR /app

# Installed mozaiks package (mozaiksai, mozaiks_cli, factory_app, logs, etc.)
COPY --from=builder /opt/venv /opt/venv

# Physical app workspace that `mozaiks serve .` resolves at runtime. This is
# the first-party Studio/builder workspace, not a generated app bundle.
COPY factory_app/ ./factory_app/

# Create non-root user for security and a writable logs directory
RUN mkdir -p /app/logs \
    && useradd --create-home --shell /bin/bash mozaiks \
    && chown -R mozaiks:mozaiks /app
USER mozaiks

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')" || exit 1

EXPOSE 8000

# Serves the repo-local factory_app/ workspace through the Studio host by
# default. Override at `docker run` time, e.g.:
#   docker run ... mozaiks-repo-host:latest mozaiks serve . --host platform
CMD ["mozaiks", "serve", ".", "--host", "studio"]
