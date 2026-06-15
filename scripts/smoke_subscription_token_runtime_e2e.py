"""Docker/Mongo-backed subscription-token runtime smoke.

This smoke exercises the generic OSS SaaS runtime path against a real MongoDB
process and a stub LLM provider:

1. Load a factory-shaped app with config/subscriptions.yaml.
2. Store an active subscription assignment in the app data database.
3. Materialize token allowances in RuntimeTokenWallet collections.
4. Allow an LLM boundary call while balance is sufficient.
5. Debit usage idempotently.
6. Deny a later LLM boundary call before the provider is invoked.

No real LLM or payment provider calls are made.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from mozaiksai.core.capabilities.simple_llm import SimpleLLMCapabilityService
from mozaiksai.core.core_config import close_mongo_client, get_mongo_client
from mozaiksai.core.data.persistence.namespaces import SYSTEM_DATABASE, RuntimeCollections
from mozaiksai.core.runtime.app.loader import AppLoader
from mozaiksai.core.tokens.guard import TokenUsageDenied
from mozaiksai.core.tokens.wallet import TokenWalletLedger

DEFAULT_MONGO_URI = "mongodb://localhost:27017/mozaiks_subscription_token_smoke"


@dataclass(frozen=True)
class SmokeResult:
    app_id: str
    user_id: str
    app_database: str
    initial_balance: int
    balance_after_debit: int
    provider_calls_before_denial: int
    provider_calls_after_denial: int
    denied_error_code: str


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeLLMClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.is_closed = False

    async def post(self, url: str, *, headers=None, json=None):  # noqa: ANN001
        self.calls.append({"url": url, "headers": headers, "json": json})
        return _FakeResponse(
            {
                "choices": [{"message": {"content": "stubbed smoke response"}}],
                "usage": {
                    "prompt_tokens": 4,
                    "completion_tokens": 6,
                    "total_tokens": 10,
                },
            }
        )

    async def aclose(self) -> None:
        self.is_closed = True


async def _fake_provider(llm_config=None):  # noqa: ANN001
    return {
        "model": (llm_config or {}).get("model") or "stub-smoke-model",
        "api_key": "stub-key",
        "api_base": "https://stub.local/v1",
    }


def _write_app(app_root: Path) -> None:
    app_root.mkdir(parents=True, exist_ok=True)
    (app_root / "config").mkdir()
    (app_root / "data").mkdir()
    (app_root / "app.json").write_text(
        json.dumps(
            {
                "name": "Subscription Token Smoke",
                "appName": "Subscription Token Smoke",
                "version": "1.0.0",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (app_root / "data" / "contract.json").write_text(
        json.dumps(
            {
                "version": "1",
                "surfaces": [],
                "aliases": [
                    {
                        "alias": "billing.subscriptions",
                        "collection": "subscription_token_smoke_subscriptions",
                    }
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (app_root / "config" / "subscriptions.yaml").write_text(
        textwrap.dedent(
            """
            schema_version: mozaiks.subscriptions.v1
            label: Subscription Token Smoke
            default_plan_id: free
            assignment_store:
              data_alias: billing.subscriptions
              user_id_field: user_id
              workspace_id_field: workspace_id
              active_statuses: [active]
            token_wallets:
              - wallet_id: ai_tokens
                label: AI tokens
                unit: tokens
                usage_meter_id: ai_tokens
                scope: user
                auto_debit_usage: true
                allow_negative_balance: false
            plans:
              - plan_id: free
                label: Free
                capabilities: []
              - plan_id: pro
                label: Pro
                capabilities: [ai.chat]
                token_allowances:
                  - wallet_id: ai_tokens
                    amount: 100
                    cadence: monthly
                    label: Pro smoke monthly AI tokens
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )


def _docker_mongo_running() -> bool:
    try:
        output = subprocess.check_output(
            ["docker", "ps", "--format", "{{.Names}} {{.Image}} {{.Ports}}"],
            text=True,
            stderr=subprocess.STDOUT,
        )
    except Exception:
        return False
    return any("mongo" in line.lower() for line in output.splitlines())


