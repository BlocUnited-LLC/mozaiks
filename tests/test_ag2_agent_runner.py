"""Tests for AG2StructuredAgentRunner.

Determinism requirements verified here
---------------------------------------
- retry_count is caller-controlled and passed exclusively to RetryMiddleware.
- schema_validation_retries is caller-controlled and passed exclusively to
  reply.content(retries=...).
- The two retry parameters are independently configurable and cover distinct
  failure layers (provider/execution vs schema validation).
- Retries are bounded; no unbounded loop is possible.
- Empty output raises regardless of retry configuration.
- Mozaiks Pydantic model_validate runs after AG2 content() resolution.
- Sensitive provider errors are not re-wrapped or re-exposed by the runner.
"""
from __future__ import annotations

from typing import Any

import pytest
from ag2.middleware.builtin import RetryMiddleware
from pydantic import BaseModel, ConfigDict
from pydantic import ValidationError as PydanticValidationError

from mozaiksai.core.adapters.ag2_agent_runner import AG2StructuredAgentRunner

# ---------------------------------------------------------------------------
# Shared response model
# ---------------------------------------------------------------------------

class _RunnerResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str


# ---------------------------------------------------------------------------
# Fake infrastructure
# ---------------------------------------------------------------------------

class _SchemaValidationError(Exception):
    """Simulates AG2's internal ValidationError raised by reply.content() when
    schema validation fails.  AG2 source (ag2==1.0.1, AgentReply.content):

        try:
            return await schema.validate(current.body, ...)
        except ValidationError as e:
            if attempt > max_retries:
                raise e
            current = await current.ask(...)

    This fake is raised by _FakeReply.content() to simulate that path.
    """


class _FakeReply:
    """Deterministic reply stand-in.

    ``outcomes`` is an ordered list of values to produce on successive
    validation attempts inside content().  Each entry is either a plain value
    to return or a _SchemaValidationError instance to raise.

    This mirrors AG2's actual retry loop in AgentReply.content():
    - attempt 1..max_retries: on ValidationError, continue to next outcome.
    - attempt max_retries+1: on ValidationError, raise immediately.

    content() records the ``retries`` kwarg it received so tests can assert
    the runner passed the correct value.
    """

    def __init__(self, *outcomes: Any) -> None:
        self._outcomes = list(outcomes)
        self.retries_received: int | None = None

    async def content(self, *, retries: int = 0) -> Any:
        self.retries_received = retries
        max_retries = max(retries, 0)
        attempt = 0
        for outcome in self._outcomes:
            attempt += 1
            if isinstance(outcome, _SchemaValidationError):
                if attempt > max_retries:
                    raise outcome
                # continue to next outcome (simulates AG2 ask() retry turn)
            else:
                return outcome
        # Should never be reached if outcomes are specified correctly in tests.
        raise AssertionError(  # pragma: no cover
            f"_FakeReply ran out of outcomes after {attempt} attempt(s)"
        )


class _FakeAgent:
    """Agent stand-in that returns a pre-configured _FakeReply on ask()."""

    def __init__(
        self,
        system_prompt: str,
        llm_config: dict[str, Any],
        reply: _FakeReply,
    ) -> None:
        self.system_prompt = system_prompt
        self.llm_config = llm_config
        self.reply = reply
        self.calls: list[dict[str, Any]] = []

    async def ask(self, user_prompt: str, **kwargs: Any) -> _FakeReply:
        self.calls.append({"user_prompt": user_prompt, **kwargs})
        return self.reply


def _make_runner(reply: _FakeReply) -> tuple[AG2StructuredAgentRunner, list[_FakeAgent]]:
    """Helper: build a runner whose factory captures the created agent."""
    created: list[_FakeAgent] = []

    def factory(system_prompt: str, llm_config: dict[str, Any]) -> _FakeAgent:
        agent = _FakeAgent(system_prompt, llm_config, reply)
        created.append(agent)
        return agent

    return AG2StructuredAgentRunner(agent_factory=factory, stream_factory=lambda: "stream"), created


# ---------------------------------------------------------------------------
# Existing behaviour (preserved)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ag2_structured_agent_runner_calls_agent_with_schema_and_defaults() -> None:
    """Original contract: agent.ask() receives stream, response_schema, middleware, observers."""
    reply = _FakeReply(_RunnerResponse(status="ok"))
    runner, created = _make_runner(reply)

    result = await runner.run(
        agent_name="ControlPlaneCheckpoint",
        system_prompt="system",
        user_prompt="user",
        llm_config={"model": "gpt-test", "temperature": 0.0},
        response_schema=_RunnerResponse,
    )

    assert result == _RunnerResponse(status="ok")
    assert len(created) == 1
    assert created[0].system_prompt == "system"
    assert created[0].llm_config == {"model": "gpt-test", "temperature": 0.0}
    assert created[0].calls[0]["user_prompt"] == "user"
    assert created[0].calls[0]["stream"] == "stream"
    assert created[0].calls[0]["response_schema"] is _RunnerResponse
    assert created[0].calls[0]["middleware"]
    assert created[0].calls[0]["observers"]


