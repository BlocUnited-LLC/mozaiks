from __future__ import annotations

import re
from collections import Counter
from typing import Any

from mozaiksai.control_plane.contracts import ControlPlaneToolContext
from mozaiksai.core.artifacts.store import BuildRecordStore, get_build_record_store

from ._bundle_workspace import load_bundle_workspace, safe_relpath
from ._shared import list_head, normalize_context, text_excerpt

_MAX_FILE_COUNT = 160
_MAX_MATCH_COUNT = 12
_MAX_DIR_COUNT = 12
_STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "that",
    "this",
    "from",
    "into",
    "your",
    "when",
    "then",
    "just",
    "have",
    "will",
    "want",
    "needs",
    "need",
    "make",
    "change",
    "update",
    "add",
    "fix",
    "file",
    "files",
    "page",
    "component",
}


async def get_bundle_workspace_catalog(
    *,
    context: ControlPlaneToolContext | dict[str, Any] | None = None,
    record_store: BuildRecordStore | None = None,
) -> dict[str, Any]:
    tool_context = normalize_context(context)
    app_id = str(tool_context.app_id or "").strip()
    build_record_id = str(tool_context.artifact_version_id or "").strip()
    if not app_id or not build_record_id:
        return {"present": False, "reason": "missing_app_id_or_artifact_version"}

    store = record_store or get_build_record_store()
    workspace = await load_bundle_workspace(
        record_store=store,
        app_id=app_id,
        build_record_id=build_record_id,
    )
    if not workspace.get("present"):
        return workspace

    artifact = workspace["artifact"]
    file_map = workspace["file_map"]
    file_tree = sorted(file_map.keys())
    keywords = _keywords(tool_context.raw_user_request)
    matches = _rank_matches(file_map=file_map, keywords=keywords)
    selected_paths = _normalize_selected_paths(tool_context.extra.get("selected_file_paths"))

    return {
        "present": True,
        "build_record_id": artifact.id,
        "build_family": artifact.build_family,
        "build_key": artifact.build_key,
        "source": workspace["source"],
        "file_count": len(file_tree),
        "file_tree": list_head(file_tree, limit=_MAX_FILE_COUNT),
        "directory_summary": _directory_summary(file_tree),
        "request_keywords": keywords,
        "selected_file_paths": selected_paths,
        "matches": matches,
    }


def _normalize_selected_paths(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    selected: list[str] = []
    for item in value:
        safe = safe_relpath(item)
        if safe and safe not in selected:
            selected.append(safe)
    return selected


def _keywords(raw_request: str) -> list[str]:
    tokens = re.findall(r"[a-zA-Z][a-zA-Z0-9_]{2,}", str(raw_request or "").lower())
    keywords: list[str] = []
    for token in tokens:
        if token in _STOPWORDS:
            continue
        if token not in keywords:
            keywords.append(token)
    return keywords[:12]


def _rank_matches(*, file_map: dict[str, str], keywords: list[str]) -> list[dict[str, Any]]:
    if not keywords:
        return []

    ranked: list[tuple[int, dict[str, Any]]] = []
    for path, content in file_map.items():
        path_lower = path.lower()
        content_lower = content.lower()
        path_hits = [token for token in keywords if token in path_lower]
        content_hits = [token for token in keywords if token in content_lower]
        score = len(path_hits) * 5 + len(content_hits)
        if score <= 0:
            continue
        ranked.append(
            (
                score,
                {
                    "path": path,
                    "score": score,
                    "path_hits": path_hits,
                    "content_hits": content_hits,
                    "excerpt": text_excerpt(content, max_length=300),
                },
            )
        )

    ranked.sort(key=lambda item: (-item[0], item[1]["path"]))
    return [entry for _, entry in ranked[:_MAX_MATCH_COUNT]]


def _directory_summary(file_tree: list[str]) -> list[dict[str, Any]]:
    counter: Counter[str] = Counter()
    for path in file_tree:
        parts = str(path).split("/")
        directory = "/".join(parts[:2]) if len(parts) >= 2 else (parts[0] if parts else "")
        if directory:
            counter[directory] += 1
    rows = [
        {"directory": directory, "file_count": count}
        for directory, count in counter.most_common(_MAX_DIR_COUNT)
    ]
    return rows
