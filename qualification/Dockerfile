# Local, disposable app previews. This image is not a deployment artifact.
FROM node@sha256:ec857ea6bb60cb3d0fa4d97495590c5fe7d51be8572c307b55957eeb06569cf7

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_DISABLE_PIP_VERSION_CHECK=1
ENV MOZAIKS_WEB_SHELL_PATH=/opt/mozaiks/web_shell
ENV MOZAIKS_CHAT_UI_PATH=/opt/mozaiks/chat-ui
ENV MOZAIKS_FACTORY_APP_PATH=/opt/mozaiks/factory_app
ENV LOGS_BASE_DIR=/workspace/logs

WORKDIR /opt/mozaiks
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        gnupg \
        libcurl4 \
        libgssapi-krb5-2 \
        liblzma5 \
        libssl3 \
        python3 \
        python3-pip \
    && curl -fsSL https://pgp.mongodb.com/server-7.0.asc \
        | gpg --dearmor -o /usr/share/keyrings/mongodb-server-7.0.gpg \
    && echo "deb [ signed-by=/usr/share/keyrings/mongodb-server-7.0.gpg ] https://repo.mongodb.org/apt/debian bookworm/mongodb-org/7.0 main" \
        > /etc/apt/sources.list.d/mongodb-org-7.0.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends mongodb-org-server=7.0.45 \
    && ln -s /usr/bin/python3 /usr/local/bin/python \
    && python --version \
    && mongod --version \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml setup.py MANIFEST.in README.md LICENSE ./
COPY mozaiks/ ./mozaiks/
COPY mozaiksai/ ./mozaiksai/
COPY mozaiks_cli/ ./mozaiks_cli/
COPY logs/ ./logs/
COPY factory_app/ ./factory_app/
COPY web_shell/ ./web_shell/
COPY chat-ui/ ./chat-ui/
COPY dist/*.whl /tmp/wheels/
COPY constraints.txt /tmp/constraints.txt
RUN pip install --no-cache-dir -c /tmp/constraints.txt /tmp/wheels/*.whl --break-system-packages \
    && pip list --format=freeze > /opt/mozaiks/preview-constraints.txt \
    && npm ci --prefix web_shell --no-audit --no-fund \
    && useradd --create-home --uid 10001 sandbox \
    && mkdir -p /workspace/app /workspace/logs \
    && chown -R sandbox:sandbox /workspace /opt/mozaiks/web_shell

USER sandbox
WORKDIR /workspace
EXPOSE 3000 8000
CMD ["sleep", "1800"]

USER root
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/mozaiks/playwright-browsers
RUN cd /opt/mozaiks/web_shell && npx --no-install playwright install --with-deps chromium && chown -R sandbox:sandbox /opt/mozaiks/playwright-browsers
RUN python - <<'PY'
import json, sysconfig, zipfile
from pathlib import Path
installed = Path(sysconfig.get_paths()["purelib"])
with zipfile.ZipFile(next(Path("/tmp/wheels").glob("*.whl"))) as wheel:
    for name in wheel.namelist():
        if name.endswith("/") or ".dist-info/" in name:
            continue
        assert (installed / name).read_bytes() == wheel.read(name), name
        source_name = name.replace("mozaiks_chat_ui/", "chat-ui/", 1)
        source_path = Path("/opt/mozaiks") / source_name
        if source_name == "mozaiksai/_build_revision.json":
            assert json.loads(source_path.read_bytes()) == json.loads(wheel.read(name))
        else:
            assert source_path.read_bytes() == wheel.read(name), source_name
print("Installed wheel and image source payloads are byte-identical; source-image proof, not installed-only")
PY
LABEL io.mozaiks.browser.source_sha="7d52ab4bf8131f62bf69438dfdc9302f18b2b29d"
LABEL io.mozaiks.browser.spec_sha256="e6d81e062e661852470371cedcbe93b5da6d910444174141060d8aeb55cc64b1"
LABEL io.mozaiks.browser.paired_spec_sha256="4f2612ee4cdc4a5245f96d21da3d2c4fd132ae0788fe72f84fd2dc6ad5f93063"
LABEL io.mozaiks.browser.helper_sha256="9dca5b755fbfdcb2f06fdc6a55babc7e9fdcefab36149065f8520ec048493a73"
LABEL io.mozaiks.browser.config_sha256="d58c54ac7b58cdd2b2a0b9bda24c6b086c13a1926d4ff9f6d1d02a0aebca79f3"
USER sandbox
WORKDIR /workspace
