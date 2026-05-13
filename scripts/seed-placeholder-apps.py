from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from mozaiksai.hosts.bootstrap import configure_repo_host_defaults

configure_repo_host_defaults("studio")

from factory_app.app.modules.app_registry.backend.service import AppRegistryService


PLACEHOLDER_APPS = (
    {
        "app_id": "placeholder-client-intake",
        "name": "Client Intake",
        "description": "Draft intake routing and approval capture.",
        "status": "draft",
    },
    {
        "app_id": "placeholder-support-ops",
        "name": "Support Operations",
        "description": "Support triage and escalation workflows in Build.",
        "status": "building",
    },
    {
        "app_id": "placeholder-revenue-review",
        "name": "Revenue Review",
        "description": "Finance review and audit flows in review.",
        "status": "review",
    },
    {
        "app_id": "placeholder-partner-delivery",
        "name": "Partner Delivery",
        "description": "Partner rollout and environment validation in deployment.",
        "status": "deploying",
    },
    {
        "app_id": "placeholder-member-growth",
        "name": "Member Growth",
        "description": "Live growth and campaign orchestration.",
        "status": "active",
    },
    {
        "app_id": "placeholder-campaign-revision",
        "name": "Campaign Revision",
        "description": "Release revision blocked on stakeholder feedback.",
        "status": "needs_revision",
    },
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Seed placeholder app registry records for local Console review.",
    )
    parser.add_argument(
        "--owner-user-id",
        default=os.getenv("MOZAIKS_DEFAULT_USER_ID", "demo-user").strip() or "demo-user",
        help="Owner user id used by the Console in local auth-disabled mode (default: %(default)s).",
    )
    parser.add_argument(
        "--preset",
        choices=("portfolio", "single"),
        default="portfolio",
        help="Seed either the full lifecycle portfolio or a single draft app.",
    )
    parser.add_argument(
        "--name",
        default="New App",
        help="Name for the single-app preset.",
    )
    parser.add_argument(
        "--description",
        default="Draft app record for local Console review.",
        help="Description for the single-app preset.",
    )
    parser.add_argument(
        "--app-id",
        default="placeholder-app",
        help="App id for the single-app preset.",
    )
    return parser.parse_args()


def _records_from_args(args: argparse.Namespace) -> Iterable[dict]:
    if args.preset == "single":
        return (
            {
                "app_id": args.app_id,
                "name": args.name,
                "description": args.description,
                "status": "draft",
            },
        )
    return PLACEHOLDER_APPS


async def _seed_records(args: argparse.Namespace) -> list[dict]:
    service = AppRegistryService()
    seeded: list[dict] = []
    for record in _records_from_args(args):
        result = await service.create_app_record(
            owner_user_id=args.owner_user_id,
            name=record["name"],
            description=record["description"],
            status=record["status"],
            app_id=record["app_id"],
        )
        app = result.get("app")
        if isinstance(app, dict):
            seeded.append(app)
    return seeded


async def _main() -> int:
    args = _parse_args()
    seeded = await _seed_records(args)
    if not seeded:
        print("No placeholder app records were written.")
        return 1

    print(f"Seeded {len(seeded)} placeholder app record(s) for owner_user_id={args.owner_user_id}")
    for app in seeded:
        print(f"- {app.get('app_id')} [{app.get('lifecycle_state')}] {app.get('name')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
