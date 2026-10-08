"""Dry-run connector vault inventory. Never reads or writes credential values."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from mozaiksai.core.data.persistence.connector_store import (  # noqa: E402
    normalize_connector_service,
)
from mozaiksai.core.data.persistence.namespaces import (  # noqa: E402
    SYSTEM_DATABASE,
    PlatformCollections,
)
from mozaiksai.core.secrets.connector_vault import _secret_name, _slug  # noqa: E402

Provider = Literal["mongo", "azure_key_vault"]
MAX_RECORDS = 10_000
CONNECTOR_FIELDS = (
    "scope", "scope_id", "service", "status", "secret_available", "secret_storage",
    "secret_name", "created_at", "updated_at", "last_submitted_at",
)
SECRET_FIELDS = (
    "scope", "scope_id", "service", "managed_by", "connector_prefix", "secret_name", "stored_at", "expires_at",
    "version_count", "version_fingerprint", "version_identity_conflict",
)


def _metadata(row: dict[str, Any], fields: tuple[str, ...]) -> dict[str, Any]:
    """Allowlist fields before fingerprinting or classifying caller-supplied rows."""
    return {
        field: value.isoformat() if isinstance(value, (date, datetime)) else value
        for field in fields
        if (value := row.get(field)) is not None
    }


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _opaque(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()[:20]


def _legacy_azure_name(scope_id: str, service: str, prefix: str) -> str:
    """Reconstruct the old name for metadata comparison only; never read it."""
    base = _slug(prefix, default="mozaiks-connector")
    service_slug = _slug(service, default="service")
    scope_slug = _slug(scope_id, default="scope")
    digest = hashlib.sha1(scope_id.encode("utf-8")).hexdigest()[:10]
    return f"{base}-{service_slug}-{scope_slug[:40]}-{digest}"[:127]


def build_inventory(
    connectors: list[dict[str, Any]],
    secrets: list[dict[str, Any]],
    *,
    provider: Provider,
    prefix: str = "mozaiks-connector",
) -> dict[str, Any]:
    """Classify projected metadata without accessing encrypted or plaintext values."""
    if provider not in {"mongo", "azure_key_vault"}:
        raise ValueError("unsupported connector vault provider")
    metadata = [_metadata(row, CONNECTOR_FIELDS) for row in connectors]
    vault = [_metadata(row, SECRET_FIELDS) for row in secrets]
    snapshot = {
        "provider": provider,
        "prefix": prefix,
        "connectors": sorted(metadata, key=_canonical_json),
        "secrets": sorted(vault, key=_canonical_json),
    }
    fingerprint = hashlib.sha256(_canonical_json(snapshot).encode("utf-8")).hexdigest()
    owners: dict[tuple[str, str, str], list[int]] = defaultdict(list)
    for index, row in enumerate(metadata):
        key = (str(row.get("scope") or ""), str(row.get("scope_id") or ""), normalize_connector_service(row.get("service")))
        owners[key].append(index)

    qualified_counts = Counter(
        (str(row.get("scope")), str(row.get("scope_id")), str(row.get("service")))
        for row in vault if row.get("scope") in {"app", "workspace"}
    )
    covered: set[int] = set()
    findings: list[dict[str, Any]] = []

    for row in vault:
        scope = str(row.get("scope") or "")
        scope_id = str(row.get("scope_id") or "")
        service = str(row.get("service") or "")
        matching: list[int] = []
        if scope in {"app", "workspace"}:
            key = (scope, scope_id, service)
            matching = owners.get(key, [])
            expected = _secret_name(scope, scope_id, service, prefix=prefix) if scope_id and service else ""
            if provider == "azure_key_vault" and (
                row.get("managed_by") != "mozaiks"
                or row.get("connector_prefix") != _slug(prefix, default="mozaiks-connector")
            ):
                status = "qualified_identity_mismatch"
            elif row.get("version_identity_conflict"):
                status = "version_identity_conflict"
            elif qualified_counts[key] > 1:
                status = "duplicate_qualified_secret"
            elif len(matching) != 1:
                status = "orphan_or_duplicate_owner"
            elif (
                row.get("secret_name") != expected
                or metadata[matching[0]].get("secret_name") != expected
                or metadata[matching[0]].get("secret_storage") != provider
                or not metadata[matching[0]].get("secret_available")
            ):
                status = "qualified_identity_mismatch"
            else:
                status = "qualified_ready"
            covered.update(matching)
        elif scope:
            status = "invalid_vault_scope"
        else:
            for key, indices in owners.items():
                owner_scope, owner_id, owner_service = key
                if owner_scope not in {"app", "workspace"}:
                    continue
                if provider == "mongo":
                    matches = owner_id == scope_id and _slug(owner_service, default="service") == service
                else:
                    matches = row.get("secret_name") == _legacy_azure_name(owner_id, owner_service, prefix)
                if matches:
                    matching.extend(indices)
            covered.update(matching)
            status = (
                "legacy_ambiguous" if len(matching) > 1 or row.get("version_identity_conflict")
                else "legacy_review" if matching else "legacy_orphan"
            )

        findings.append({
            "ref": _opaque(row),
            "status": status,
            "owner_refs": sorted(_opaque(metadata[index]) for index in matching),
        })

    for indices in owners.values():
        if len(indices) > 1:
            findings.append({
                "ref": _opaque([metadata[index] for index in indices]),
                "status": "duplicate_connector_metadata",
                "owner_refs": sorted(_opaque(metadata[index]) for index in indices),
            })
    for index, row in enumerate(metadata):
        if (row.get("secret_available") or row.get("status") in {"active", "expiring"}) and index not in covered:
            findings.append({"ref": _opaque(row), "status": "missing_qualified_secret", "owner_refs": [_opaque(row)]})

    findings.sort(key=lambda row: (row["status"], row["ref"]))
    counts = dict(sorted(Counter(row["status"] for row in findings).items()))
    return {
        "format": "connector-vault-metadata-inventory-v1",
        "provider": provider,
        "fingerprint": fingerprint,
        "counts": {"connectors": len(metadata), "vault_records": len(vault), **counts},
        "ready": all(row["status"] == "qualified_ready" for row in findings),
        "findings": findings,
    }


async def _mongo_metadata(uri: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    from motor.motor_asyncio import AsyncIOMotorClient

    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5_000)
    try:
        database = client[SYSTEM_DATABASE]
        connectors = await database[PlatformCollections.CONNECTORS].find(
            {}, {field: 1 for field in CONNECTOR_FIELDS} | {"_id": 0}
        ).to_list(length=MAX_RECORDS + 1)
        secrets = await database[PlatformCollections.CONNECTOR_SECRETS].find(
            {}, {field: 1 for field in SECRET_FIELDS} | {"_id": 0}
        ).to_list(length=MAX_RECORDS + 1)
        if len(connectors) > MAX_RECORDS or len(secrets) > MAX_RECORDS:
            raise RuntimeError("connector inventory exceeds bounded record limit")
        return connectors, secrets
    finally:
        client.close()


def _azure_metadata(vault_name: str, prefix: str) -> list[dict[str, Any]]:
    from azure.identity import DefaultAzureCredential
    from azure.keyvault.secrets import SecretClient

    client = SecretClient(vault_url=f"https://{vault_name}.vault.azure.net/", credential=DefaultAzureCredential())
    full_prefix = _slug(prefix, default="mozaiks-connector")
    if len(full_prefix) > 126:
        raise ValueError("connector prefix is too long for an unambiguous old-name inventory")
    short_prefix = full_prefix[:20]
    results: list[dict[str, Any]] = []
    for properties in client.list_properties_of_secrets():
        tags = properties.tags or {}
        is_old_name = properties.name.startswith(f"{full_prefix}-")
        is_scoped_name = properties.name.startswith(f"{short_prefix}-") and tags.get(
            "connector_prefix"
        ) in {None, full_prefix}
        if not (is_old_name or is_scoped_name):
            continue
        if len(results) >= MAX_RECORDS:
            raise RuntimeError("connector inventory exceeds bounded record limit")
        versions: list[dict[str, Any]] = []
        version_identity_conflict = False
        for version in client.list_properties_of_secret_versions(properties.name):
            if len(versions) >= MAX_RECORDS:
                raise RuntimeError("connector version inventory exceeds bounded record limit")
            version_tags = version.tags or {}
            versions.append({
                "version": version.version,
                "created_at": version.created_on,
                "expires_at": version.expires_on,
                "tags": {key: version_tags.get(key) for key in ("managed_by", "connector_prefix", "scope", "scope_id", "service")},
            })
            if any(version_tags.get(key) != tags.get(key) for key in ("managed_by", "connector_prefix", "scope", "scope_id", "service")):
                version_identity_conflict = True
        results.append({
            "scope": tags.get("scope"),
            "scope_id": tags.get("scope_id"),
            "service": tags.get("service"),
            "managed_by": tags.get("managed_by"),
            "connector_prefix": tags.get("connector_prefix"),
            "secret_name": properties.name,
            "stored_at": properties.created_on,
            "expires_at": properties.expires_on,
            "version_count": len(versions),
            "version_fingerprint": _opaque(versions),
            "version_identity_conflict": version_identity_conflict,
        })
    return results


async def _run(args: argparse.Namespace) -> int:
    uri = os.getenv("MONGO_URI", "").strip()
    if not uri:
        raise RuntimeError("MONGO_URI is required for connector metadata inventory")
    connectors, mongo_secrets = await _mongo_metadata(uri)
    if args.provider == "mongo":
        secrets = mongo_secrets
    else:
        vault_name = os.getenv("AZURE_KEY_VAULT_NAME", "").strip()
        if not vault_name:
            raise RuntimeError("AZURE_KEY_VAULT_NAME is required for Azure inventory")
        secrets = await asyncio.to_thread(_azure_metadata, vault_name, args.prefix)
    report = build_inventory(connectors, secrets, provider=args.provider, prefix=args.prefix)
    if args.expect_fingerprint and args.expect_fingerprint != report["fingerprint"]:
        raise RuntimeError("inventory changed since the reviewed dry run")
    target = Path(args.report).resolve()
    if target.is_relative_to(REPO_ROOT) and not target.is_relative_to(REPO_ROOT / ".local"):
        raise RuntimeError("reports inside the repository must be placed under ignored .local")
    if target.exists():
        raise RuntimeError("report already exists; select a new private path")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"fingerprint": report["fingerprint"], "ready": report["ready"], "counts": report["counts"]}))
    return 0 if report["ready"] else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Write a private metadata-only connector vault dry-run report")
    parser.add_argument("--provider", choices=["mongo", "azure_key_vault"], required=True)
    parser.add_argument("--report", required=True, help="New private report path")
    parser.add_argument("--prefix", default=os.getenv("MOZAIKS_CONNECTOR_SECRET_PREFIX", "mozaiks-connector"))
    parser.add_argument("--expect-fingerprint", help="Reject inventory drift from a reviewed report")
    return asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
