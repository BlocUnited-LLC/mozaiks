"""Refresh Common Ground's declared derivative of the social posts module.

Run from the OSS checkout with --check to detect drift without writing files.
The standalone app never imports this developer-only script or Factory code.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

REFERENCE_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
MODULE_FILES = (
    "modules/user_posts/module.yaml",
    "modules/user_posts/contracts/events.yaml",
    "modules/user_posts/backend/__init__.py",
    "modules/user_posts/backend/base_handler.py",
    "modules/user_posts/backend/handler.py",
    "modules/user_posts/backend/service.py",
    "modules/user_posts/backend/repo.py",
    "modules/user_posts/backend/policy.py",
    "modules/user_posts/backend/schemas.py",
    "modules/user_posts/backend/account_data_handler.py",
)
MIGRATION_PATH = "data/migrations/001_community_collections.json"


def render_reference_files(
    *, repository_root: Path = REPOSITORY_ROOT, reference_root: Path = REFERENCE_ROOT,
) -> dict[str, str]:
    """Return only the explicitly owned derivative files, relative to app/."""
    from factory_app.workflows.AppGenerator.tools.module_api_template import get_module_api_template
    from factory_app.workflows.AppGenerator.tools.resolve_managed_capability_templates import (
        resolve_managed_capability_templates,
    )

    rendered = {
        item["filename"]: item["content"]
        for item in resolve_managed_capability_templates([{
            "id": "social",
            "capability_source": "generated_module",
            "pack_source_path": str(repository_root / "factory_app/build_context/social"),
        }])
    }
    # This is a posts-only derivative, not an installation of the complete pack.
    # In particular, do not copy its whole-pack provenance or optional companions.
    files = {name: rendered[name] for name in MODULE_FILES}
    files["ui/lib/moduleApi.js"] = get_module_api_template()
    manifest = yaml.safe_load(files["modules/user_posts/module.yaml"])
    for action in manifest["actions"]:
        if action["id"] in {"create_post", "list_posts"}:
            action["input_schema"]["properties"]["visibility"]["enum"] = ["public"]
    files["modules/user_posts/module.yaml"] = yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True)

    contract = json.loads((reference_root / "app/data/contract.json").read_text(encoding="utf-8"))
    collections = next(
        surface["collections"] for surface in contract["surfaces"]
        if surface["surface_id"] == "user_posts"
    )
    operations = []
    for collection in collections:
        target = {"module_id": "user_posts", "entity_name": collection["name"]}
        operations.append({"type": "ensure_collection", **target})
        operations.extend(
            {"type": "ensure_index", **target, "index": index}
            for index in collection["indexes"]
        )
    files[MIGRATION_PATH] = json.dumps({
        "migration_id": "001_community_collections",
        "version": "1",
        "operations": operations,
    }, indent=2) + "\n"
    return files


def refresh_reference(*, check: bool, reference_root: Path = REFERENCE_ROOT) -> list[str]:
    """Return changed paths; check mode never creates, changes, or deletes files."""
    changed = []
    for name, content in render_reference_files(reference_root=reference_root).items():
        path = reference_root / "app" / name
        if path.is_file() and path.read_text(encoding="utf-8") == content:
            continue
        changed.append(name)
        if not check:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8", newline="\n")
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Report drift without changing files.")
    args = parser.parse_args()
    sys.path.insert(0, str(REPOSITORY_ROOT))
    changed = refresh_reference(check=args.check)
    for name in changed:
        print(f"{'Drift' if args.check else 'Updated'}: app/{name}")
    return 1 if args.check and changed else 0


if __name__ == "__main__":
    raise SystemExit(main())
