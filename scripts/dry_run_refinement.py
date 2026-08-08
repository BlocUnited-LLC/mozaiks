from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

# ruff: noqa: E402,I001

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from mozaiksai.control_plane.dry_run import (
    DRY_RUN_NOTICE,
    build_refinement_dry_run_plan,
    load_manifest_file,
    neutral_manifest,
)

APP_ROOT = REPO_ROOT / "factory_app" / "app"


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv  # type: ignore
    except ImportError:
        return
    load_dotenv(REPO_ROOT / ".env")


def _resolve_save_plan_path(path: str) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = (REPO_ROOT / candidate).resolve()
    else:
        candidate = candidate.resolve()
    blocked_roots = [
        (REPO_ROOT / "generated").resolve(),
        (REPO_ROOT / "generated_apps").resolve(),
    ]
    if any(candidate == root or candidate.is_relative_to(root) for root in blocked_roots):
        raise ValueError("Refusing to save a dry-run plan under generated/ or generated_apps/.")
    return candidate


def _print_human(plan: dict[str, Any]) -> None:
    print("Refinement dry run: PASS")
    print(DRY_RUN_NOTICE)
    print(f"request: {plan['request']}")
    print(f"change_class: {plan['change_class']}")
    print(f"refinement_lane: {plan.get('refinement_lane')}")
    print(f"workflow_sequence: {plan['workflow_sequence']}")
    print(f"target_workflow: {plan['target_workflow']}")
    print(f"families: {plan['affected_declarative_families']}")
    print(f"paths: {plan['affected_bundle_paths']}")
    print(f"scope: {plan['scope_summary']}")
    print(f"profiles: {plan['profiles']}")
    print(f"next_step: {plan['next_step']}")
    print("mutation_allowed: false")


async def run(args: argparse.Namespace) -> dict[str, Any]:
    if args.live_classifier:
        _load_dotenv()
    manifest = load_manifest_file(Path(args.manifest).resolve()) if args.manifest else neutral_manifest()
    plan = await build_refinement_dry_run_plan(
        request=args.request,
        build_family=args.artifact_kind,
        change_class=args.change_class,
        files_manifest=manifest,
        app_root=APP_ROOT,
        live_classifier=args.live_classifier,
    )
    return plan.model_dump(mode="json")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Preview a refinement execution plan without mutations.")
    parser.add_argument("--request", required=True, help="Natural-language refinement request.")
    parser.add_argument("--artifact-kind", default="app_bundle", help="Artifact kind to route. Default: app_bundle.")
    parser.add_argument("--manifest", help="Optional JSON manifest path. Uses a neutral fixture when omitted.")
    classifier_group = parser.add_mutually_exclusive_group()
    classifier_group.add_argument(
        "--change-class",
        choices=["patch", "design", "feature", "core"],
        help="Use an explicit offline ChangeClass.",
    )
    classifier_group.add_argument(
        "--live-classifier",
        action="store_true",
        help="Call the configured live LLM classifier before route/impact dry-run.",
    )
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON only.")
    parser.add_argument("--save-plan", help="Write the dry-run plan to an explicit JSON path.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        payload = asyncio.run(run(args))
        if args.save_plan:
            save_path = _resolve_save_plan_path(args.save_plan)
            save_path.parent.mkdir(parents=True, exist_ok=True)
            save_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
            payload["saved_plan"] = str(save_path)
        if args.json:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            _print_human(payload)
            if args.save_plan:
                print(f"saved_plan: {payload['saved_plan']}")
        return 0
    except Exception as exc:
        print(f"Refinement dry run failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
