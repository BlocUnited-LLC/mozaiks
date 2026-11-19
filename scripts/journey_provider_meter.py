"""Isolated, bounded OpenAI chat gateway for synthetic journey evidence.

This is an operator test harness, not runtime metering or an account spending cap.
The caller must isolate every journey process and route all provider traffic
through this gateway; otherwise its journal is incomplete. Only OpenAI chat
completions with one approved model are supported. Streaming responses are
buffered until their final usage and terminal marker have been checked.
An in-flight call can exceed the estimated budget. Unknown-usage failures stop
subsequent calls and leave their cost unknown.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import json
import math
import os
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

UPSTREAM = "https://api.openai.com/v1/chat/completions"
MAX_REQUEST_BYTES = 2_000_000
MAX_RESPONSE_BYTES = 2_000_000


@dataclass(frozen=True)
class Limits:
    model: str
    attempts: int
    output_tokens: int
    budget_usd: float
    input_per_million: float
    cached_per_million: float
    output_per_million: float

    def __post_init__(self) -> None:
        if not self.model or type(self.attempts) is not int or self.attempts < 1:
            raise ValueError("model and a positive attempt limit are required")
        if type(self.output_tokens) is not int or self.output_tokens < 1:
            raise ValueError("output limit must be positive")
        for value in (self.budget_usd, self.input_per_million, self.cached_per_million, self.output_per_million):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError("budget and rates must be finite non-negative numbers")

    def estimated_total(self, subtotal: float, prompt: int, completion: int, cached: int) -> float:
        total = (subtotal + (prompt - cached) * (self.input_per_million / 1_000_000)
                 + cached * (self.cached_per_million / 1_000_000)
                 + completion * (self.output_per_million / 1_000_000))
        if not math.isfinite(total):
            raise ValueError("estimated cost exceeds the supported range")
        return total


def usage_tuple(model: Any, usage: Any) -> tuple[str, int, int, int] | None:
    """Return validated provider usage; never infer absent usage as zero."""
    if not isinstance(model, str) or not model or not isinstance(usage, dict):
        return None
    prompt = usage.get("prompt_tokens")
    completion = usage.get("completion_tokens")
    total = usage.get("total_tokens")
    details = usage.get("prompt_tokens_details")
    cached = details.get("cached_tokens") if isinstance(details, dict) else None
    # The Mongo ledger can represent at most a signed 64-bit integer.
    if any(type(x) is not int or not 0 <= x < 2**63 for x in (prompt, completion, total, cached)):
        return None
    if total != prompt + completion or cached > prompt:
        return None
    return model, prompt, completion, cached


def partial_usage(usage: Any) -> dict[str, int]:
    if not isinstance(usage, dict):
        return {}
    result = {
        key: usage[key] for key in ("prompt_tokens", "completion_tokens", "total_tokens")
        if type(usage.get(key)) is int and usage[key] >= 0
    }
    details = usage.get("prompt_tokens_details")
    if isinstance(details, dict) and type(details.get("cached_tokens")) is int and details["cached_tokens"] >= 0:
        result["cached_prompt_tokens"] = details["cached_tokens"]
    return result


def streaming_usage(body: bytes, model: str) -> tuple[tuple[str, int, int, int] | None, dict[str, int]]:
    """Read terminal OpenAI SSE usage without storing model output."""
    values: list[tuple[str, int, int, int]] = []
    done = False
    partial: dict[str, int] = {}
    for line in body.decode("utf-8").splitlines():
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if done:
            return None, partial
        if data == "[DONE]":
            done = True
            continue
        try:
            chunk = json.loads(data)
        except ValueError:
            return None, partial
        if (not isinstance(chunk, dict) or (chunk.get("model") is not None and chunk["model"] != model)
                or chunk.get("service_tier") not in (None, "default")):
            return None, partial
        if chunk.get("usage") is not None:
            partial = partial_usage(chunk["usage"])
            usage = usage_tuple(chunk.get("model"), chunk["usage"])
            if usage is None or usage[0] != model:
                return None, partial
            values.append(usage)
    return (values[0] if done and len(values) == 1 else None), partial


def reconcile_attempts(
    records: list[dict[str, Any]], receipts: list[dict[str, Any]], *,
    app_id: str, user_id: str, chat_ids: set[str], allow_isolated_chatless_auxiliary: bool = False,
) -> dict[str, Any]:
    """Compare provider and scoped ledger usage; identical tuples are ambiguous.

    This is a multiset check, not a provider-response identity proof. Every
    receipt must match the isolated app, user and known journey chats.
    """
    if (not isinstance(app_id, str) or not app_id.strip() or not isinstance(user_id, str) or not user_id.strip()
            or (not chat_ids and not allow_isolated_chatless_auxiliary)
            or any(not isinstance(chat, str) or not chat.strip() for chat in chat_ids)):
        raise ValueError("isolated app, user and a chat or explicit chatless auxiliary scope are required")
    provider: list[tuple[str, int, int, int]] = []
    unknown = 0
    for row in records:
        if row.get("phase") != "result":
            continue
        value = usage_tuple(row.get("model"), row.get("usage"))
        if row.get("outcome") != "completed" or value is None:
            unknown += 1
        else:
            provider.append(value)
    ledger: list[tuple[str, int, int, int]] = []
    invalid = 0
    receipt_ids: set[str] = set()
    for row in receipts:
        event_id = row.get("event_id")
        chat = row.get("chat_id")
        eligible_chat = chat in chat_ids or (
            allow_isolated_chatless_auxiliary and chat is None and row.get("execution_kind") == "auxiliary"
        )
        if (not isinstance(event_id, str) or not event_id
                or event_id in receipt_ids or row.get("app_id") != app_id or row.get("user_id") != user_id
                or not eligible_chat):
            invalid += 1
            continue
        receipt_ids.add(event_id)
        value = usage_tuple(row.get("model_name"), {
            "prompt_tokens": row.get("prompt_tokens"),
            "completion_tokens": row.get("completion_tokens"),
            "total_tokens": row.get("total_tokens"),
            "prompt_tokens_details": {"cached_tokens": row.get("cached_prompt_tokens")},
        })
        if value is None:
            invalid += 1
        else:
            ledger.append(value)
    p, r = Counter(provider), Counter(ledger)
    ambiguous = sum(count for value, count in p.items() if count > 1 and r[value] > 0)
    missing = sum((p - r).values())
    extra = sum((r - p).values())
    attempt_ids = [row.get("attempt") for row in records if row.get("phase") == "attempt"]
    result_ids = [row.get("attempt") for row in records if row.get("phase") == "result"]
    attempts, results = len(attempt_ids), len(result_ids)
    expected = [(phase, i) for i in attempt_ids for phase in ("attempt", "result")]
    pairs = [(row.get("phase"), row.get("attempt")) for row in records]
    valid_prefix = (attempts > 0 and all(type(i) is int and i > 0 for i in attempt_ids + result_ids)
                    and attempt_ids == list(range(1, attempts + 1)) and result_ids == attempt_ids[:results]
                    and attempts - results in (0, 1) and pairs == expected[:len(pairs)])
    valid_journal = valid_prefix and attempts == results
    return {
        "attempts": attempts, "results": results, "unknown": unknown + max(0, attempts - results),
        "missing_receipts": missing, "extra_receipts": extra, "invalid_receipts": invalid,
        "ambiguous_calls": ambiguous, "valid_journal": valid_journal, "valid_prefix": valid_prefix,
        "reconciled": valid_journal and not (unknown or invalid or missing or extra or ambiguous),
        "method": "model_and_usage_multiset_not_provider_identity",
    }


class ProviderGateway:
    def __init__(self, *, limits: Limits, token: str, upstream_key: str, client: httpx.AsyncClient, journal: Path,
                 pricing_sha256: str | None = None):
        if not token or not upstream_key or hmac.compare_digest(token, upstream_key):
            raise ValueError("distinct nonempty local token and upstream credential are required")
        self.limits, self.token, self.key, self.client, self.journal = limits, token, upstream_key, client, journal
        self.attempts = 0
        self.usd = 0.0
        self.stopped = False
        self.lock = asyncio.Lock()
        self.pending = 0
        self.pricing_sha256 = pricing_sha256
        fd = os.open(journal, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)

    def record(self, value: dict[str, Any]) -> None:
        try:
            with self.journal.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(value, separators=(",", ":")) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        except OSError:
            self.stopped = True
            raise

    async def dispatch(self, request: Request) -> Response:
        if not hmac.compare_digest(request.headers.get("authorization", ""), f"Bearer {self.token}"):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        try:
            body_buffer = bytearray()
            async for chunk in request.stream():
                body_buffer.extend(chunk)
                if len(body_buffer) > MAX_REQUEST_BYTES:
                    return JSONResponse({"error": "request too large"}, status_code=413)
            body = bytes(body_buffer)
            payload = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            payload = None
        if not isinstance(payload, dict) or payload.get("model") != self.limits.model:
            return JSONResponse({"error": "unsupported request"}, status_code=400)
        output = payload.get("max_completion_tokens", payload.get("max_tokens"))
        tools = payload.get("tools", [])
        messages = payload.get("messages")
        def text_only(message: Any) -> bool:
            if not isinstance(message, dict) or "audio" in message:
                return False
            content = message.get("content")
            return (content is None or isinstance(content, str) or (
                isinstance(content, list) and all(
                    isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str)
                    for part in content
                )
            ))
        stream = payload.get("stream", False)
        stream_options = payload.get("stream_options")
        if (type(stream) is not bool or type(payload.get("n", 1)) is not int or payload.get("n", 1) != 1
                or type(output) is not int or not 0 < output <= self.limits.output_tokens
                or ("max_completion_tokens" in payload and "max_tokens" in payload)
                or not isinstance(tools, list) or any(not isinstance(x, dict) or x.get("type") != "function" for x in tools)
                or not isinstance(messages, list) or not messages or not all(text_only(m) for m in messages)
                or payload.get("service_tier", "default") != "default"
                or payload.get("modalities", ["text"]) != ["text"]
                or (stream_options is not None and stream_options != {"include_usage": True})
                or any(key in payload for key in ("audio", "web_search_options", "prediction"))):
            return JSONResponse({"error": "unsupported request limits or tools"}, status_code=400)
        if stream:
            payload["stream_options"] = {"include_usage": True}
        # Forward precisely the parsed and checked values, including when the
        # original JSON contained duplicate keys.
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        if self.pending >= self.limits.attempts:
            return JSONResponse({"error": "provider queue full"}, status_code=429)
        self.pending += 1
        try:
            return await self.forward(body, stream=stream)
        finally:
            self.pending -= 1

    async def forward(self, body: bytes, *, stream: bool) -> Response:
        async with self.lock:
            if self.stopped or self.attempts >= self.limits.attempts or self.usd >= self.limits.budget_usd:
                return JSONResponse({"error": "provider gate stopped"}, status_code=429)
            self.attempts += 1
            number = self.attempts
            self.record({"phase": "attempt", "attempt": number, "model": self.limits.model,
                         "pricing_sha256": self.pricing_sha256, "max_attempts": self.limits.attempts,
                         "max_output_tokens": self.limits.output_tokens, "budget_usd": self.limits.budget_usd})
            response_status = None
            partial: dict[str, int] = {}
            try:
                async with asyncio.timeout(90.0), self.client.stream("POST", UPSTREAM, content=body, headers={
                    "authorization": f"Bearer {self.key}", "content-type": "application/json",
                }) as response:
                    response_status = response.status_code
                    chunks = bytearray()
                    async for chunk in response.aiter_bytes():
                        chunks.extend(chunk)
                        if len(chunks) > MAX_RESPONSE_BYTES:
                            raise ValueError("response too large")
                    if stream:
                        usage, partial = streaming_usage(bytes(chunks), self.limits.model)
                    else:
                        data = json.loads(chunks)
                        partial = partial_usage(data.get("usage")) if isinstance(data, dict) else {}
                        usage = usage_tuple(data.get("model"), data.get("usage")) if isinstance(data, dict) else None
                        if isinstance(data, dict) and data.get("service_tier") not in (None, "default"):
                            raise ValueError("provider response uses an unpriced service tier")
                    if response.status_code != 200 or usage is None or usage[0] != self.limits.model:
                        raise ValueError("provider response cannot be reconciled")
                    _, prompt, completion, cached = usage
                    self.usd = self.limits.estimated_total(self.usd, prompt, completion, cached)
            except asyncio.CancelledError:
                self.stopped = True
                self.record({"phase": "result", "attempt": number, "model": self.limits.model, "outcome": "unknown",
                             "status": response_status, "partial_usage": partial})
                raise
            except Exception:
                self.stopped = True
                self.record({"phase": "result", "attempt": number, "model": self.limits.model, "outcome": "unknown",
                             "status": response_status, "partial_usage": partial})
                return JSONResponse({"error": "provider call failed or usage unavailable"}, status_code=502)
            safe_usage = {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": prompt + completion,
                          "prompt_tokens_details": {"cached_tokens": cached}}
            self.record({"phase": "result", "attempt": number, "model": self.limits.model,
                         "outcome": "completed", "status": 200, "usage": safe_usage, "estimated_usd": self.usd})
            return Response(content=bytes(chunks), status_code=200,
                            media_type="text/event-stream" if stream else "application/json")


def make_app(gateway: ProviderGateway) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.add_api_route("/v1/chat/completions", gateway.dispatch, methods=["POST"])
    return app


def pricing_rates(path: Path, model: str) -> tuple[dict[str, Any], str]:
    raw = path.read_bytes()
    catalog = json.loads(raw)
    rates = catalog.get("models", catalog)[model]
    if not isinstance(rates, dict) or rates.get("provider") != "openai" or rates.get("mode") != "chat":
        raise ValueError("the exact model must have an OpenAI chat catalog entry")
    Limits(model, 1, 1, 1.0, rates["input_per_1m_usd"], rates["cached_input_per_1m_usd"],
           rates["output_per_1m_usd"])
    return rates, hashlib.sha256(raw).hexdigest()


def summarize_reconciliation(
    records: list[dict[str, Any]], receipts: list[dict[str, Any]], *, limits: Limits, pricing_sha256: str,
    app_id: str, user_id: str, chat_ids: set[str], allow_isolated_chatless_auxiliary: bool = False,
) -> dict[str, Any]:
    comparison = reconcile_attempts(records, receipts, app_id=app_id, user_id=user_id, chat_ids=chat_ids,
                                    allow_isolated_chatless_auxiliary=allow_isolated_chatless_auxiliary)
    attempts = [row for row in records if row.get("phase") == "attempt"]
    valid_config = bool(attempts) and all(
        row.get("model") == limits.model and row.get("pricing_sha256") == pricing_sha256 for row in attempts
    )
    totals = {"prompt_tokens": 0, "completion_tokens": 0, "cached_prompt_tokens": 0}
    subtotal = 0.0
    for row in records:
        if row.get("phase") != "result" or row.get("outcome") != "completed":
            continue
        value = usage_tuple(row.get("model"), row.get("usage"))
        if value is None or value[0] != limits.model:
            valid_config = False
            continue
        _, prompt, completion, cached = value
        totals["prompt_tokens"] += prompt
        totals["completion_tokens"] += completion
        totals["cached_prompt_tokens"] += cached
        try:
            subtotal = limits.estimated_total(subtotal, prompt, completion, cached)
        except ValueError:
            valid_config = False
    stages: dict[str, dict[str, int]] = {}
    seen: set[str] = set()
    for row in receipts:
        event_id = row.get("event_id")
        chat = row.get("chat_id")
        eligible = chat in chat_ids or (allow_isolated_chatless_auxiliary and chat is None
                                       and row.get("execution_kind") == "auxiliary")
        if (not isinstance(event_id, str) or not event_id or event_id in seen or row.get("app_id") != app_id
                or row.get("user_id") != user_id or not eligible):
            continue
        seen.add(event_id)
        stage = str(row.get("workflow_name") or ("auxiliary" if row.get("execution_kind") == "auxiliary" else "unknown"))
        stage_totals = stages.setdefault(stage, {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
                                                 "cached_prompt_tokens": 0})
        if all(type(row.get(key)) is int and row[key] >= 0
               for key in ("prompt_tokens", "completion_tokens", "cached_prompt_tokens")):
            stage_totals["calls"] += 1
            for key in ("prompt_tokens", "completion_tokens", "cached_prompt_tokens"):
                stage_totals[key] += row[key]
    complete = bool(comparison["reconciled"] and valid_config)
    known = bool(comparison["valid_prefix"] and valid_config)
    return {
        "model": limits.model, "pricing_sha256": pricing_sha256, "comparison": comparison,
        "pricing_and_model_match": valid_config, "known_provider_tokens": totals if known else None,
        "known_estimated_usd": subtotal if known else None, "complete_estimated_usd": subtotal if complete else None,
        "receipt_stage_observations": stages,
        "qualification": "gateway observations require separately verified exclusive provider routing",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve")
    serve.add_argument("--port", required=True, type=int)
    serve.add_argument("--max-attempts", required=True, type=int)
    serve.add_argument("--max-output-tokens", required=True, type=int)
    serve.add_argument("--max-usd", required=True, type=float)
    reconcile = commands.add_parser("reconcile")
    reconcile.add_argument("--ledger", required=True, type=Path)
    reconcile.add_argument("--app-id", required=True)
    reconcile.add_argument("--user-id", required=True)
    reconcile.add_argument("--chat-id", action="append", default=[])
    reconcile.add_argument("--allow-isolated-chatless-auxiliary", action="store_true")
    for command in (serve, reconcile):
        command.add_argument("--model", required=True)
        command.add_argument("--pricing-file", required=True, type=Path)
        command.add_argument("--journal", required=True, type=Path)
    args = parser.parse_args()
    rates, pricing_sha256 = pricing_rates(args.pricing_file, args.model)
    if args.command == "reconcile":
        records = [json.loads(line) for line in args.journal.read_text(encoding="utf-8").splitlines()]
        ledger = json.loads(args.ledger.read_text(encoding="utf-8"))
        receipts = ledger.get("events") if isinstance(ledger, dict) else ledger
        if not isinstance(receipts, list) or any(not isinstance(row, dict) for row in records + receipts):
            parser.error("journal and ledger must contain JSON objects")
        limits = Limits(args.model, 1, 1, 1.0, rates["input_per_1m_usd"], rates["cached_input_per_1m_usd"],
                        rates["output_per_1m_usd"])
        report = summarize_reconciliation(records, receipts, limits=limits, pricing_sha256=pricing_sha256,
                                          app_id=args.app_id, user_id=args.user_id, chat_ids=set(args.chat_id),
                                          allow_isolated_chatless_auxiliary=args.allow_isolated_chatless_auxiliary)
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        raise SystemExit(0 if report["complete_estimated_usd"] is not None else 1)
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    limits = Limits(args.model, args.max_attempts, args.max_output_tokens, args.max_usd,
                    rates["input_per_1m_usd"], rates["cached_input_per_1m_usd"], rates["output_per_1m_usd"])
    timeout = httpx.Timeout(90.0, connect=10.0)
    client = httpx.AsyncClient(timeout=timeout, transport=httpx.AsyncHTTPTransport(retries=0), trust_env=False)
    gateway = ProviderGateway(limits=limits, token=os.environ.get("JOURNEY_GATEWAY_TOKEN", ""),
                              upstream_key=os.environ.get("JOURNEY_UPSTREAM_OPENAI_API_KEY", ""),
                              client=client, journal=args.journal, pricing_sha256=pricing_sha256)
    import uvicorn
    uvicorn.run(make_app(gateway), host="127.0.0.1", port=args.port, access_log=False, log_config=None)


if __name__ == "__main__":
    main()
