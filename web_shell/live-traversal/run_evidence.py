"""Report what a traversal actually produced, from where the runtime writes it.

Generated code lands in `ChatSessions.generated_files`; the artifact the build
accepts lands in `ArtifactVersions`. Neither is on disk -- a harness watching
`generated/apps` reports a successful build as NO BUNDLE, because that
directory stays empty through a complete run.

Emits JSON on stdout:

    {
      "bundles":   [{"chat_id", "workflow", "file_count"}],
      "artifacts": [{"artifact_version_id", "app_id", "source_chat_id",
                     "lifecycle_status", "app_validation_status",
                     "app_validation_strategy", "build_family", "build_key",
                     "file_count", "created_at"}]
    }

Totals are not evidence. Other agents build against the same database, and
their bundle raises any global count while your run is still in its first
workflow -- so the caller attributes rows to the chat ids it actually drove.
"""

import json
import os
import sys
from pathlib import Path

import pymongo


def _mongo_uri() -> str:
    """Prefer the environment; fall back to the repo .env this file ships in."""
    for key in ("MOZAIKS_TRAVERSAL_MONGO_URI", "MONGO_URI"):
        value = os.environ.get(key)
        if value:
            return value.strip()
    # live-traversal/ -> web_shell/ -> repo root
    env_path = Path(__file__).resolve().parents[2] / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.startswith("MONGO_URI="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("set MONGO_URI (or MOZAIKS_TRAVERSAL_MONGO_URI), or add it to the repo .env")


def _iso(value: object) -> str | None:
    return None if value is None else str(value)


db = pymongo.MongoClient(_mongo_uri(), serverSelectionTimeoutMS=8000)[
    os.environ.get("MOZAIKS_TRAVERSAL_DB", "mozaiksai")
]

bundles = []
for doc in db.ChatSessions.find({"generated_files": {"$exists": True, "$ne": None}}):
    files = doc.get("generated_files") or {}
    bundles.append(
        {
            "chat_id": str(doc.get("_id")),
            "workflow": doc.get("workflow_name"),
            "file_count": len(files) if hasattr(files, "__len__") else 0,
        }
    )

artifacts = []
for doc in db.ArtifactVersions.find({"source_chat_id": {"$ne": None}}):
    manifest = doc.get("files_manifest") or []
    artifacts.append(
        {
            "artifact_version_id": str(doc.get("_id")),
            "app_id": doc.get("app_id"),
            "source_chat_id": str(doc.get("source_chat_id")),
            "lifecycle_status": doc.get("lifecycle_status"),
            # Populated by the runtime when an app validation actually runs.
            # It is routinely absent, so the caller must treat "missing" as
            # "unvalidated" rather than as a pass.
            "app_validation_status": doc.get("app_validation_status"),
            "app_validation_strategy": doc.get("app_validation_strategy"),
            "build_family": doc.get("build_family"),
            "build_key": doc.get("build_key"),
            "file_count": len(manifest) if hasattr(manifest, "__len__") else 0,
            "created_at": _iso(doc.get("created_at")),
        }
    )

json.dump({"bundles": bundles, "artifacts": artifacts}, sys.stdout)