@pytest.mark.asyncio
async def test_ag2_structured_agent_runner_validates_dict_result() -> None:
    """Dict returned by content() is validated into the Pydantic model."""
    reply = _FakeReply({"status": "ok"})
    runner, _ = _make_runner(reply)

    result = await runner.run(
        agent_name="ControlPlaneCheckpoint",
        system_prompt="system",
        user_prompt="user",
        llm_config={},
        response_schema=_RunnerResponse,
    )

    assert result == _RunnerResponse(status="ok")


# ---------------------------------------------------------------------------
# Schema validation retry tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_valid_first_response_succeeds_without_retry() -> None:
    """A valid first response resolves immediately; no retry turn is triggered."""
    reply = _FakeReply(_RunnerResponse(status="first"))
    runner, _ = _make_runner(reply)

    result = await runner.run(
        agent_name="Agent",
        system_prompt="s",
        user_prompt="u",
        llm_config={},
        response_schema=_RunnerResponse,
        schema_validation_retries=2,
    )

    assert result == _RunnerResponse(status="first")
    # content() was called once with the configured retries value
    assert reply.retries_received == 2


@pytest.mark.asyncio
async def test_schema_validation_retry_is_triggered_on_invalid_output() -> None:
    """Invalid first response triggers a retry; corrected second response succeeds."""
    error = _SchemaValidationError("model returned wrong schema")
    reply = _FakeReply(error, _RunnerResponse(status="corrected"))
    runner, _ = _make_runner(reply)

    result = await runner.run(
        agent_name="Agent",
        system_prompt="s",
        user_prompt="u",
        llm_config={},
        response_schema=_RunnerResponse,
        schema_validation_retries=1,
    )

    assert result == _RunnerResponse(status="corrected")


@pytest.mark.asyncio
async def test_corrected_retry_output_succeeds() -> None:
    """Two consecutive invalid responses, then a valid one on the third attempt."""
    e1 = _SchemaValidationError("first failure")
    e2 = _SchemaValidationError("second failure")
    reply = _FakeReply(e1, e2, _RunnerResponse(status="third"))
    runner, _ = _make_runner(reply)

    result = await runner.run(
        agent_name="Agent",
        system_prompt="s",
        user_prompt="u",
        llm_config={},
        response_schema=_RunnerResponse,
        schema_validation_retries=2,
    )

    assert result == _RunnerResponse(status="third")


@pytest.mark.asyncio
async def test_schema_validation_retries_stop_at_configured_limit() -> None:
    """Retries stop exactly at schema_validation_retries; the error propagates."""
    error = _SchemaValidationError("persistent schema error")
    # Two errors exceeds schema_validation_retries=1 (max 1 retry → 2 attempts).
    reply = _FakeReply(error, error)
    runner, _ = _make_runner(reply)

    with pytest.raises(_SchemaValidationError):
        await runner.run(
            agent_name="Agent",
            system_prompt="s",
            user_prompt="u",
            llm_config={},
            response_schema=_RunnerResponse,
            schema_validation_retries=1,
        )


@pytest.mark.asyncio
async def test_zero_schema_validation_retries_performs_no_correction_turn() -> None:
    """schema_validation_retries=0: first failure raises immediately, no retry turn."""
    error = _SchemaValidationError("immediate failure")
    reply = _FakeReply(error)
    runner, _ = _make_runner(reply)

    with pytest.raises(_SchemaValidationError):
        await runner.run(
            agent_name="Agent",
            system_prompt="s",
            user_prompt="u",
            llm_config={},
            response_schema=_RunnerResponse,
            schema_validation_retries=0,
        )

    # content() was called with retries=0
    assert reply.retries_received == 0


@pytest.mark.asyncio
async def test_empty_output_raises_regardless_of_retry_config() -> None:
    """None from content() raises ValueError even when retries are configured."""
    reply = _FakeReply(None)
    runner, _ = _make_runner(reply)

    with pytest.raises(ValueError, match="ControlPlaneCheckpoint returned an empty response"):
        await runner.run(
            agent_name="ControlPlaneCheckpoint",
            system_prompt="s",
            user_prompt="u",
            llm_config={},
            response_schema=_RunnerResponse,
            schema_validation_retries=3,
        )


@pytest.mark.asyncio
async def test_dict_result_validated_into_pydantic_model() -> None:
    """A raw dict returned by content() is coerced into the declared response model."""
    reply = _FakeReply({"status": "from_dict"})
    runner, _ = _make_runner(reply)

    result = await runner.run(
        agent_name="Agent",
        system_prompt="s",
        user_prompt="u",
        llm_config={},
        response_schema=_RunnerResponse,
    )

    assert isinstance(result, _RunnerResponse)
    assert result.status == "from_dict"