async def run_smoke(
    *,
    mongo_uri: str = DEFAULT_MONGO_URI,
    app_database: str | None = None,
    require_docker: bool = False,
    keep_app: bool = False,
) -> SmokeResult:
    if require_docker and not _docker_mongo_running():
        raise RuntimeError("No running Docker MongoDB container found.")

    app_id = f"subscription_token_smoke_{uuid4().hex}"
    user_id = f"user_{uuid4().hex[:10]}"
    app_database_name = app_database or f"mozaiks_subscription_token_test_{uuid4().hex[:10]}"
    if "test" not in app_database_name.lower() and "smoke" not in app_database_name.lower():
        raise ValueError("app_database must contain 'test' or 'smoke'")

    original_env = {
        key: os.environ.get(key)
        for key in (
            "MONGO_URI",
            "MOZAIKS_APP_DATABASE_NAME",
            "MOZAIKS_APP_DATA_DATABASE_NAME",
            "PLATFORM_PATH",
            "MOZAIKS_APP_WORKSPACE_PATH",
        )
    }
    temp_dir = Path(tempfile.mkdtemp(prefix="mozaiks-subscription-token-smoke-"))
    app_root = temp_dir / "app"

    os.environ["MONGO_URI"] = mongo_uri
    os.environ["MOZAIKS_APP_DATABASE_NAME"] = app_database_name
    os.environ["MOZAIKS_APP_DATA_DATABASE_NAME"] = app_database_name
    os.environ["PLATFORM_PATH"] = str(app_root)
    os.environ["MOZAIKS_APP_WORKSPACE_PATH"] = str(app_root)
    close_mongo_client()

    client = get_mongo_client()
    app_db = client[app_database_name]
    token_db = client[SYSTEM_DATABASE]
    ledger = TokenWalletLedger()

    try:
        await client.admin.command("ping")
        _write_app(app_root)
        loaded = await AppLoader.load(app_root)
        assert loaded.subscriptions_config is not None
        config = loaded.subscriptions_config
        wallet = config.token_wallet_by_id("ai_tokens")
        assert wallet is not None

        assignments = app_db["subscription_token_smoke_subscriptions"]
        await assignments.insert_one(
            {
                "app_id": app_id,
                "user_id": user_id,
                "tenant_id": None,
                "workspace_id": None,
                "plan_id": "pro",
                "status": "active",
                "granted_capabilities": [
                    {"pack_id": "ai", "capability_id": "ai.chat"}
                ],
            }
        )

        service = SimpleLLMCapabilityService()
        fake_client = _FakeLLMClient()
        service._client = fake_client  # type: ignore[assignment]
        service._select_provider = _fake_provider  # type: ignore[method-assign]

        first = await service.generate_chat_completion(
            messages=[{"role": "user", "content": "short smoke prompt"}],
            llm_config={"model": "stub-smoke-model"},
            app_id=app_id,
            user_id=user_id,
        )
        assert first["content"] == "stubbed smoke response"

        initial_balance = (
            await ledger.query_balance(
                app_id=app_id,
                wallet_id="ai_tokens",
                user_id=user_id,
                preferred_scope="user",
            )
        )["balance"]
        assert initial_balance == 100

        usage_payload = {
            "event_id": f"usage_{uuid4().hex}",
            "app_id": app_id,
            "user_id": user_id,
            "chat_id": "smoke_chat",
            "workflow_name": "SmokeWorkflow",
            "agent_name": "SmokeAgent",
            "model_name": "stub-smoke-model",
            "prompt_tokens": 40,
            "completion_tokens": 50,
            "total_tokens": 90,
        }
        debit_one = await ledger.record_usage_debit(usage_payload, wallet=wallet)
        debit_two = await ledger.record_usage_debit(usage_payload, wallet=wallet)
        assert debit_one is not None and debit_one.status == "applied"
        assert debit_two is not None and debit_two.status == "applied"

        balance_after_debit = (
            await ledger.query_balance(
                app_id=app_id,
                wallet_id="ai_tokens",
                user_id=user_id,
                preferred_scope="user",
            )
        )["balance"]
        assert balance_after_debit == 10

        calls_before_denial = len(fake_client.calls)
        denied_error_code = ""
        try:
            await service.generate_chat_completion(
                messages=[{"role": "user", "content": "this call should be blocked"}],
                llm_config={"model": "stub-smoke-model"},
                extra_payload={"max_completion_tokens": 25},
                app_id=app_id,
                user_id=user_id,
            )
        except TokenUsageDenied as exc:
            denied_error_code = exc.decision.error_code or ""
        else:  # pragma: no cover - script failure path
            raise AssertionError("Expected depleted wallet to deny before provider call")

        assert denied_error_code == "INSUFFICIENT_TOKENS"
        assert len(fake_client.calls) == calls_before_denial

        return SmokeResult(
            app_id=app_id,
            user_id=user_id,
            app_database=app_database_name,
            initial_balance=initial_balance,
            balance_after_debit=balance_after_debit,
            provider_calls_before_denial=calls_before_denial,
            provider_calls_after_denial=len(fake_client.calls),
            denied_error_code=denied_error_code,
        )
    finally:
        await token_db[RuntimeCollections.RUNTIME_TOKEN_WALLET_BALANCES].delete_many({"app_id": app_id})
        await token_db[RuntimeCollections.RUNTIME_TOKEN_WALLET_ENTRIES].delete_many({"app_id": app_id})
        await client.drop_database(app_database_name)
        close_mongo_client()
        for key, value in original_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        close_mongo_client()
        if not keep_app:
            shutil.rmtree(temp_dir, ignore_errors=True)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the real-Mongo subscription/token runtime smoke."
    )
    parser.add_argument("--mongo-uri", default=os.getenv("MONGO_URI") or DEFAULT_MONGO_URI)
    parser.add_argument("--app-database", default="")
    parser.add_argument(
        "--require-docker",
        action="store_true",
        help="Fail unless a running Docker MongoDB container is detected.",
    )
    parser.add_argument("--keep-app", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(list(argv or []))
    try:
        result = asyncio.run(
            run_smoke(
                mongo_uri=args.mongo_uri,
                app_database=args.app_database or None,
                require_docker=bool(args.require_docker),
                keep_app=bool(args.keep_app),
            )
        )
    except Exception as exc:
        print(f"subscription token runtime smoke failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(asdict(result), indent=2, sort_keys=True))
    print("subscription token runtime smoke passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
