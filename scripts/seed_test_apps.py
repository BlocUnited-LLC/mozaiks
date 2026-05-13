"""
Seed script: insert test app registry records for local development.

Run from the repo root:
    python scripts/seed_test_apps.py

Uses MONGO_URI from .env (or the environment). Does not import mozaiksai to
avoid circular-import issues — connects directly via motor/pymongo.
"""
from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

# Load .env from repo root
_ENV = Path(__file__).resolve().parents[1] / ".env"
if _ENV.exists():
    from dotenv import load_dotenv
    load_dotenv(_ENV)

MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
DB_NAME = "mozaiksai"
COLLECTION = "AppRegistryRecords"

SEED_APPS = [
    {
        "owner_user_id": "demo-user",  # matches MOZAIKS_DEFAULT_USER_ID (default: "demo-user") in no-auth dev mode
        "app_id": "app-support-ops-001",
        "name": "Support Operations Hub",
        "description": "Internal support ticketing and escalation app with agent-assisted triage.",
        "lifecycle_state": "building",
    },
    {
        "owner_user_id": "demo-user",  # matches MOZAIKS_DEFAULT_USER_ID (default: "demo-user") in no-auth dev mode
        "app_id": "app-investor-mkt-002",
        "name": "Investor Marketplace",
        "description": "Marketplace for connecting startups with angel investors. Includes deal flow, messaging, and document sharing.",
        "lifecycle_state": "review",
    },
    {
        "owner_user_id": "demo-user",  # matches MOZAIKS_DEFAULT_USER_ID (default: "demo-user") in no-auth dev mode
        "app_id": "app-community-hub-003",
        "name": "Community Events Hub",
        "description": "Event discovery and RSVP platform for local community groups.",
        "lifecycle_state": "active",
    },
    {
        "owner_user_id": "demo-user",  # matches MOZAIKS_DEFAULT_USER_ID (default: "demo-user") in no-auth dev mode
        "app_id": "app-fleet-mgmt-004",
        "name": "Fleet Management Portal",
        "description": "Vehicle tracking, maintenance scheduling, and driver assignment for a small fleet operation.",
        "lifecycle_state": "configuring",
    },
]


async def main() -> None:
    try:
        from motor.motor_asyncio import AsyncIOMotorClient
    except ImportError:
        import pymongo
        print("motor not available; falling back to pymongo (sync)")
        client = pymongo.MongoClient(MONGO_URI)
        coll = client[DB_NAME][COLLECTION]
        _seed_sync(coll)
        return

    client = AsyncIOMotorClient(MONGO_URI)
    coll = client[DB_NAME][COLLECTION]
    await _seed(coll)
    client.close()


def _make_doc(spec: dict) -> dict:
    now = datetime.now(UTC)
    build_registry_id = f"appreg_{uuid4().hex}"
    return {
        "_id": build_registry_id,
        "app_id": spec["app_id"],
        "owner_user_id": spec["owner_user_id"],
        "name": spec["name"],
        "description": spec.get("description"),
        "lifecycle_state": spec["lifecycle_state"],
        "bundle_path": None,
        "created_at": now,
        "updated_at": now,
        "last_status_changed_at": now,
    }


async def _seed(coll) -> None:
    print(f"Seeding {len(SEED_APPS)} test apps into {DB_NAME}.{COLLECTION}...\n")
    for spec in SEED_APPS:
        existing = await coll.find_one({"app_id": spec["app_id"]})
        if existing:
            print(f"  SKIP {spec['name']} already exists (app_id={spec['app_id']})\n")
            continue
        doc = _make_doc(spec)
        await coll.insert_one(doc)
        print(f"  OK {spec['name']}")
        print(f"     app_id={doc['app_id']}  state={doc['lifecycle_state']}")
        print(f"     build_registry_id={doc['_id']}\n")
    print("Done.")


def _seed_sync(coll) -> None:
    print(f"Seeding {len(SEED_APPS)} test apps into {DB_NAME}.{COLLECTION}...\n")
    for spec in SEED_APPS:
        existing = coll.find_one({"app_id": spec["app_id"]})
        if existing:
            print(f"  SKIP {spec['name']} already exists (app_id={spec['app_id']})\n")
            continue
        doc = _make_doc(spec)
        coll.insert_one(doc)
        print(f"  OK {spec['name']}")
        print(f"     app_id={doc['app_id']}  state={doc['lifecycle_state']}")
        print(f"     build_registry_id={doc['_id']}\n")
    print("Done.")


if __name__ == "__main__":
    asyncio.run(main())
