"""Compile a private interactive page in Studio's sandbox, without starting an app host."""

from __future__ import annotations

import base64
import json
import posixpath
import re
from collections.abc import Mapping
from contextlib import asynccontextmanager
from importlib.resources import files as resources
from pathlib import PurePosixPath
from uuid import uuid4

from mozaiksai.core.sandbox.preview_sessions import (
    get_artifact_preview_sessions,
    resolve_preview_provider,
)

_SUPPORTED = {".jsx", ".tsx", ".js", ".mjs", ".ts", ".json", ".css"}
_MEDIA = {".png", ".jpeg", ".jpg", ".webp", ".gif", ".svg", ".woff2"}
_EXTENSIONS = ("", ".jsx", ".tsx", ".js", ".ts", ".json", ".css", "/index.jsx", "/index.tsx", "/index.js")
_JS_IMPORT = re.compile(r"""\b(?:from\s*|import\s*(?:\(\s*)?|require\s*\(\s*)["'](\.[^"'\r\n]+)["']""")
_CSS_IMPORT = re.compile(r"""(?:@import\s+|url\(\s*)["']?([^"'\s;)]+)""")


def _page_input(files: Mapping[str, str | bytes], page: str):
    if page not in files or not page.startswith("ui/") or not page.endswith((".jsx", ".tsx", ".js")):
        raise ValueError("The selected preview page is unavailable")
    inputs = {}
    size = 0
    pending = [page]
    while pending:
        path = pending.pop()
        if path in inputs:
            continue
        name = PurePosixPath(path)
        media = name.suffix in _MEDIA
        supported = path.startswith("ui/") and name.suffix in _SUPPORTED
        supported_asset = media and path.startswith(("ui/", "brand/"))
        if not (supported or supported_asset) or ".." in name.parts or "\\" in path or len(inputs) >= 256:
            raise ValueError("Unsupported preview files")
        content = files[path]
        raw = content.encode() if isinstance(content, str) else content
        size += len(raw)
        if len(raw) > 512_000 or size > 3_000_000:
            raise ValueError("Preview source is too large")
        if media:
            inputs[path] = base64.b64encode(raw).decode("ascii")
            continue
        source = raw.decode("utf-8")
        inputs[path] = source
        imports = _CSS_IMPORT.findall(source) if name.suffix == ".css" else _JS_IMPORT.findall(source)
        for specifier in imports:
            target = posixpath.normpath(posixpath.join(str(name.parent), specifier))
            match = next((target + ext for ext in _EXTENSIONS if target + ext in files), None)
            if match and match not in inputs:
                pending.append(match)
    raw_theme = files.get("brand/theme_config.json", "{}")
    if len(raw_theme) > 128_000:
        raise ValueError("Preview theme is too large")
    theme = json.loads(raw_theme)
    if not isinstance(theme, dict):
        raise ValueError("Invalid app theme")
    keys = ("theme", "colors", "primitives", "fonts", "ui", "shadows")
    return {"page": page, "files": inputs, "brand": {key: theme[key] for key in keys if key in theme}}


class DocumentCompileError(ValueError):
    """The supplied frontend source could not be compiled as an isolated page."""


@asynccontextmanager
async def private_document_renderer(*, app_id: str, user_id: str, target_app_id: str, build_registry_id: str):
    """Yield a renderer bound to one disposable Studio allocation and release it on exit.

    The caller authorizes the source bundle and the recipient of each document.
    The rendered HTML must be hosted in an opaque, script-only sandbox frame.
    The normal Studio owner quota also covers this allocation.
    """
    manager = get_artifact_preview_sessions()
    source = resources("mozaiksai.core.sandbox").joinpath("compile_document.cjs").read_text(encoding="utf-8")
    state = await manager.create_or_reuse(
        "document-" + uuid4().hex, app_id=app_id, user_id=user_id, target_app_id=target_app_id,
        build_registry_id=build_registry_id, document_only=True,
    )
    try:
        if state.provider != "e2b" or not state.session_id:
            raise RuntimeError("Document preview allocation unavailable")
        _, port = resolve_preview_provider(provider="e2b")
        # Random names avoid collisions in E2B's sticky temporary directory.
        script_name = "mozaiks-compiler-" + uuid4().hex + ".cjs"
        await port.write_files(session_id=state.session_id, cwd="/tmp", files={script_name: source})

        async def render(files: Mapping[str, str | bytes], page: str) -> str:
            document_input = _page_input(files, page)
            job = "mozaiks-preview-" + uuid4().hex
            await port.write_files(
                session_id=state.session_id, cwd="/tmp", files={job + ".json": json.dumps(document_input)},
            )
            result = await port.run_command(
                session_id=state.session_id, cwd="/tmp",
                command=f"node --max-old-space-size=256 {script_name} {job}", timeout_seconds=35,
            )
            if not result.success:
                raise DocumentCompileError("The page could not be compiled")
            document = await port.read_file(session_id=state.session_id, path=f"/tmp/{job}.html")
            if not isinstance(document, str) or not document.startswith("<!doctype html>") or len(document.encode()) > 2_000_000:
                raise DocumentCompileError("Invalid compiled page")
            return document

        yield render
    finally:
        await manager.stop(state.sandbox_id)
