"""Report generated bundles from where the runtime actually writes them.

Generated code lands in `ChatSessions.generated_files` in Mongo, not on disk. A
harness watching `generated/apps` reports a successful build as NO BUNDLE --
that directory stays empty through a complete run.

Emits JSON on stdout so the spec can attribute bundles to its own chat ids:

    {"bundles": 3, "detail": [{"chat_id": ..., "workflow": ..., "file_count": 7}]}

A total is not evidence. Other agents build against the same Mongo, and their
bundle raises this count while your run is still in its first workflow.
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


db_name = os.environ.get("MOZAIKS_TRAVERSAL_DB", "mozaiksai")
db = pymongo.MongoClient(_mongo_uri(), serverSelectionTimeoutMS=8000)[db_name]

out = []
for doc in db.ChatSessions.find({"generated_files": {"$exists": True, "$ne": None}}):
    files = doc.get("generated_files") or {}
    out.append(
        {
            "chat_id": str(doc.get("_id")),
            "workflow": doc.get("workflow_name"),
            "file_count": len(files) if hasattr(files, "__len__") else 0,
        }
    )
json.dump({"bundles": len(out), "detail": out}, sys.stdout)
