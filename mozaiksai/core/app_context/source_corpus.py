"""Source-backed repo context bundles for AppContextGraph indexing.

The Context Graph is the relationship layer.  This module owns the bounded
source corpus that backs graph-aware retrieval: selected file contents, chunks,
symbols, imports, and search helpers.  It is storage-free so Studio, workflows,
and CLI code can persist the same payload through their own artifact stores.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .context_graph import ExtractedFileContext, extract_file_context
from .models import SourceRef
from .scan_policy import (
    SourceScanResult,
    priority_for_source_path,
    safe_scan_relpath,
)

SOURCE_CONTEXT_BUNDLE_SCHEMA_VERSION = "mozaiks.source_context.bundle.v1"

_SECRET_KEY_RE = re.compile(
    r"(?i)(password|passwd|secret|token|api[_-]?key|apikey|private[_-]?key|credential)"
)
_ASSIGNMENT_RE = re.compile(r"(?P<prefix>[:=]\s*)(?P<quote>['\"]?)(?P<value>[^'\"\s#]+)(?P=quote)")
_IMPORT_TARGET_EXTENSIONS = (
    "",
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".json",
    ".yaml",
    ".yml",
    "/index.js",
    "/index.jsx",
    "/index.ts",
    "/index.tsx",
)


class SourceCorpusFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1)
    language: str = Field(min_length=1)
    role: str = Field(min_length=1)
    priority_label: str = Field(min_length=1)
    size_bytes: int = 0
    checksum: str = Field(min_length=1)
    chunk_ids: list[str] = Field(default_factory=list)
    symbol_ids: list[str] = Field(default_factory=list)
    import_targets: list[str] = Field(default_factory=list)
    reference_count: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)


class SourceCorpusChunk(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chunk_id: str = Field(min_length=1)
    path: str = Field(min_length=1)
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    checksum: str = Field(min_length=1)
    text: str
    symbol_ids: list[str] = Field(default_factory=list)


class SourceCorpusSymbol(BaseModel):
    model_config = ConfigDict(extra="forbid")

    symbol_id: str = Field(min_length=1)
    path: str = Field(min_length=1)
    name: str = Field(min_length=1)
    kind: str = Field(min_length=1)
    line: int | None = None
    end_line: int | None = None
    qualified_name: str | None = None
    parent: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class SourceCorpusImport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1)
    target: str = Field(min_length=1)
    resolved_path: str | None = None


class SourceCorpusBundle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = SOURCE_CONTEXT_BUNDLE_SCHEMA_VERSION
    bundle_id: str = Field(min_length=1)
    app_id: str = Field(min_length=1)
    source_refs: list[SourceRef] = Field(default_factory=list)
    indexed_at: datetime
    files: list[SourceCorpusFile] = Field(default_factory=list)
    chunks: list[SourceCorpusChunk] = Field(default_factory=list)
    symbols: list[SourceCorpusSymbol] = Field(default_factory=list)
    imports: list[SourceCorpusImport] = Field(default_factory=list)
    file_contents: dict[str, str] = Field(default_factory=dict)
    health: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    parser_status: dict[str, Any] = Field(default_factory=dict)


def build_source_corpus_bundle(
    *,
    app_id: str,
    scan_result: SourceScanResult,
    source_refs: list[SourceRef] | None = None,
    parser_status: dict[str, Any] | None = None,
    indexed_at: datetime | None = None,
    max_chunk_chars: int = 9_000,
) -> SourceCorpusBundle:
    """Build a bounded source corpus from an already selected source scan result."""
    resolved_app_id = str(app_id or "").strip()
    if not resolved_app_id:
        raise ValueError("app_id is required")

    resolved_indexed_at = indexed_at or datetime.now(UTC)
    redacted_file_map = {
        path: redact_source_text(path=path, content=content)
        for path, content in sorted((scan_result.file_map or {}).items())
        if safe_scan_relpath(path)
    }
    extracted_by_path = {
        path: _safe_extract(path=path, content=content)
        for path, content in redacted_file_map.items()
    }

    symbol_index: list[SourceCorpusSymbol] = []
    symbols_by_path: dict[str, list[SourceCorpusSymbol]] = {}
    for path, file_context in extracted_by_path.items():
        entries: list[SourceCorpusSymbol] = []
        for symbol in file_context.symbols:
            symbol_id = _stable_id("sym", f"{path}:{symbol.kind}:{symbol.qualified_name or symbol.name}:{symbol.line or 0}")
            entry = SourceCorpusSymbol(
                symbol_id=symbol_id,
                path=path,
                name=symbol.name,
                kind=symbol.kind,
                line=symbol.line,
                end_line=symbol.end_line,
                qualified_name=symbol.qualified_name,
                parent=symbol.parent,
                metadata=dict(symbol.metadata or {}),
            )
            entries.append(entry)
            symbol_index.append(entry)
        symbols_by_path[path] = entries

    chunk_index: list[SourceCorpusChunk] = []
    chunks_by_path: dict[str, list[SourceCorpusChunk]] = {}
    for path, content in redacted_file_map.items():
        chunks = _chunk_file(
            path=path,
            content=content,
            symbols=symbols_by_path.get(path, []),
            max_chunk_chars=max_chunk_chars,
        )
        chunks_by_path[path] = chunks
        chunk_index.extend(chunks)

    imports: list[SourceCorpusImport] = []
    for path, file_context in extracted_by_path.items():
        for target in file_context.imports:
            imports.append(
                SourceCorpusImport(
                    path=path,
                    target=target,
                    resolved_path=_resolve_relative_import(path=path, target=target, known_paths=set(redacted_file_map)),
                )
            )

    files: list[SourceCorpusFile] = []
    for path, content in redacted_file_map.items():
        file_context = extracted_by_path[path]
        _priority, priority_label = priority_for_source_path(path)
        files.append(
            SourceCorpusFile(
                path=path,
                language=file_context.language,
                role=classify_source_role(path=path, file_context=file_context),
                priority_label=priority_label,
                size_bytes=len(content.encode("utf-8")),
                checksum=file_context.checksum,
                chunk_ids=[chunk.chunk_id for chunk in chunks_by_path.get(path, [])],
                symbol_ids=[symbol.symbol_id for symbol in symbols_by_path.get(path, [])],
                import_targets=list(file_context.imports),
                reference_count=len(file_context.references),
                metadata={
                    "structured_metadata": dict(file_context.structured_metadata or {}),
                    "line_count": _line_count(content),
                },
            )
        )

    files.sort(key=lambda item: (_role_priority(item.role), _priority_label_rank(item.priority_label), item.path))
    bundle_id = _stable_id(
        "source_context",
        f"{resolved_app_id}:{_file_map_checksum(redacted_file_map)}:{resolved_indexed_at.isoformat()}",
    )
    health = dict(scan_result.health or {})
    health.update(
        {
            "source_context_schema_version": SOURCE_CONTEXT_BUNDLE_SCHEMA_VERSION,
            "source_context_bundle_id": bundle_id,
            "source_context_file_count": len(files),
            "source_context_chunk_count": len(chunk_index),
            "source_context_symbol_count": len(symbol_index),
            "source_context_import_count": len(imports),
            "role_counts": dict(sorted(Counter(file.role for file in files).items())),
        }
    )
    return SourceCorpusBundle(
        bundle_id=bundle_id,
        app_id=resolved_app_id,
        source_refs=list(source_refs or []),
        indexed_at=resolved_indexed_at,
        files=files,
        chunks=chunk_index,
        symbols=symbol_index,
        imports=imports,
        file_contents=redacted_file_map,
        health=health,
        warnings=list(scan_result.warnings or []),
        parser_status=dict(parser_status or {}),
    )


def build_source_corpus_catalog(
    bundle: SourceCorpusBundle | dict[str, Any],
    *,
    max_files: int = 80,
    max_chunks: int = 24,
) -> dict[str, Any]:
    """Return prompt/tool-safe source context metadata without dumping every file."""
    resolved = _coerce_bundle(bundle)
    role_counts = Counter(file.role for file in resolved.files)
    language_counts = Counter(file.language for file in resolved.files)
    priority_counts = Counter(file.priority_label for file in resolved.files)
    important_files = sorted(
        resolved.files,
        key=lambda file: (
            _role_priority(file.role),
            _priority_label_rank(file.priority_label),
            file.path,
        ),
    )
    return {
        "present": True,
        "schema_version": resolved.schema_version,
        "bundle_id": resolved.bundle_id,
        "app_id": resolved.app_id,
        "indexed_at": resolved.indexed_at.isoformat(),
        "file_count": len(resolved.files),
        "chunk_count": len(resolved.chunks),
        "symbol_count": len(resolved.symbols),
        "import_count": len(resolved.imports),
        "role_counts": dict(sorted(role_counts.items())),
        "language_counts": dict(sorted(language_counts.items())),
        "priority_counts": dict(sorted(priority_counts.items())),
        "important_files": [
            {
                "path": file.path,
                "role": file.role,
                "language": file.language,
                "priority_label": file.priority_label,
                "size_bytes": file.size_bytes,
                "symbol_count": len(file.symbol_ids),
                "chunk_count": len(file.chunk_ids),
            }
            for file in important_files[:max_files]
        ],
        "representative_chunks": [
            {
                "chunk_id": chunk.chunk_id,
                "path": chunk.path,
                "start_line": chunk.start_line,
                "end_line": chunk.end_line,
                "symbol_ids": list(chunk.symbol_ids),
                "excerpt": _excerpt(chunk.text, max_length=1_200),
            }
            for chunk in resolved.chunks[:max_chunks]
        ],
        "warnings": list(resolved.warnings),
        "health": dict(resolved.health),
    }


def read_source_file_from_bundle(
    bundle: SourceCorpusBundle | dict[str, Any],
    path: str,
    *,
    max_chars: int | None = None,
) -> dict[str, Any]:
    """Read an indexed source file from a corpus bundle."""
    resolved = _coerce_bundle(bundle)
    safe = safe_scan_relpath(path)
    if not safe or safe not in resolved.file_contents:
        return {"present": False, "path": safe or str(path), "reason": "source_file_not_indexed"}
    text = resolved.file_contents[safe]
    return {
        "present": True,
        "path": safe,
        "content": _excerpt(text, max_length=max_chars) if max_chars else text,
        "file": next((file.model_dump(mode="json") for file in resolved.files if file.path == safe), None),
        "symbols": [symbol.model_dump(mode="json") for symbol in resolved.symbols if symbol.path == safe],
        "chunks": [chunk.model_dump(mode="json") for chunk in resolved.chunks if chunk.path == safe],
    }


def search_source_corpus(
    bundle: SourceCorpusBundle | dict[str, Any],
    query: str,
    *,
    max_results: int = 12,
) -> list[dict[str, Any]]:
    """Search indexed paths and chunks with deterministic local scoring."""
    resolved = _coerce_bundle(bundle)
    terms = _keywords(query)
    if not terms:
        return []
    results: list[tuple[int, str, dict[str, Any]]] = []
    files_by_path = {file.path: file for file in resolved.files}
    for chunk in resolved.chunks:
        path_text = chunk.path.lower()
        text = chunk.text.lower()
        matched = [term for term in terms if term in path_text or term in text]
        if not matched:
            continue
        file = files_by_path.get(chunk.path)
        score = len(matched) * 10
        if any(term in path_text for term in matched):
            score += 25
        if file:
            score += max(0, 20 - _role_priority(file.role))
        results.append(
            (
                score,
                chunk.chunk_id,
                {
                    "chunk_id": chunk.chunk_id,
                    "path": chunk.path,
                    "start_line": chunk.start_line,
                    "end_line": chunk.end_line,
                    "matched_terms": matched,
                    "score": score,
                    "role": file.role if file else None,
                    "language": file.language if file else None,
                    "excerpt": _matched_excerpt(chunk.text, matched),
                },
            )
        )
    results.sort(key=lambda item: (-item[0], item[1]))
    return [item for _, _, item in results[:max_results]]


def get_related_source_files(
    bundle: SourceCorpusBundle | dict[str, Any],
    path: str,
    *,
    max_results: int = 16,
) -> list[dict[str, Any]]:
    """Return files connected through relative imports plus same-directory context."""
    resolved = _coerce_bundle(bundle)
    safe = safe_scan_relpath(path)
    if not safe:
        return []
    files_by_path = {file.path: file for file in resolved.files}
    related: dict[str, str] = {}
    for import_entry in resolved.imports:
        if import_entry.path == safe and import_entry.resolved_path:
            related[import_entry.resolved_path] = "imports"
        if import_entry.resolved_path == safe:
            related[import_entry.path] = "imported_by"
    parent = str(PurePosixPath(safe).parent)
    for file_path in files_by_path:
        if file_path != safe and str(PurePosixPath(file_path).parent) == parent:
            related.setdefault(file_path, "same_directory")
    return [
        {
            "path": file_path,
            "relationship": reason,
            "role": files_by_path[file_path].role,
            "language": files_by_path[file_path].language,
        }
        for file_path, reason in list(sorted(related.items()))[:max_results]
        if file_path in files_by_path
    ]


def redact_source_text(*, path: str, content: str) -> str:
    """Redact likely inline secret values while preserving code shape for parsing."""
    safe = safe_scan_relpath(path) or str(path)
    lines: list[str] = []
    in_private_key_block = False
    for raw_line in str(content or "").splitlines():
        line = raw_line
        if "-----BEGIN" in line and "PRIVATE KEY" in line:
            in_private_key_block = True
            lines.append("[REDACTED_PRIVATE_KEY_BLOCK]")
            continue
        if in_private_key_block:
            if "-----END" in line and "PRIVATE KEY" in line:
                in_private_key_block = False
            continue
        if _SECRET_KEY_RE.search(line):
            line = _ASSIGNMENT_RE.sub(lambda match: f"{match.group('prefix')}{match.group('quote')}[REDACTED]{match.group('quote')}", line)
        lines.append(line)
    redacted = "\n".join(lines)
    if str(content or "").endswith("\n"):
        redacted += "\n"
    if safe.lower().endswith((".pem", ".key", ".p12", ".pfx")):
        return "[REDACTED_SECRET_FILE]"
    return redacted


def classify_source_role(*, path: str, file_context: ExtractedFileContext | None = None) -> str:
    safe = safe_scan_relpath(path) or str(path).replace("\\", "/").strip("/")
    lower = safe.lower()
    name = PurePosixPath(lower).name
    parts = set(PurePosixPath(lower).parts)
    metadata = dict((file_context.structured_metadata if file_context else {}) or {})
    if name in {"package.json", "pyproject.toml", "requirements.txt", "poetry.lock", "vite.config.js", "vite.config.ts"}:
        return "manifest"
    if name in {"dockerfile", "docker-compose.yml", "docker-compose.yaml"} or ".github/workflows/" in lower:
        return "deployment"
    if name.endswith((".md", ".rst")):
        return "documentation"
    if "test" in parts or "tests" in parts or name.startswith("test_") or name.endswith((".test.ts", ".test.tsx", ".spec.ts", ".spec.tsx")):
        return "test"
    if _contains_any(lower, ("auth", "login", "oauth", "jwt", "session", "permission", "middleware")):
        return "auth"
    if _contains_any(lower, ("route", "router", "routes", "controller", "controllers", "urls.py", "views.py")):
        return "route"
    if "pages" in parts or "app" in parts and name in {"page.tsx", "page.jsx"}:
        return "page"
    if "components" in parts or name.endswith((".tsx", ".jsx")) and _looks_component_file(name):
        return "component"
    if _contains_any(lower, ("api", "client", "http", "fetch", "axios")) and not _contains_any(lower, ("openapi", "swagger")):
        return "api_client"
    if _contains_any(lower, ("model", "models", "schema", "schemas", "entity", "entities", "migration", "prisma")):
        return "data_model"
    if _contains_any(lower, ("repo", "repository", "dao", "database", "db", "persistence")):
        return "persistence"
    if _contains_any(lower, ("integration", "integrations", "adapter", "adapters", "provider", "providers")):
        return "integration"
    if metadata.get("module_id"):
        return "module_contract"
    if name in {"main.py", "app.py", "server.js", "server.ts", "index.js", "index.ts", "main.tsx", "main.jsx"}:
        return "entrypoint"
    return "source"


def source_corpus_file_map(bundle: SourceCorpusBundle | dict[str, Any]) -> dict[str, str]:
    """Return the redacted full-file corpus map for graph builders."""
    return dict(_coerce_bundle(bundle).file_contents)


def _safe_extract(*, path: str, content: str) -> ExtractedFileContext:
    try:
        return extract_file_context(path=path, content=content)
    except Exception:
        checksum = f"sha256:{hashlib.sha256(str(content or '').encode('utf-8')).hexdigest()}"
        return ExtractedFileContext(path=safe_scan_relpath(path) or path, language="unknown", checksum=checksum)


def _chunk_file(
    *,
    path: str,
    content: str,
    symbols: list[SourceCorpusSymbol],
    max_chunk_chars: int,
) -> list[SourceCorpusChunk]:
    lines = str(content or "").splitlines()
    if not lines:
        return []
    chunks: list[SourceCorpusChunk] = []
    start_index = 0
    while start_index < len(lines):
        end_index = start_index
        char_count = 0
        while end_index < len(lines):
            next_count = char_count + len(lines[end_index]) + 1
            if end_index > start_index and next_count > max_chunk_chars:
                break
            char_count = next_count
            end_index += 1
        chunk_lines = lines[start_index:end_index]
        start_line = start_index + 1
        end_line = max(start_line, end_index)
        text = "\n".join(chunk_lines)
        chunk_id = _stable_id("chunk", f"{path}:{start_line}:{end_line}:{hashlib.sha256(text.encode('utf-8')).hexdigest()}")
        chunks.append(
            SourceCorpusChunk(
                chunk_id=chunk_id,
                path=path,
                start_line=start_line,
                end_line=end_line,
                checksum=f"sha256:{hashlib.sha256(text.encode('utf-8')).hexdigest()}",
                text=text,
                symbol_ids=[
                    symbol.symbol_id
                    for symbol in symbols
                    if _symbol_intersects(symbol, start_line=start_line, end_line=end_line)
                ],
            )
        )
        start_index = end_index
    return chunks


def _symbol_intersects(symbol: SourceCorpusSymbol, *, start_line: int, end_line: int) -> bool:
    line = symbol.line or start_line
    symbol_end = symbol.end_line or line
    return line <= end_line and symbol_end >= start_line


def _resolve_relative_import(*, path: str, target: str, known_paths: set[str]) -> str | None:
    raw = str(target or "").strip()
    if not raw.startswith("."):
        return None
    base = PurePosixPath(path).parent
    if raw.startswith("./"):
        relative = raw[2:]
    elif raw.startswith("../"):
        relative = raw
    else:
        leading_dots = len(raw) - len(raw.lstrip("."))
        base = _ascend(base, max(0, leading_dots - 1))
        relative = raw[leading_dots:].replace(".", "/")
    for suffix in _IMPORT_TARGET_EXTENSIONS:
        candidate = safe_scan_relpath(_join_relative(base, f"{relative}{suffix}"))
        if candidate and candidate in known_paths:
            return candidate
    return None


def _ascend(path: PurePosixPath, count: int) -> PurePosixPath:
    out = path
    for _ in range(count):
        out = out.parent
    return out


def _join_relative(base: PurePosixPath, relative: str) -> str:
    parts = list(base.parts)
    for part in PurePosixPath(relative).parts:
        if part in {"", "."}:
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    return "/".join(parts)


def _coerce_bundle(bundle: SourceCorpusBundle | dict[str, Any]) -> SourceCorpusBundle:
    if isinstance(bundle, SourceCorpusBundle):
        return bundle
    return SourceCorpusBundle.model_validate(bundle)


def _stable_id(prefix: str, value: str) -> str:
    return f"{prefix}_{hashlib.sha256(value.encode('utf-8')).hexdigest()[:16]}"


def _file_map_checksum(file_map: dict[str, str]) -> str:
    payload = {path: hashlib.sha256(content.encode("utf-8")).hexdigest() for path, content in sorted(file_map.items())}
    return f"sha256:{hashlib.sha256(repr(payload).encode('utf-8')).hexdigest()}"


def _line_count(content: str) -> int:
    if not content:
        return 0
    return len(content.splitlines())


def _keywords(query: str) -> list[str]:
    out: list[str] = []
    for token in re.findall(r"[a-zA-Z][a-zA-Z0-9_]{2,}", str(query or "").lower()):
        if token not in out:
            out.append(token)
    return out[:16]


def _matched_excerpt(text: str, matched_terms: list[str], *, max_length: int = 900) -> str:
    lower = text.lower()
    first = min((lower.find(term) for term in matched_terms if term in lower), default=0)
    start = max(0, first - 180)
    return _excerpt(text[start:], max_length=max_length)


def _excerpt(text: str | None, *, max_length: int | None) -> str | None:
    if text is None:
        return None
    if max_length is None or max_length <= 0 or len(text) <= max_length:
        return text
    return f"{text[: max(0, max_length - 20)].rstrip()}\n... [truncated]"


def _contains_any(value: str, needles: tuple[str, ...]) -> bool:
    return any(needle in value for needle in needles)


def _looks_component_file(name: str) -> bool:
    stem = name.rsplit(".", 1)[0]
    return bool(stem[:1].isupper() or "-" in stem or stem in {"index", "app"})


def _role_priority(role: str) -> int:
    order = {
        "manifest": 0,
        "entrypoint": 1,
        "route": 2,
        "page": 3,
        "component": 4,
        "api_client": 5,
        "auth": 6,
        "data_model": 7,
        "persistence": 8,
        "integration": 9,
        "module_contract": 10,
        "deployment": 30,
        "test": 60,
        "documentation": 70,
        "source": 50,
    }
    return order.get(role, 50)


def _priority_label_rank(label: str) -> int:
    order = {
        "app_modules": 0,
        "app_services": 1,
        "app_ui": 2,
        "app_config": 3,
        "workflows": 4,
        "control_plane": 5,
        "build_context_contracts": 6,
        "build_context_packs": 7,
        "build_context": 8,
        "factory_workflows": 10,
        "factory_control_plane": 11,
        "runtime": 12,
        "src": 20,
        "other_source": 50,
        "docs": 70,
        "tests": 80,
        "scripts": 90,
    }
    return order.get(label, 50)


__all__ = [
    "SOURCE_CONTEXT_BUNDLE_SCHEMA_VERSION",
    "SourceCorpusBundle",
    "SourceCorpusChunk",
    "SourceCorpusFile",
    "SourceCorpusImport",
    "SourceCorpusSymbol",
    "build_source_corpus_bundle",
    "build_source_corpus_catalog",
    "classify_source_role",
    "get_related_source_files",
    "read_source_file_from_bundle",
    "redact_source_text",
    "search_source_corpus",
    "source_corpus_file_map",
]
