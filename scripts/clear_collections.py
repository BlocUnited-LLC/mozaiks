"""MozaiksAI dev utility: clear MongoDB collections (documents only).

Designed to be called by `scripts/cleanse.ps1`.
Safety goals:
- Defaults to NO-OP unless `--yes` is provided.
- Refuses to touch non-local MongoDB hosts unless explicitly allowed.
- Deletes documents (preserves collections + indexes) by default.

This script intentionally avoids importing runtime modules to keep it safe and
usable even when the backend isn't runnable.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

from dotenv import load_dotenv
from pymongo import MongoClient
from pymongo.uri_parser import parse_uri


_ALLOWED_LOCAL_HOSTS = {
    "localhost",
    "127.0.0.1",
    "::1",
    # common Docker hostnames used in compose setups
    "mongo",
    "mongodb",
    "host.docker.internal",
}


def _repo_root() -> Path:
    # scripts/clear_collections.py -> repo root
    return Path(__file__).resolve().parents[1]


def _load_env() -> None:
    # Load .env if present so running the script directly works.
    repo_root = _repo_root()
    env_path = repo_root / ".env"
    if env_path.exists():
        load_dotenv(dotenv_path=env_path)
    else:
        load_dotenv()


def _get_mongo_uri() -> Optional[str]:
    return os.getenv("MONGO_URI") or os.getenv("MONGODB_URI") or os.getenv("MONGO_URL")


def _extract_hosts(mongo_uri: str) -> List[str]:
    parsed = parse_uri(mongo_uri)
    hosts: List[str] = []
    for host, _port in parsed.get("nodelist", []):
        hosts.append(str(host).strip().lower())
    return hosts


def _is_local_host(hostname: str) -> bool:
    if hostname in _ALLOWED_LOCAL_HOSTS:
        return True
    # allow raw IPv4 private ranges (common local-dev) as "local"
    if hostname.startswith("192.168.") or hostname.startswith("10.") or hostname.startswith("172.16."):
        return True
    return False


def _resolve_database_name(mongo_uri: str, override: Optional[str]) -> Optional[str]:
    if override:
        return override.strip()
    parsed = parse_uri(mongo_uri)
    db = parsed.get("database")
    if db:
        return str(db)
    # MozaiksAI runtime consistently uses client["MozaiksAI"], even if the URI omits a DB.
    # Default here so cleanse works with plain mongodb://host:27017
    return os.getenv("MONGO_DB_NAME") or "MozaiksAI"


def _ping(client: MongoClient) -> None:
    client.admin.command("ping")


def _iter_target_collections(db) -> Iterable[str]:
    for name in db.list_collection_names():
        # be conservative
        if name.startswith("system."):
            continue
        yield name


def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(description="Clear MongoDB documents for local dev.")
    parser.add_argument("--action", choices=["delete", "drop", "list"], default="delete")
    parser.add_argument("--database", default=None, help="Database name override (defaults to DB in URI)")
    parser.add_argument("--yes", action="store_true", help="Required confirmation flag")
    parser.add_argument(
        "--allow-drop",
        action="store_true",
        help="Allow dropping collections when --action drop is used (VERY DESTRUCTIVE).",
    )
    parser.add_argument(
        "--allow-nonlocal",
        action="store_true",
        help="Allow operating on non-local Mongo hosts (DANGEROUS).",
    )
    parser.add_argument(
        "--timeout-ms",
        type=int,
        default=2000,
        help="Server selection timeout in ms (default: 2000)",
    )

    args = parser.parse_args(argv)

    if not args.yes:
        print("Refusing to run without --yes (no changes made).")
        return 0

    _load_env()
    mongo_uri = _get_mongo_uri()
    if not mongo_uri:
        print("MONGO_URI is not set; skipping MongoDB cleanup (no changes made).")
        return 0

    hosts = _extract_hosts(mongo_uri)
    nonlocal_hosts = [h for h in hosts if not _is_local_host(h)]
    if nonlocal_hosts and not args.allow_nonlocal:
        print(
            "Refusing to clear MongoDB because the URI points to non-local host(s): "
            + ", ".join(nonlocal_hosts)
        )
        print("Set --allow-nonlocal only if you are 100% sure this is safe.")
        # Non-zero so callers (e.g., cleanse.ps1) can surface this as a warning.
        return 2

    db_name = _resolve_database_name(mongo_uri, args.database)
    if not db_name:
        print("MongoDB URI does not include a database name and --database not provided; skipping.")
        return 3

    client = MongoClient(mongo_uri, serverSelectionTimeoutMS=int(args.timeout_ms))
    try:
        _ping(client)
    except Exception as e:
        print(f"Could not connect to MongoDB ({e}); skipping cleanup.")
        return 4

    db = client[db_name]

    if args.action == "list":
        cols = list(_iter_target_collections(db))
        print(f"Database: {db_name}")
        print(f"Collections ({len(cols)}):")
        for c in cols:
            print(f"- {c}")
        return 0

    if args.action == "drop" and not args.allow_drop:
        print("Refusing to drop collections without --allow-drop (no changes made).")
        return 5

    total_deleted = 0
    total_dropped = 0

    for coll_name in _iter_target_collections(db):
        coll = db[coll_name]
        if args.action == "delete":
            try:
                res = coll.delete_many({})
                deleted = int(getattr(res, "deleted_count", 0) or 0)
                total_deleted += deleted
                print(f"{coll_name}: deleted {deleted}")
            except Exception as e:
                print(f"{coll_name}: delete failed ({e})")
        elif args.action == "drop":
            try:
                coll.drop()
                total_dropped += 1
                print(f"{coll_name}: dropped")
            except Exception as e:
                print(f"{coll_name}: drop failed ({e})")

    if args.action == "delete":
        print(f"Done. Total documents deleted: {total_deleted}")
    else:
        print(f"Done. Collections dropped: {total_dropped}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
