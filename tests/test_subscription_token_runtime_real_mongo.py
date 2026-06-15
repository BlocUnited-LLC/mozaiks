from __future__ import annotations

import os

import pytest

from scripts.smoke_subscription_token_runtime_e2e import run_smoke

RUN_ENV = "MOZAIKS_RUN_SUBSCRIPTION_TOKEN_DOCKER_SMOKE"


pytestmark = pytest.mark.skipif(
    os.getenv(RUN_ENV) != "1",
    reason=f"set {RUN_ENV}=1 to run the Docker/Mongo subscription-token runtime smoke",
)


@pytest.mark.asyncio
async def test_subscription_token_runtime_real_mongo_smoke() -> None:
    result = await run_smoke(
        mongo_uri=os.getenv("MONGO_URI") or "mongodb://localhost:27017/mozaiks_subscription_token_smoke",
        require_docker=True,
    )

    assert result.initial_balance == 100
    assert result.balance_after_debit == 10
    assert result.provider_calls_before_denial == result.provider_calls_after_denial
    assert result.denied_error_code == "INSUFFICIENT_TOKENS"
