"""Seed a registered build whose app bundle plans images, for a live AppMediaGenerator run.

Creates, through the same artifact store and registry records a Factory build
writes, a draft app bundle version whose ``config/asset_manifest.json`` plans
two AI-made images and one owner-supplied photo, plus the completed AppGenerator
session that binds it. Launch AppMediaGenerator against the printed
``build_registry_id``; it should publish a child bundle version with the images.

Usage:
    python scripts/seed_app_media_smoke.py --stage-dir <dir> [--host-app-id mozaiks-factory] [--user-id anonymous]
    python scripts/seed_app_media_smoke.py --verify <build_registry_id>

Requires MONGO_URI (defaults to mongodb://localhost:27017). Writes only new records.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import io
import json
import os
import sys
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

BUNDLE_NAME = "BrewlineCoffee"


def _bundle_files(target_app_id: str) -> dict[str, str]:
    manifest = {
        "version": "1.0",
        "assets": [
            {
                "asset_id": "home-hero", "kind": "hero_image", "source": "generated", "aspect": "landscape",
                "brief": "A warm neighborhood coffee shop counter in soft morning light with a barista pouring a latte.",
                "alt": "Barista pouring a latte at a sunny coffee bar", "usage": ["home"], "path": None, "url": None,
            },
            {
                "asset_id": "menu-illustration", "kind": "illustration", "source": "generated", "aspect": "square",
                "brief": "A friendly flat illustration of seasonal drinks: a pumpkin latte, an iced cold brew, and a croissant.",
                "alt": "Illustrated seasonal drinks and a croissant", "usage": ["menu"], "path": None, "url": None,
            },
            {
                "asset_id": "signature-latte-photo", "kind": "product_photo", "source": "uploaded", "aspect": "square",
                "brief": "Photo of the shop's real signature oat-milk honey latte.",
                "alt": "Signature oat-milk honey latte", "usage": ["menu"], "path": None, "url": None,
            },
        ],
    }
    page = "\n".join([
        "schema_version: mozaiks.app_page.v1",
        "name: home",
        "route: /home",
        "title: Brewline Coffee",
        "page_type: landing",
        "layout: full-width",
        "sections:",
        "  - id: home-hero-image",
        "    primitive: Image",
        "    config: { asset_id: home-hero, alt: Barista pouring a latte at a sunny coffee bar, variant: banner }",
        "  - id: menu-art",
        "    primitive: Image",
        "    config: { asset_id: menu-illustration, alt: Illustrated seasonal drinks and a croissant, caption: This season's menu }",
        "  - id: signature-photo",
        "    primitive: Image",
        "    config: { asset_id: signature-latte-photo, alt: Signature oat-milk honey latte }",
        "",
    ])
    return {
        "app.json": json.dumps({
            "appId": target_app_id,
            "name": "Brewline Coffee",
            "description": "A neighborhood coffee shop app for ordering ahead and browsing the seasonal menu.",
        }, indent=2) + "\n",
        "brand/theme_config.json": json.dumps({
            "colors": {"primary": "#7c4a2d", "accent": "#e8b04b", "background": "#fbf6ef"},
            "fonts": {"heading": "Fraunces", "body": "Inter"},
        }, indent=2) + "\n",
        "config/asset_manifest.json": json.dumps(manifest, indent=2) + "\n",
        "ui/pages/home.yaml": page,
    }


async def _seed(stage_dir: Path, host_app_id: str, user_id: str) -> dict[str, str]:
    from pymongo import MongoClient

    from mozaiksai.core.artifacts import (
        ArtifactLifecycleStatus,
        ArtifactValidationStatus,
        get_artifact_store,
    )
    from mozaiksai.core.artifacts.models import canonical_bundle_archive_path

    token = uuid.uuid4().hex[:8]
    target_app_id = f"media-smoke-{token}"
    build_registry_id = f"appreg_{uuid.uuid4().hex}"
    build_id = f"build_{uuid.uuid4().hex}"
    source_chat_id = f"media-smoke-appgen-{token}"
    binding = {
        "build_registry_id": build_registry_id, "target_app_id": target_app_id,
        "build_id": build_id, "phase": "genesis",
    }

    app_dir = stage_dir / "apps" / target_app_id / build_id / "app"
    files = _bundle_files(target_app_id)
    for path, content in files.items():
        target = app_dir / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8", newline="")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(files):
            archive.writestr(f"{BUNDLE_NAME}/{path}", files[path])
    raw = buffer.getvalue()
    zip_path = app_dir.parent / f"{BUNDLE_NAME}.zip"
    zip_path.write_bytes(raw)

    record = await get_artifact_store().create_build_record(
        app_id=target_app_id,
        build_family="app_bundle",
        build_key="app_bundle",
        files_manifest=[{
            "path": canonical_bundle_archive_path(BUNDLE_NAME),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "size_bytes": len(raw),
            "content_type": "application/zip",
        }],
        source_workflow="AppGenerator",
        source_chat_id=source_chat_id,
        lifecycle_status=ArtifactLifecycleStatus.DRAFT,
        validation_status=ArtifactValidationStatus.PASSED,
        app_validation_status="passed",
        commit_metadata={
            "message": f"AppGenerator: {BUNDLE_NAME}",
            "author_user_id": user_id,
            "source_workflow": "AppGenerator",
            "source_chat_id": source_chat_id,
            "metadata": {
                "bundle_name": BUNDLE_NAME,
                "artifact_path": str(zip_path.resolve()),
                "workspace_dir": str(app_dir.resolve()),
                "capability_packs": [],
                **binding,
            },
        },
    )

    now = datetime.now(UTC)
    db = MongoClient(os.getenv("MONGO_URI") or "mongodb://localhost:27017")["mozaiksai"]
    db["ChatSessions"].insert_one({
        "_id": source_chat_id, "chat_id": source_chat_id, "app_id": host_app_id, "user_id": user_id,
        "workflow_name": "AppGenerator", "status": 1, "run_build_binding": binding,
        "created_at": now, "last_updated_at": now,
    })
    run = {
        "build_id": build_id, "phase": "genesis", "workflow_sequence": "build", "status": "review",
        "active_chat_id": source_chat_id, "active_workflow_id": "AppGenerator",
        "artifact_version_id": record.id, "bundle_path": str(app_dir.resolve()),
        "started_at": now, "updated_at": now, "completed_at": now,
    }
    db["AppRegistryRecords"].insert_one({
        "_id": build_registry_id, "owner_user_id": user_id, "app_id": target_app_id,
        "chat_app_id": host_app_id, "name": "Brewline Coffee", "lifecycle_state": "review",
        "bundle_path": str(app_dir.resolve()), "active_chat_id": source_chat_id,
        "active_workflow_id": "AppGenerator", "current_build_run": run, "build_runs": [run],
        "created_at": now, "updated_at": now, "last_status_changed_at": now,
    })
    return {
        "build_registry_id": build_registry_id, "target_app_id": target_app_id, "build_id": build_id,
        "artifact_version_id": record.id, "source_chat_id": source_chat_id, "bundle_path": str(app_dir.resolve()),
    }


async def _verify(build_registry_id: str) -> dict[str, object]:
    """Report the build's current bundle version: lineage, media files, and asset manifest."""
    from pymongo import MongoClient

    from factory_app.workflows._shared.artifact_bundle import read_artifact_bundle
    from mozaiksai.core.artifacts import get_artifact_store

    db = MongoClient(os.getenv("MONGO_URI") or "mongodb://localhost:27017")["mozaiksai"]
    record = db["AppRegistryRecords"].find_one({"_id": build_registry_id}) or {}
    run = record.get("current_build_run") or {}
    artifact = await get_artifact_store().get_build_record(
        app_id=record.get("app_id"), build_record_id=run.get("artifact_version_id"),
    )
    if artifact is None:
        return {"error": "current artifact not found", "current_build_run": str(run)}
    files, diagnostics = await read_artifact_bundle(artifact, include_binary=True)
    manifest = json.loads(files.get("config/asset_manifest.json") or "{}")
    return {
        "artifact_version_id": artifact.id,
        "parent_build_record_id": artifact.parent_build_record_id,
        "source_workflow": artifact.source_workflow,
        "lifecycle_status": str(artifact.lifecycle_status),
        "validation_status": str(artifact.validation_status),
        "active_workflow_id": run.get("active_workflow_id"),
        "media_files": {
            path: len(content) for path, content in sorted(files.items()) if path.startswith("ui/assets/")
        },
        "blocking_diagnostics": [item for item in diagnostics if item.get("blocking")],
        "assets": [
            {key: entry.get(key) for key in ("asset_id", "source", "path", "tags")}
            for entry in manifest.get("assets", [])
        ],
        "media_overlay": artifact.commit_metadata.metadata.get("media_overlay"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--stage-dir", type=Path)
    parser.add_argument("--host-app-id", default="mozaiks-factory")
    parser.add_argument("--user-id", default="anonymous")
    parser.add_argument("--verify", metavar="BUILD_REGISTRY_ID", help="Report the build's current bundle instead of seeding.")
    args = parser.parse_args()
    if args.verify:
        print(json.dumps(asyncio.run(_verify(args.verify)), default=str))
        return
    if args.stage_dir is None:
        parser.error("--stage-dir is required when seeding")
    print(json.dumps(asyncio.run(_seed(args.stage_dir.resolve(), args.host_app_id, args.user_id))))


if __name__ == "__main__":
    main()
