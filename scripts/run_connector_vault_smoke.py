from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _load_env() -> None:
    load_dotenv(REPO_ROOT / ".env", override=False)


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            return str(value)
    return str(value)


async def _run_metadata_mode(*, scope_id: str, service: str, display_name: str | None) -> dict[str, Any]:
    from mozaiksai.core.data.persistence.connector_store import ConnectorStore
    from mozaiksai.core.workflow.generator_support.connector_service import (
        delete_connector,
        get_connector,
        get_connector_backend_summary,
        save_connector_draft,
    )

    store = ConnectorStore()
    backend_summary = await get_connector_backend_summary()
    recorded = await save_connector_draft(
        scope=ConnectorStore.SCOPE_APP,
        scope_id=scope_id,
        user_id="smoke-user",
        service=service,
        display_name=display_name,
        key_length=24,
        workflow_name="connector_vault_smoke",
        chat_id="smoke-chat",
        agent_message_id="smoke-message",
        ui_event_id="smoke-ui-event",
        status_reason="Smoke test metadata-only connector path.",
        store=store,
    )
    status = await get_connector(scope=ConnectorStore.SCOPE_APP, scope_id=scope_id, service=service, store=store)
    deleted = await delete_connector(scope=ConnectorStore.SCOPE_APP, scope_id=scope_id, service=service, store=store)
    return {
        "mode": "metadata",
        "scope_id": scope_id,
        "service": service,
        "backend_summary": backend_summary,
        "recorded": recorded,
        "status": status,
        "deleted": deleted,
    }


async def _run_secret_mode(
    *,
    scope_id: str,
    service: str,
    display_name: str | None,
    secret_value: str,
    ttl_days: int,
) -> dict[str, Any]:
    from mozaiksai.core.data.persistence.connector_store import ConnectorStore
    from mozaiksai.core.workflow.generator_support.connector_service import (
        delete_connector,
        get_connector,
        get_connector_backend_summary,
        get_secret,
        save_connector,
    )

    store = ConnectorStore()
    backend_summary = await get_connector_backend_summary()
    stored = await save_connector(
        scope=ConnectorStore.SCOPE_APP,
        scope_id=scope_id,
        user_id="smoke-user",
        service=service,
        secret_value=secret_value,
        display_name=display_name,
        ttl_days=ttl_days,
        store=store,
    )
    status = await get_connector(scope=ConnectorStore.SCOPE_APP, scope_id=scope_id, service=service, store=store)
    secret_result = await get_secret(scope_id=scope_id, service=service)
    deleted = await delete_connector(scope=ConnectorStore.SCOPE_APP, scope_id=scope_id, service=service, store=store)
    return {
        "mode": "secret",
        "scope_id": scope_id,
        "service": service,
        "backend_summary": backend_summary,
        "stored": stored,
        "status": status,
        "secret_result": {
            "success": bool(secret_result.get("success")),
            "provider": secret_result.get("provider"),
            "secret_name": secret_result.get("secret_name"),
            "expires_at": secret_result.get("expires_at"),
            "secret_value_length": len(str(secret_result.get("secret_value") or "")),
            "error": secret_result.get("error"),
        },
        "deleted": deleted,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a live connector/vault smoke check against the local Mozaiks runtime contracts.")
    parser.add_argument("--mode", choices=["metadata", "secret"], default="metadata")
    parser.add_argument("--app-id", default=f"smoke-app-{uuid.uuid4().hex[:8]}")
    parser.add_argument("--service", default="mozaikspay")
    parser.add_argument("--display-name", default="MozaiksPay")
    parser.add_argument("--secret-value", default=None)
    parser.add_argument("--secret-env", default="MOZAIKS_SMOKE_CONNECTOR_SECRET")
    parser.add_argument("--ttl-days", type=int, default=30)
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON only.")
    return parser


def _print_human_summary(result: dict[str, Any]) -> None:
    mode = result.get("mode")
    print(f"[connector-smoke] mode: {mode}")
    print(f"[connector-smoke] scope_id: {result.get('scope_id')}")
    print(f"[connector-smoke] service: {result.get('service')}")

    backend = result.get("backend_summary") or {}
    print(
        "[connector-smoke] backend:"
        f" provider={backend.get('provider')} configured={backend.get('configured')}"
        f" mode={backend.get('mode')} vault_name={backend.get('vault_name')}"
    )

    if mode == "metadata":
        status = (result.get("status") or {}).get("status")
        deleted = (result.get("deleted") or {}).get("deleted")
        print(f"[connector-smoke] metadata status: {status}")
        print(f"[connector-smoke] metadata cleanup deleted: {deleted}")
        return

    stored = result.get("stored") or {}
    secret_result = result.get("secret_result") or {}
    deleted = result.get("deleted") or {}
    print(
        "[connector-smoke] secret store:"
        f" success={stored.get('success')} error={stored.get('error')}"
    )
    print(
        "[connector-smoke] secret fetch:"
        f" success={secret_result.get('success')} provider={secret_result.get('provider')}"
        f" value_length={secret_result.get('secret_value_length')}"
        f" error={secret_result.get('error')}"
    )
    print(
        "[connector-smoke] cleanup:"
        f" metadata_deleted={deleted.get('deleted')}"
        f" secret_deleted={deleted.get('secret_deleted')}"
        f" secret_error={deleted.get('secret_error')}"
    )


async def _run(args: argparse.Namespace) -> int:
    _load_env()

    if not os.getenv("MONGO_URI"):
        print("MONGO_URI is not set.", file=sys.stderr)
        return 2

    if args.mode == "metadata":
        result = await _run_metadata_mode(
            scope_id=args.app_id,
            service=args.service,
            display_name=args.display_name,
        )
    else:
        secret_value = args.secret_value or os.getenv(args.secret_env or "")
        if not secret_value:
            print(
                f"No secret value provided. Set --secret-value or environment variable {args.secret_env}.",
                file=sys.stderr,
            )
            return 2
        result = await _run_secret_mode(
            scope_id=args.app_id,
            service=args.service,
            display_name=args.display_name,
            secret_value=secret_value,
            ttl_days=max(int(args.ttl_days), 1),
        )

    if args.json:
        print(json.dumps(_json_safe(result), indent=2))
    else:
        _print_human_summary(result)
        print(json.dumps(_json_safe(result), indent=2))

    if args.mode == "metadata":
        return 0 if (result.get("status") or {}).get("status") == "metadata_only" else 1

    stored_success = bool((result.get("stored") or {}).get("success"))
    fetched_success = bool((result.get("secret_result") or {}).get("success"))
    return 0 if stored_success and fetched_success else 1


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
