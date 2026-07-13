from __future__ import annotations

"""Validate Mozaiks usage pricing against one live OpenAI Responses API call.

This script intentionally performs a tiny call and prints the usage payload plus
the Mozaiks estimated cost. It is an operator smoke test, not a billing source
of truth.
"""

import argparse
import os
from typing import Any

from openai import OpenAI

from mozaiksai.core.usage.pricing import estimate_token_cost


def _usage_value(usage: Any, name: str) -> int:
    if hasattr(usage, name):
        try:
            return max(0, int(getattr(usage, name) or 0))
        except (TypeError, ValueError):
            return 0
    if isinstance(usage, dict):
        try:
            return max(0, int(usage.get(name) or 0))
        except (TypeError, ValueError):
            return 0
    return 0


def _cached_input_tokens(usage: Any) -> int:
    details = getattr(usage, "input_tokens_details", None)
    if details is None and isinstance(usage, dict):
        details = usage.get("input_tokens_details")
    if details is None:
        details = getattr(usage, "prompt_tokens_details", None)
    if details is None and isinstance(usage, dict):
        details = usage.get("prompt_tokens_details")
    return _usage_value(details, "cached_tokens")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--model",
        default=os.getenv("OPENAI_PRICING_TEST_MODEL")
        or os.getenv("DEFAULT_LLM_MODEL")
        or "gpt-5-nano",
    )
    parser.add_argument("--prompt", default="Reply with exactly: ok")
    parser.add_argument("--max-output-tokens", type=int, default=16)
    args = parser.parse_args()

    if not os.getenv("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY is not set; cannot run live OpenAI validation.")

    client = OpenAI()
    response = client.responses.create(
        model=args.model,
        input=args.prompt,
        max_output_tokens=args.max_output_tokens,
    )
    usage = response.usage
    input_tokens = _usage_value(usage, "input_tokens") or _usage_value(usage, "prompt_tokens")
    output_tokens = _usage_value(usage, "output_tokens") or _usage_value(usage, "completion_tokens")
    total_tokens = _usage_value(usage, "total_tokens") or input_tokens + output_tokens
    cached_tokens = _cached_input_tokens(usage)

    estimate = estimate_token_cost(
        model_name=args.model,
        prompt_tokens=input_tokens,
        completion_tokens=output_tokens,
        cached_prompt_tokens=cached_tokens,
    )

    print(f"model={args.model}")
    print(f"input_tokens={input_tokens}")
    print(f"cached_input_tokens={cached_tokens}")
    print(f"output_tokens={output_tokens}")
    print(f"total_tokens={total_tokens}")
    print(f"estimated_cost_usd={estimate.estimated_cost_usd:.12f}")
    print(f"cost_source={estimate.cost_source}")
    print(f"pricing_catalog={os.getenv('MOZAIKS_USAGE_PRICING_CATALOG_PATH') or ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