@pytest.mark.asyncio
async def test_already_instantiated_model_passes_through() -> None:
    """A content() result that is already the target model type is returned as-is."""
    instance = _RunnerResponse(status="already_model")
    reply = _FakeReply(instance)
    runner, _ = _make_runner(reply)

    result = await runner.run(
        agent_name="Agent",
        system_prompt="s",
        user_prompt="u",
        llm_config={},
        response_schema=_RunnerResponse,
    )

    assert result is instance


# ---------------------------------------------------------------------------
# Distinct responsibility tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_retry_middleware_and_content_retries_are_configured_separately() -> None:
    """RetryMiddleware receives retry_count; content() receives schema_validation_retries.

    Both parameters are caller-controlled integers.  They are independently
    configurable and cover distinct failure layers:
    - retry_count → RetryMiddleware → provider/execution failures
    - schema_validation_retries → reply.content(retries=...) → schema validation failures
    """
    reply = _FakeReply(_RunnerResponse(status="ok"))
    runner, created = _make_runner(reply)

    await runner.run(
        agent_name="Agent",
        system_prompt="s",
        user_prompt="u",
        llm_config={},
        response_schema=_RunnerResponse,
        retry_count=3,
        schema_validation_retries=5,
    )

    # Verify RetryMiddleware received retry_count=3
    call = created[0].calls[0]
    middleware_list = call["middleware"]
    assert len(middleware_list) == 1
    retry_mw = middleware_list[0]
    assert isinstance(retry_mw, RetryMiddleware)
    assert retry_mw._max_retries == 3  # noqa: SLF001

    # Verify content() received schema_validation_retries=5
    assert reply.retries_received == 5


@pytest.mark.asyncio
async def test_schema_validation_retries_passed_correctly_with_default_retry_count() -> None:
    """Default retry_count=2 does not affect schema_validation_retries."""
    reply = _FakeReply(_RunnerResponse(status="ok"))
    runner, created = _make_runner(reply)

    await runner.run(
        agent_name="Agent",
        system_prompt="s",
        user_prompt="u",
        llm_config={},
        response_schema=_RunnerResponse,
        schema_validation_retries=4,
    )

    call = created[0].calls[0]
    retry_mw = call["middleware"][0]
    assert retry_mw._max_retries == 2  # default retry_count  # noqa: SLF001
    assert reply.retries_received == 4  # schema_validation_retries is independent


@pytest.mark.asyncio
async def test_no_retry_becomes_unbounded() -> None:
    """Retries are bounded: schema_validation_retries=N yields at most N+1 total attempts."""
    n = 2
    errors = [_SchemaValidationError(f"fail {i}") for i in range(n + 1)]
    # n+1 errors with retries=n → the (n+1)th attempt exceeds max_retries → raises
    reply = _FakeReply(*errors)
    runner, _ = _make_runner(reply)

    with pytest.raises(_SchemaValidationError):
        await runner.run(
            agent_name="Agent",
            system_prompt="s",
            user_prompt="u",
            llm_config={},
            response_schema=_RunnerResponse,
            schema_validation_retries=n,
        )

    # Exactly n+1 validation attempts were made before raising
    # (Verified indirectly: if it were unbounded, _FakeReply.content() would
    # have raised AssertionError from exhausting outcomes, not _SchemaValidationError.)


@pytest.mark.asyncio
async def test_provider_error_not_exposed_through_schema_validation_path() -> None:
    """Schema validation errors from content() propagate directly without wrapping.

    The runner does not inspect, log, or re-raise provider error internals.
    Callers receive the exact exception type that AG2's content() raises on
    exhausted validation retries — no additional wrapping that could leak
    sensitive prompt or provider details.
    """
    original_error = _SchemaValidationError("schema parse failure")
    reply = _FakeReply(original_error)
    runner, _ = _make_runner(reply)

    with pytest.raises(_SchemaValidationError) as exc_info:
        await runner.run(
            agent_name="Agent",
            system_prompt="s",
            user_prompt="u",
            llm_config={},
            response_schema=_RunnerResponse,
            schema_validation_retries=0,
        )

    # The raised exception is the original, unwrapped object.
    assert exc_info.value is original_error


@pytest.mark.asyncio
async def test_invalid_dict_result_raises_pydantic_validation_error() -> None:
    """A dict that fails Pydantic model_validate raises PydanticValidationError.

    Mozaiks Pydantic validation runs after AG2 content() and enforces the
    strict schema (extra='forbid').  This confirms Mozaiks remains the final
    canonical validator even after AG2 accepts the output.
    """
    reply = _FakeReply({"status": "ok", "unexpected_field": "bad"})
    runner, _ = _make_runner(reply)

    with pytest.raises(PydanticValidationError):
        await runner.run(
            agent_name="Agent",
            system_prompt="s",
            user_prompt="u",
            llm_config={},
            response_schema=_RunnerResponse,
        )
