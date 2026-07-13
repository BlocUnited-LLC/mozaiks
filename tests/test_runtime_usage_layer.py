"""
Runtime usage layer — comprehensive tests for summarize_usage_events,
estimate_token_cost, and ledger helper functions.

These are all pure-Python computations with no external dependencies.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _event(
    *,
    app_id: str = "app-1",
    chat_id: str = "chat-1",
    user_id: str = "user-1",
    workflow_name: str = "AppGenerator",
    prompt_tokens: int = 10,
    completion_tokens: int = 5,
    cached_prompt_tokens: int = 0,
    total_tokens: int | None = None,
    estimated_cost_usd: float = 0.0,
) -> dict:
    return {
        "app_id": app_id,
        "chat_id": chat_id,
        "user_id": user_id,
        "workflow_name": workflow_name,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "cached_prompt_tokens": cached_prompt_tokens,
        "total_tokens": total_tokens if total_tokens is not None else prompt_tokens + completion_tokens,
        "estimated_cost_usd": estimated_cost_usd,
        "event_ts": datetime(2026, 6, 1, tzinfo=UTC),
    }


# ---------------------------------------------------------------------------
# 1. summarize_usage_events — totals
# ---------------------------------------------------------------------------

class TestSummarizeUsageEventsTotals:
    def test_empty_events_returns_zero_totals(self):
        from mozaiksai.core.usage.ledger import summarize_usage_events
        result = summarize_usage_events([], app_id="app-1")
        assert result["totals"]["prompt_tokens"] == 0
        assert result["totals"]["completion_tokens"] == 0
        assert result["totals"]["total_tokens"] == 0
        assert result["totals"]["cached_prompt_tokens"] == 0
        assert result["totals"]["estimated_cost_usd"] == 0.0
        assert result["totals"]["llm_calls"] == 0
        assert result["by_workflow"] == []
        assert result["by_run"] == []

    def test_single_event_totals(self):
        from mozaiksai.core.usage.ledger import summarize_usage_events
        docs = [_event(prompt_tokens=10, completion_tokens=5, estimated_cost_usd=0.03)]
        result = summarize_usage_events(docs, app_id="app-1")
        assert result["totals"]["prompt_tokens"] == 10
        assert result["totals"]["completion_tokens"] == 5
        assert result["totals"]["total_tokens"] == 15
        assert result["totals"]["cached_prompt_tokens"] == 0
        assert result["totals"]["llm_calls"] == 1

    def test_multiple_events_sum_totals(self):
        from mozaiksai.core.usage.ledger import summarize_usage_events
        docs = [
            _event(prompt_tokens=10, completion_tokens=5),
            _event(prompt_tokens=20, completion_tokens=10),
        ]
        result = summarize_usage_events(docs, app_id="app-1")
        assert result["totals"]["prompt_tokens"] == 30
        assert result["totals"]["completion_tokens"] == 15
        assert result["totals"]["total_tokens"] == 45
        assert result["totals"]["cached_prompt_tokens"] == 0
        assert result["totals"]["llm_calls"] == 2

    def test_cached_prompt_tokens_sum_totals(self):
        from mozaiksai.core.usage.ledger import summarize_usage_events
        docs = [
            _event(prompt_tokens=10, completion_tokens=5, cached_prompt_tokens=4),
            _event(prompt_tokens=20, completion_tokens=10, cached_prompt_tokens=6),
        ]
        result = summarize_usage_events(docs, app_id="app-1")
        assert result["totals"]["cached_prompt_tokens"] == 10
        assert result["by_workflow"][0]["cached_prompt_tokens"] == 10
        assert result["by_run"][0]["cached_prompt_tokens"] == 10

    def test_cost_accumulation(self):
        from mozaiksai.core.usage.ledger import summarize_usage_events
        docs = [
            _event(estimated_cost_usd=0.05),
            _event(estimated_cost_usd=0.03),
        ]
        result = summarize_usage_events(docs, app_id="app-1")
        assert abs(result["totals"]["estimated_cost_usd"] - 0.08) < 1e-9


# ---------------------------------------------------------------------------
# 2. summarize_usage_events — by_workflow
# ---------------------------------------------------------------------------

class TestSummarizeUsageEventsByWorkflow:
    def test_single_workflow_group(self):
        from mozaiksai.core.usage.ledger import summarize_usage_events
        docs = [
            _event(workflow_name="AppGenerator", prompt_tokens=10, completion_tokens=5),
            _event(workflow_name="AppGenerator", prompt_tokens=20, completion_tokens=10),
        ]
        result = summarize_usage_events(docs, app_id="app-1")
        assert len(result["by_workflow"]) == 1
        wf = result["by_workflow"][0]
        assert wf["workflow_name"] == "AppGenerator"
        assert wf["prompt_tokens"] == 30
        assert wf["llm_calls"] == 2

    def test_multiple_workflows_sorted_by_total_tokens_descending(self):
        from mozaiksai.core.usage.ledger import summarize_usage_events
        docs = [
            _event(workflow_name="SmallWorkflow", prompt_tokens=5, completion_tokens=2),  # total=7
            _event(workflow_name="BigWorkflow", prompt_tokens=100, completion_tokens=50),  # total=150
            _event(workflow_name="MediumWorkflow", prompt_tokens=30, completion_tokens=20),  # total=50
        ]
        result = summarize_usage_events(docs, app_id="app-1")
        names = [wf["workflow_name"] for wf in result["by_workflow"]]
        assert names == ["BigWorkflow", "MediumWorkflow", "SmallWorkflow"]

    def test_workflow_run_count_deduplicates_chat_ids(self):
        from mozaiksai.core.usage.ledger import summarize_usage_events
        # Three events for AppGenerator — chat-1 appears twice, chat-2 once → 2 runs
        docs = [
            _event(workflow_name="AppGenerator", chat_id="chat-1"),
            _event(workflow_name="AppGenerator", chat_id="chat-1"),
            _event(workflow_name="AppGenerator", chat_id="chat-2"),
        ]
        result = summarize_usage_events(docs, app_id="app-1")
        wf = result["by_workflow"][0]
        assert wf["runs"] == 2

    def test_event_without_chat_id_not_counted_in_runs(self):
        from mozaiksai.core.usage.ledger import summarize_usage_events
        docs = [
            {**_event(), "chat_id": ""},
            {**_event(), "chat_id": None},
        ]
        result = summarize_usage_events(docs, app_id="app-1")
        wf = result["by_workflow"][0]
        assert wf["runs"] == 0


# ---------------------------------------------------------------------------
# 3. summarize_usage_events — by_run
# ---------------------------------------------------------------------------

class TestSummarizeUsageEventsByRun:
    def test_events_grouped_by_chat_id(self):
        from mozaiksai.core.usage.ledger import summarize_usage_events
        docs = [
            _event(chat_id="chat-1", prompt_tokens=10, completion_tokens=5),
            _event(chat_id="chat-1", prompt_tokens=20, completion_tokens=10),
            _event(chat_id="chat-2", prompt_tokens=100, completion_tokens=50),
        ]
        result = summarize_usage_events(docs, app_id="app-1")
        assert len(result["by_run"]) == 2
        chat_1 = next(r for r in result["by_run"] if r["chat_id"] == "chat-1")
        assert chat_1["prompt_tokens"] == 30
        assert chat_1["llm_calls"] == 2

    def test_by_run_sorted_by_total_tokens_descending(self):
        from mozaiksai.core.usage.ledger import summarize_usage_events
        docs = [
            _event(chat_id="small-run", prompt_tokens=2, completion_tokens=1),    # total=3
            _event(chat_id="big-run", prompt_tokens=100, completion_tokens=50),  # total=150
        ]
        result = summarize_usage_events(docs, app_id="app-1")
        assert result["by_run"][0]["chat_id"] == "big-run"
        assert result["by_run"][1]["chat_id"] == "small-run"

    def test_events_without_chat_id_excluded_from_by_run(self):
        from mozaiksai.core.usage.ledger import summarize_usage_events
        docs = [{**_event(), "chat_id": None}]
        result = summarize_usage_events(docs, app_id="app-1")
        assert result["by_run"] == []

    def test_result_metadata_fields(self):
        from mozaiksai.core.usage.ledger import summarize_usage_events
        result = summarize_usage_events([], app_id="app-x", user_id="user-y")
        assert result["app_id"] == "app-x"
        assert result["user_id"] == "user-y"
        assert result["source"] == "runtime_usage_events"


# ---------------------------------------------------------------------------
# 4. estimate_token_cost — explicit cost path
# ---------------------------------------------------------------------------

class TestEstimateTokenCostExplicit:
    def test_explicit_positive_cost_returned_directly(self):
        from mozaiksai.core.usage.pricing import estimate_token_cost
        result = estimate_token_cost(
            model_name="gpt-4o",
            prompt_tokens=100,
            completion_tokens=50,
            explicit_cost_usd=0.42,
        )
        assert result.estimated_cost_usd == 0.42
        assert result.cost_source == "provided"

    def test_explicit_zero_cost_is_valid(self):
        from mozaiksai.core.usage.pricing import estimate_token_cost
        result = estimate_token_cost(
            model_name="gpt-4o",
            prompt_tokens=100,
            completion_tokens=50,
            explicit_cost_usd=0.0,
        )
        assert result.estimated_cost_usd == 0.0
        assert result.cost_source == "provided"

    def test_explicit_negative_cost_falls_through(self):
        from mozaiksai.core.usage.pricing import estimate_token_cost
        # Negative cost → invalid → falls through to env-var path
        result = estimate_token_cost(
            model_name=None,
            prompt_tokens=0,
            completion_tokens=0,
            explicit_cost_usd=-1.0,
        )
        assert result.cost_source in ("not_configured", "estimated")

    def test_explicit_none_falls_through(self):
        from mozaiksai.core.usage.pricing import estimate_token_cost
        result = estimate_token_cost(
            model_name=None,
            prompt_tokens=0,
            completion_tokens=0,
            explicit_cost_usd=None,
        )
        assert result.cost_source in ("not_configured", "estimated")

    def test_explicit_non_numeric_string_falls_through(self):
        from mozaiksai.core.usage.pricing import estimate_token_cost
        result = estimate_token_cost(
            model_name=None,
            prompt_tokens=0,
            completion_tokens=0,
            explicit_cost_usd="not-a-number",
        )
        assert result.cost_source in ("not_configured", "estimated")


# ---------------------------------------------------------------------------
# 5. estimate_token_cost — rate-based path
# ---------------------------------------------------------------------------

class TestEstimateTokenCostRateBased:
    def test_no_env_vars_returns_not_configured(self, monkeypatch):
        from mozaiksai.core.usage import pricing
        monkeypatch.delenv("MOZAIKS_USAGE_INPUT_PER_1K_USD", raising=False)
        monkeypatch.delenv("MOZAIKS_USAGE_OUTPUT_PER_1K_USD", raising=False)
        result = pricing.estimate_token_cost(
            model_name=None, prompt_tokens=100, completion_tokens=50
        )
        assert result.estimated_cost_usd == 0.0
        assert result.cost_source == "not_configured"

    def test_input_rate_only(self, monkeypatch):
        from mozaiksai.core.usage import pricing
        monkeypatch.setenv("MOZAIKS_USAGE_INPUT_PER_1K_USD", "0.01")
        monkeypatch.delenv("MOZAIKS_USAGE_OUTPUT_PER_1K_USD", raising=False)
        result = pricing.estimate_token_cost(
            model_name=None, prompt_tokens=1000, completion_tokens=0
        )
        assert abs(result.estimated_cost_usd - 0.01) < 1e-9
        assert result.cost_source == "estimated"

    def test_output_rate_only(self, monkeypatch):
        from mozaiksai.core.usage import pricing
        monkeypatch.delenv("MOZAIKS_USAGE_INPUT_PER_1K_USD", raising=False)
        monkeypatch.setenv("MOZAIKS_USAGE_OUTPUT_PER_1K_USD", "0.02")
        result = pricing.estimate_token_cost(
            model_name=None, prompt_tokens=0, completion_tokens=1000
        )
        assert abs(result.estimated_cost_usd - 0.02) < 1e-9

    def test_both_rates_combined(self, monkeypatch):
        from mozaiksai.core.usage import pricing
        monkeypatch.setenv("MOZAIKS_USAGE_INPUT_PER_1K_USD", "0.01")
        monkeypatch.setenv("MOZAIKS_USAGE_OUTPUT_PER_1K_USD", "0.02")
        result = pricing.estimate_token_cost(
            model_name=None, prompt_tokens=1000, completion_tokens=1000
        )
        # 1000/1000 * 0.01 + 1000/1000 * 0.02 = 0.03
        assert abs(result.estimated_cost_usd - 0.03) < 1e-9

    def test_model_specific_env_var_takes_precedence(self, monkeypatch):
        from mozaiksai.core.usage import pricing
        monkeypatch.setenv("MOZAIKS_USAGE_INPUT_PER_1K_USD", "0.01")
        monkeypatch.setenv("MOZAIKS_USAGE_GPT_4O_INPUT_PER_1K_USD", "0.05")
        result = pricing.estimate_token_cost(
            model_name="gpt-4o", prompt_tokens=1000, completion_tokens=0
        )
        assert abs(result.estimated_cost_usd - 0.05) < 1e-9

    def test_negative_tokens_treated_as_zero(self, monkeypatch):
        from mozaiksai.core.usage import pricing
        monkeypatch.setenv("MOZAIKS_USAGE_INPUT_PER_1K_USD", "0.01")
        result = pricing.estimate_token_cost(
            model_name=None, prompt_tokens=-100, completion_tokens=-50
        )
        assert result.estimated_cost_usd == 0.0


# ---------------------------------------------------------------------------
# 6. Ledger helper functions
# ---------------------------------------------------------------------------

class TestLedgerHelpers:
    def test_int_value_none_returns_zero(self):
        from mozaiksai.core.usage.ledger import _int_value
        assert _int_value(None) == 0

    def test_int_value_negative_returns_zero(self):
        from mozaiksai.core.usage.ledger import _int_value
        assert _int_value(-5) == 0

    def test_int_value_valid_int(self):
        from mozaiksai.core.usage.ledger import _int_value
        assert _int_value(42) == 42

    def test_int_value_string_parses(self):
        from mozaiksai.core.usage.ledger import _int_value
        assert _int_value("17") == 17

    def test_int_value_invalid_string_returns_zero(self):
        from mozaiksai.core.usage.ledger import _int_value
        assert _int_value("not-a-number") == 0

    def test_float_value_none_returns_zero(self):
        from mozaiksai.core.usage.ledger import _float_value
        assert _float_value(None) == 0.0

    def test_float_value_negative_returns_zero(self):
        from mozaiksai.core.usage.ledger import _float_value
        assert _float_value(-1.5) == 0.0

    def test_float_value_valid(self):
        from mozaiksai.core.usage.ledger import _float_value
        assert abs(_float_value(3.14) - 3.14) < 1e-9

    def test_text_none_returns_none(self):
        from mozaiksai.core.usage.ledger import _text
        assert _text(None) is None

    def test_text_empty_returns_none(self):
        from mozaiksai.core.usage.ledger import _text
        assert _text("") is None

    def test_text_whitespace_returns_none(self):
        from mozaiksai.core.usage.ledger import _text
        assert _text("   ") is None

    def test_text_strips_whitespace(self):
        from mozaiksai.core.usage.ledger import _text
        assert _text("  hello  ") == "hello"


# ---------------------------------------------------------------------------
# 7. record_usage_delta guard conditions (pure validation logic)
# ---------------------------------------------------------------------------

class TestRecordUsageDeltaGuards:
    """
    Tests the guard conditions in record_usage_delta using a patched collection
    to avoid needing a live MongoDB connection.
    """

    @pytest.mark.asyncio
    async def test_missing_app_id_skips_write(self, monkeypatch):
        from mozaiksai.core.usage import ledger as ledger_mod
        writes = []

        async def fake_coll(self):
            class _FakeColl:
                async def update_one(self, *a, **k):
                    writes.append(("update_one", a, k))
                async def create_index(self, *a, **k):
                    pass
            return _FakeColl()

        monkeypatch.setattr(ledger_mod.RuntimeUsageLedger, "_coll", fake_coll)
        ledger = ledger_mod.RuntimeUsageLedger()
        await ledger.record_usage_delta({
            "app_id": "",
            "chat_id": "chat-1",
            "workflow_name": "AppGenerator",
            "prompt_tokens": 10,
        })
        assert writes == []

    @pytest.mark.asyncio
    async def test_missing_chat_id_skips_write(self, monkeypatch):
        from mozaiksai.core.usage import ledger as ledger_mod

        writes = []

        async def fake_coll(self):
            class _FakeColl:
                async def update_one(self, *a, **k):
                    writes.append(("update_one", a, k))
                async def create_index(self, *a, **k):
                    pass
            return _FakeColl()

        monkeypatch.setattr(ledger_mod.RuntimeUsageLedger, "_coll", fake_coll)
        ledger = ledger_mod.RuntimeUsageLedger()
        await ledger.record_usage_delta({
            "app_id": "app-1",
            "chat_id": None,
            "workflow_name": "AppGenerator",
            "prompt_tokens": 10,
        })
        assert writes == []

    @pytest.mark.asyncio
    async def test_missing_workflow_name_skips_write(self, monkeypatch):
        from mozaiksai.core.usage import ledger as ledger_mod

        writes = []

        async def fake_coll(self):
            class _FakeColl:
                async def update_one(self, *a, **k):
                    writes.append(("update_one", a, k))
                async def create_index(self, *a, **k):
                    pass
            return _FakeColl()

        monkeypatch.setattr(ledger_mod.RuntimeUsageLedger, "_coll", fake_coll)
        ledger = ledger_mod.RuntimeUsageLedger()
        await ledger.record_usage_delta({
            "app_id": "app-1",
            "chat_id": "chat-1",
            "workflow_name": "",
            "prompt_tokens": 10,
        })
        assert writes == []

    @pytest.mark.asyncio
    async def test_input_tokens_alias_accepted(self, monkeypatch):
        from mozaiksai.core.usage import ledger as ledger_mod

        inserted_docs = []

        async def fake_coll(self):
            class _FakeColl:
                async def update_one(self, query, update, **kw):
                    inserted_docs.append(update["$setOnInsert"])
                async def create_index(self, *a, **k):
                    pass
            return _FakeColl()

        monkeypatch.setattr(ledger_mod.RuntimeUsageLedger, "_coll", fake_coll)
        ledger = ledger_mod.RuntimeUsageLedger()
        await ledger.record_usage_delta({
            "app_id": "app-1",
            "chat_id": "chat-1",
            "workflow_name": "AppGenerator",
            "input_tokens": 8,    # alias for prompt_tokens
            "output_tokens": 4,   # alias for completion_tokens
        })
        assert len(inserted_docs) == 1
        assert inserted_docs[0]["prompt_tokens"] == 8
        assert inserted_docs[0]["completion_tokens"] == 4

    @pytest.mark.asyncio
    async def test_valid_payload_writes_to_collection(self, monkeypatch):
        from mozaiksai.core.usage import ledger as ledger_mod

        inserted_docs = []

        async def fake_coll(self):
            class _FakeColl:
                async def update_one(self, query, update, **kw):
                    inserted_docs.append(update["$setOnInsert"])
                async def create_index(self, *a, **k):
                    pass
            return _FakeColl()

        monkeypatch.setattr(ledger_mod.RuntimeUsageLedger, "_coll", fake_coll)
        ledger = ledger_mod.RuntimeUsageLedger()
        await ledger.record_usage_delta({
            "app_id": "app-1",
            "chat_id": "chat-1",
            "tenant_id": "tenant-1",
            "workspace_id": "workspace-1",
            "workflow_name": "AppGenerator",
            "prompt_tokens": 12,
            "completion_tokens": 8,
        })
        assert len(inserted_docs) == 1
        doc = inserted_docs[0]
        assert doc["app_id"] == "app-1"
        assert doc["chat_id"] == "chat-1"
        assert doc["tenant_id"] == "tenant-1"
        assert doc["workspace_id"] == "workspace-1"
        assert doc["prompt_tokens"] == 12
        assert doc["completion_tokens"] == 8
        assert doc["total_tokens"] == 20
