"""Seed (or purge) App Zero: a realistic local dataset for evaluating Studio.

App Zero exists so Studio surfaces can be judged against data that looks like a
real workspace, and so the *empty* first-run experience can be judged against
its absence. Both states matter: the populated one shows whether a page is
readable when full, the empty one is what a new install actually ships with.

    python scripts/seed_app_zero.py            # populate
    python scripts/seed_app_zero.py --purge    # remove ONLY seeded rows
    python scripts/seed_app_zero.py --status   # report what is seeded

Every document written here carries ``seed_tag: "app-zero"``. Purge deletes by
that tag alone, so it can never remove data this script did not create.

This is a local development script. ``scripts/`` is not in MANIFEST.in, so it
is not part of the published wheel — there is no placeholder content shipping
to users. Metric rows are written through the real ``AppMetrics`` API rather
than hand-built documents, so seeded data cannot drift from the shape the
analytics readers expect.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

_ENV = Path(__file__).resolve().parents[1] / ".env"
if _ENV.exists():
    from dotenv import load_dotenv

    load_dotenv(_ENV)

MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
# App registry rows live in the runtime database; metric events live in the
# separate app database. Both names are resolved the way the runtime resolves
# them, so a seeded row always lands where the reader will look for it.
RUNTIME_DB = "mozaiksai"
SEED_TAG = "app-zero"
OWNER = os.environ.get("MOZAIKS_DEFAULT_USER_ID", "demo-user")

# One app carries the full monetized story; the others give the portfolio
# views something to rank and compare against.
APPS = [
    {
        "app_id": "app-zero-flagship",
        "name": "Northwind Bookings",
        "description": "Appointment booking with subscription tiers, reminders, and a customer portal.",
        "lifecycle_state": "active",
        "mrr": 4820.0,
        "prev_mrr": 4390.0,
        "paying": 96,
        "active_users": 412,
        "page_views": 180,
        "actions": 90,
    },
    {
        "app_id": "app-zero-growth",
        "name": "Trailhead Community",
        "description": "Member community with events, discussions, and paid supporter tiers.",
        "lifecycle_state": "active",
        "mrr": 1180.0,
        "prev_mrr": 1265.0,
        "paying": 41,
        "active_users": 233,
        "page_views": 120,
        "actions": 48,
    },
    {
        "app_id": "app-zero-early",
        "name": "Fieldwork Notes",
        "description": "Offline-first field data capture for survey teams. Pre-revenue.",
        "lifecycle_state": "building",
        "mrr": 0.0,
        "prev_mrr": 0.0,
        "paying": 0,
        "active_users": 18,
        "page_views": 24,
        "actions": 6,
    },
]

PAGES = ["/", "/bookings", "/customers", "/settings", "/billing"]
ACTIONS = ["booking.create", "customer.update", "reminder.send", "plan.upgrade"]


def _app_db_name() -> str:
    """The app database the runtime resolves for metric events."""
    from mozaiksai.core.runtime.persistence.mongo import _default_database_name

    return _default_database_name()


def _client():
    from motor.motor_asyncio import AsyncIOMotorClient

    return AsyncIOMotorClient(MONGO_URI, serverSelectionTimeoutMS=5000)


def _metrics_for(app_id: str):
    """Real AppMetrics bound to one app, so writes use the production path."""
    from mozaiksai.core.metrics.app_metrics import AppMetrics
    from mozaiksai.core.runtime.persistence.mongo import MongoPersistenceContext

    # No database_name: let it resolve exactly as the analytics reader does
    # (AppMetrics(ctx) with no persistence). Forcing a name here is what made
    # the first seed invisible to the reader.
    persistence = MongoPersistenceContext(app_id=app_id)
    ctx = type(
        "SeedCtx",
        (),
        {
            "app_id": app_id,
            "user_id": OWNER,
            "tenant_id": "",
            "workspace_id": "",
            "correlation_id": "",
            "module_id": "app_zero_seed",
            "action_id": "seed",
            "persistence": persistence,
        },
    )()
    return AppMetrics(ctx, persistence=persistence), ctx


async def _seed_app_records(db) -> int:
    coll = db["AppRegistryRecords"]
    written = 0
    for spec in APPS:
        if await coll.find_one({"app_id": spec["app_id"]}):
            print(f"  skip  {spec['name']} (already present)")
            continue
        now = datetime.now(UTC)
        await coll.insert_one(
            {
                "_id": f"appreg_{uuid4().hex}",
                "app_id": spec["app_id"],
                "owner_user_id": OWNER,
                "name": spec["name"],
                "description": spec["description"],
                "lifecycle_state": spec["lifecycle_state"],
                "bundle_path": None,
                "created_at": now - timedelta(days=45),
                "updated_at": now,
                "last_status_changed_at": now,
                "name_status": "named",
                "name_source": "seed",
                "seed_tag": SEED_TAG,
            }
        )
        print(f"  app   {spec['name']}  ({spec['app_id']})")
        written += 1
    return written


async def _seed_metrics(spec: dict) -> int:
    """Revenue KPIs plus the usage events that make audience metrics real.

    kpi.* has no writer in OSS by design — the reader is an extension point a
    billing integration fills. Seeding it here is what lets the revenue
    surfaces be evaluated at all.
    """
    metrics, ctx = _metrics_for(spec["app_id"])
    now = datetime.now(UTC)
    written = 0

    # Two snapshots per KPI so period-over-period movement has something to
    # compare against; the reader takes the latest inside each window.
    for offset, mrr in ((45, spec["prev_mrr"]), (1, spec["mrr"])):
        occurred = now - timedelta(days=offset)
        for name, value, unit in (
            # Event names are kpi.<metric_id> from the metric registry —
            # "paying_users", not "paying_customers", or the reader finds nothing.
            ("kpi.mrr", mrr, "usd"),
            ("kpi.paying_users", spec["paying"] if offset == 1 else max(0, spec["paying"] - 7), "count"),
        ):
            await metrics.record_snapshot(
                name,
                value=value,
                unit=unit,
                occurred_at=occurred,
                metadata={"seed_tag": SEED_TAG},
            )
            written += 1

    rng = random.Random(spec["app_id"])
    visitors = [f"visitor-{i}" for i in range(1, (spec["active_users"] or 1) + 1)]
    for _ in range(spec["page_views"]):
        ctx.user_id = rng.choice(visitors)  # drives actor_id; distinct actors = active users
        await metrics.track(
            "app.page_view",
            occurred_at=now - timedelta(hours=rng.randint(1, 24 * 27)),
            dimensions={"path": rng.choice(PAGES)},
            attribution_id=ctx.user_id,
            metadata={"seed_tag": SEED_TAG},
        )
        written += 1
    for _ in range(spec["actions"]):
        ctx.user_id = rng.choice(visitors)
        await metrics.track(
            "app.action_invoked",
            occurred_at=now - timedelta(hours=rng.randint(1, 24 * 27)),
            dimensions={"action": rng.choice(ACTIONS)},
            attribution_id=ctx.user_id,
            metadata={"seed_tag": SEED_TAG},
        )
        written += 1
    return written


async def seed() -> None:
    client = _client()
    db = client[RUNTIME_DB]
    print(f"Seeding App Zero into {RUNTIME_DB}+{_app_db_name()} (owner={OWNER})\n")
    apps = await _seed_app_records(db)
    total = 0
    for spec in APPS:
        n = await _seed_metrics(spec)
        total += n
        print(f"  data  {spec['name']}: {n} metric rows")
    print(f"\nDone. {apps} app records, {total} metric rows, all tagged seed_tag={SEED_TAG!r}.")
    print("Purge with:  python scripts/seed_app_zero.py --purge")
    client.close()


async def _tagged_collections(client):
    """Every collection holding rows this script created, across both databases."""
    for db_name in dict.fromkeys([RUNTIME_DB, _app_db_name()]):
        db = client[db_name]
        for name in await db.list_collection_names():
            coll = db[name]
            if await coll.find_one({"seed_tag": SEED_TAG}) or await coll.find_one(
                {"metadata.seed_tag": SEED_TAG}
            ):
                yield f"{db_name}.{name}", coll


async def purge() -> None:
    client = _client()
    print(f"Purging rows tagged seed_tag={SEED_TAG!r} from {RUNTIME_DB}+{_app_db_name()}\n")
    removed = 0
    async for name, coll in _tagged_collections(client):
        res = await coll.delete_many(
            {"$or": [{"seed_tag": SEED_TAG}, {"metadata.seed_tag": SEED_TAG}]}
        )
        if res.deleted_count:
            print(f"  {name}: {res.deleted_count} removed")
            removed += res.deleted_count
    print(f"\nDone. {removed} rows removed. Nothing untagged was touched.")
    client.close()


async def status() -> None:
    client = _client()
    print(f"App Zero status in {RUNTIME_DB}+{_app_db_name()}\n")
    total = 0
    async for name, coll in _tagged_collections(client):
        n = await coll.count_documents(
            {"$or": [{"seed_tag": SEED_TAG}, {"metadata.seed_tag": SEED_TAG}]}
        )
        print(f"  {name}: {n}")
        total += n
    print(f"\n{total} seeded rows." if total else "\nNothing seeded — this is the fresh-install state.")
    client.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed or purge the App Zero local dataset.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--purge", action="store_true", help="remove only rows this script created")
    group.add_argument("--status", action="store_true", help="report seeded row counts")
    args = parser.parse_args()
    if args.purge:
        asyncio.run(purge())
    elif args.status:
        asyncio.run(status())
    else:
        asyncio.run(seed())


if __name__ == "__main__":
    main()
