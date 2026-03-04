# ==============================================================================
# FILE: cost_tracker.py
# DESCRIPTION: Token cost calculation and budget management utilities.
#
# This module provides:
# - Model-to-pricing mapping (configurable via JSON or environment)
# - Cost calculation from token counts
# - Budget tracking and threshold warnings
# - Event emission for cost/budget events (no enforcement, measurement only)
#
# Design Principles:
# - MEASUREMENT ONLY: This module calculates and emits cost data but does NOT
#   enforce spending limits. Enforcement belongs in the platform layer.
# - CONFIGURABLE: Pricing data is hot-loadable from JSON or environment.
# - MULTI-TENANT: All operations require app_id/user_id context.
#
# Environment Variables:
# - COST_TRACKING_ENABLED: Enable cost calculation (default: "true")
# - MODEL_PRICING_JSON: Path to JSON file with model pricing (optional)
# - DEFAULT_INPUT_COST_PER_1K: Default cost per 1K input tokens (default: 0.001)
# - DEFAULT_OUTPUT_COST_PER_1K: Default cost per 1K output tokens (default: 0.002)
# ==============================================================================

from __future__ import annotations

import json
import os
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Callable, Awaitable
import asyncio

from mozaiksai.core.events.unified_event_dispatcher import get_event_dispatcher

logger = logging.getLogger("core.observability.cost_tracker")

# ---------------------------------------------------------------------------
# Event Types (for platform consumption)
# ---------------------------------------------------------------------------

COST_CALCULATED_EVENT = "chat.cost_calculated"
BUDGET_WARNING_EVENT = "budget.warning"
BUDGET_EXCEEDED_EVENT = "budget.exceeded"

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def _is_cost_tracking_enabled() -> bool:
    """Check if cost tracking is enabled."""
    value = os.getenv("COST_TRACKING_ENABLED", "true").strip().lower()
    return value not in {"0", "false", "off", "no", "disabled"}


def _get_default_input_cost() -> float:
    """Default cost per 1K input tokens."""
    try:
        return float(os.getenv("DEFAULT_INPUT_COST_PER_1K", "0.001"))
    except ValueError:
        return 0.001


def _get_default_output_cost() -> float:
    """Default cost per 1K output tokens."""
    try:
        return float(os.getenv("DEFAULT_OUTPUT_COST_PER_1K", "0.002"))
    except ValueError:
        return 0.002


# ---------------------------------------------------------------------------
# Model Pricing Data
# ---------------------------------------------------------------------------

@dataclass
class ModelPricing:
    """Pricing information for a specific model."""
    model_name: str
    input_cost_per_1k: float  # Cost per 1,000 input/prompt tokens
    output_cost_per_1k: float  # Cost per 1,000 output/completion tokens
    context_window: int = 128000  # Max context window size
    provider: str = "openai"  # Provider identifier

    def calculate_cost(self, prompt_tokens: int, completion_tokens: int) -> float:
        """Calculate total cost for given token counts."""
        input_cost = (prompt_tokens / 1000.0) * self.input_cost_per_1k
        output_cost = (completion_tokens / 1000.0) * self.output_cost_per_1k
        return round(input_cost + output_cost, 6)


# Default pricing data (as of early 2026 - update periodically)
# These are approximate and should be overridden via MODEL_PRICING_JSON for accuracy
DEFAULT_MODEL_PRICING: Dict[str, ModelPricing] = {
    # OpenAI GPT-4 family
    "gpt-4o": ModelPricing("gpt-4o", 0.005, 0.015, 128000, "openai"),
    "gpt-4o-mini": ModelPricing("gpt-4o-mini", 0.00015, 0.0006, 128000, "openai"),
    "gpt-4-turbo": ModelPricing("gpt-4-turbo", 0.01, 0.03, 128000, "openai"),
    "gpt-4": ModelPricing("gpt-4", 0.03, 0.06, 8192, "openai"),
    "gpt-3.5-turbo": ModelPricing("gpt-3.5-turbo", 0.0005, 0.0015, 16385, "openai"),
    # Anthropic Claude family
    "claude-3-opus": ModelPricing("claude-3-opus", 0.015, 0.075, 200000, "anthropic"),
    "claude-3-sonnet": ModelPricing("claude-3-sonnet", 0.003, 0.015, 200000, "anthropic"),
    "claude-3-haiku": ModelPricing("claude-3-haiku", 0.00025, 0.00125, 200000, "anthropic"),
    "claude-3.5-sonnet": ModelPricing("claude-3.5-sonnet", 0.003, 0.015, 200000, "anthropic"),
    # Azure OpenAI (typically matches OpenAI pricing)
    "gpt-4o-azure": ModelPricing("gpt-4o-azure", 0.005, 0.015, 128000, "azure"),
}

# Runtime pricing cache (loaded from JSON if configured)
_pricing_cache: Optional[Dict[str, ModelPricing]] = None


def _load_pricing_from_json(path: str) -> Dict[str, ModelPricing]:
    """Load model pricing from a JSON file.

    Expected JSON format:
    {
        "models": {
            "gpt-4o": {
                "input_cost_per_1k": 0.005,
                "output_cost_per_1k": 0.015,
                "context_window": 128000,
                "provider": "openai"
            },
            ...
        }
    }
    """
    pricing = {}
    try:
        with open(path, "r") as f:
            data = json.load(f)

        models = data.get("models", {})
        for name, info in models.items():
            pricing[name] = ModelPricing(
                model_name=name,
                input_cost_per_1k=float(info.get("input_cost_per_1k", _get_default_input_cost())),
                output_cost_per_1k=float(info.get("output_cost_per_1k", _get_default_output_cost())),
                context_window=int(info.get("context_window", 128000)),
                provider=str(info.get("provider", "unknown")),
            )
        logger.info(f"Loaded pricing for {len(pricing)} models from {path}")
    except Exception as e:
        logger.warning(f"Failed to load pricing from {path}: {e}")

    return pricing


def get_model_pricing(model_name: Optional[str]) -> ModelPricing:
    """Get pricing information for a model.

    Falls back to default pricing if model is unknown.

    Args:
        model_name: The model identifier (e.g., "gpt-4o", "claude-3-sonnet")

    Returns:
        ModelPricing instance with cost rates.
    """
    global _pricing_cache

    # Lazy-load from JSON if configured
    if _pricing_cache is None:
        json_path = os.getenv("MODEL_PRICING_JSON", "").strip()
        if json_path and Path(json_path).exists():
            _pricing_cache = _load_pricing_from_json(json_path)
        else:
            _pricing_cache = {}

    # Normalize model name for lookup
    if model_name:
        normalized = model_name.lower().strip()

        # Check cache first, then defaults
        if normalized in _pricing_cache:
            return _pricing_cache[normalized]
        if normalized in DEFAULT_MODEL_PRICING:
            return DEFAULT_MODEL_PRICING[normalized]

        # Partial match (e.g., "gpt-4o-2024-05-01" -> "gpt-4o")
        for key in list(_pricing_cache.keys()) + list(DEFAULT_MODEL_PRICING.keys()):
            if normalized.startswith(key) or key in normalized:
                return _pricing_cache.get(key) or DEFAULT_MODEL_PRICING[key]

    # Return default pricing
    return ModelPricing(
        model_name=model_name or "unknown",
        input_cost_per_1k=_get_default_input_cost(),
        output_cost_per_1k=_get_default_output_cost(),
    )


# ---------------------------------------------------------------------------
# Cost Calculation
# ---------------------------------------------------------------------------

@dataclass
class CostResult:
    """Result of a cost calculation."""
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    input_cost_usd: float
    output_cost_usd: float
    total_cost_usd: float
    model_name: str
    pricing_used: ModelPricing


def calculate_cost(
    prompt_tokens: int,
    completion_tokens: int,
    model_name: Optional[str] = None,
) -> CostResult:
    """Calculate cost for a single LLM call.

    Args:
        prompt_tokens: Number of input/prompt tokens.
        completion_tokens: Number of output/completion tokens.
        model_name: Model identifier for pricing lookup.

    Returns:
        CostResult with detailed cost breakdown.
    """
    pricing = get_model_pricing(model_name)

    input_cost = (prompt_tokens / 1000.0) * pricing.input_cost_per_1k
    output_cost = (completion_tokens / 1000.0) * pricing.output_cost_per_1k
    total_cost = input_cost + output_cost

    return CostResult(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
        input_cost_usd=round(input_cost, 6),
        output_cost_usd=round(output_cost, 6),
        total_cost_usd=round(total_cost, 6),
        model_name=model_name or "unknown",
        pricing_used=pricing,
    )


# ---------------------------------------------------------------------------
# Budget Tracking
# ---------------------------------------------------------------------------

@dataclass
class BudgetConfig:
    """Budget configuration for a tenant (app or user)."""
    budget_type: str  # "daily", "monthly", "total"
    limit_usd: float  # Maximum spend allowed
    warning_threshold_pct: float = 0.8  # Warn at 80% by default
    soft_limit: bool = True  # If False, stop execution when exceeded


@dataclass
class BudgetStatus:
    """Current budget status for a tenant."""
    budget_type: str
    limit_usd: float
    spent_usd: float
    remaining_usd: float
    usage_percent: float
    is_warning: bool
    is_exceeded: bool
    period_start: datetime
    period_end: Optional[datetime] = None


class BudgetTracker:
    """In-memory budget tracker for development/testing.

    For production, implement a persistent BudgetTracker that uses
    MongoDB or your platform's billing database.

    This class demonstrates the budget tracking pattern that OSS users
    can adapt to their needs.
    """

    def __init__(self):
        # In-memory tracking: {(app_id, user_id, budget_type): spent_usd}
        self._spent: Dict[tuple, float] = {}
        self._configs: Dict[tuple, BudgetConfig] = {}
        self._lock = asyncio.Lock()
        self._listeners: List[Callable[[str, BudgetStatus], Awaitable[None]]] = []

    def configure_budget(
        self,
        app_id: str,
        user_id: str,
        budget_type: str,
        limit_usd: float,
        warning_threshold_pct: float = 0.8,
        soft_limit: bool = True,
    ) -> None:
        """Configure a budget for a tenant.

        Args:
            app_id: Application identifier.
            user_id: User identifier.
            budget_type: Type of budget ("daily", "monthly", "total").
            limit_usd: Maximum spend allowed in USD.
            warning_threshold_pct: Percentage at which to emit warning (0.0-1.0).
            soft_limit: If True, only warn. If False, block execution.
        """
        key = (app_id, user_id, budget_type)
        self._configs[key] = BudgetConfig(
            budget_type=budget_type,
            limit_usd=limit_usd,
            warning_threshold_pct=warning_threshold_pct,
            soft_limit=soft_limit,
        )
        logger.info(f"Budget configured: {key} = ${limit_usd:.2f} ({budget_type})")

    async def record_spend(
        self,
        app_id: str,
        user_id: str,
        cost_usd: float,
        budget_type: str = "total",
    ) -> BudgetStatus:
        """Record spending and return updated budget status.

        Args:
            app_id: Application identifier.
            user_id: User identifier.
            cost_usd: Amount spent in USD.
            budget_type: Type of budget to update.

        Returns:
            BudgetStatus with current state.
        """
        key = (app_id, user_id, budget_type)

        async with self._lock:
            current = self._spent.get(key, 0.0)
            new_total = current + cost_usd
            self._spent[key] = new_total

        config = self._configs.get(key)
        limit = config.limit_usd if config else float("inf")
        warning_pct = config.warning_threshold_pct if config else 0.8

        remaining = max(0.0, limit - new_total)
        usage_pct = (new_total / limit) if limit > 0 else 0.0
        is_warning = usage_pct >= warning_pct and usage_pct < 1.0
        is_exceeded = usage_pct >= 1.0

        status = BudgetStatus(
            budget_type=budget_type,
            limit_usd=limit,
            spent_usd=round(new_total, 6),
            remaining_usd=round(remaining, 6),
            usage_percent=round(usage_pct * 100, 2),
            is_warning=is_warning,
            is_exceeded=is_exceeded,
            period_start=datetime.now(timezone.utc),  # Simplified for demo
        )

        # Emit events for significant state changes
        if is_exceeded:
            await self._emit_budget_event(BUDGET_EXCEEDED_EVENT, app_id, user_id, status)
        elif is_warning:
            await self._emit_budget_event(BUDGET_WARNING_EVENT, app_id, user_id, status)

        return status

    async def get_status(
        self,
        app_id: str,
        user_id: str,
        budget_type: str = "total",
    ) -> BudgetStatus:
        """Get current budget status without recording new spend."""
        key = (app_id, user_id, budget_type)
        spent = self._spent.get(key, 0.0)

        config = self._configs.get(key)
        limit = config.limit_usd if config else float("inf")
        warning_pct = config.warning_threshold_pct if config else 0.8

        remaining = max(0.0, limit - spent)
        usage_pct = (spent / limit) if limit > 0 else 0.0

        return BudgetStatus(
            budget_type=budget_type,
            limit_usd=limit,
            spent_usd=round(spent, 6),
            remaining_usd=round(remaining, 6),
            usage_percent=round(usage_pct * 100, 2),
            is_warning=usage_pct >= warning_pct and usage_pct < 1.0,
            is_exceeded=usage_pct >= 1.0,
            period_start=datetime.now(timezone.utc),
        )

    async def reset_budget(
        self,
        app_id: str,
        user_id: str,
        budget_type: str = "total",
    ) -> None:
        """Reset spent amount for a budget (e.g., at period rollover)."""
        key = (app_id, user_id, budget_type)
        async with self._lock:
            self._spent[key] = 0.0
        logger.info(f"Budget reset: {key}")

    def add_listener(
        self,
        callback: Callable[[str, BudgetStatus], Awaitable[None]],
    ) -> None:
        """Add a listener for budget events (warning/exceeded)."""
        self._listeners.append(callback)

    async def _emit_budget_event(
        self,
        event_type: str,
        app_id: str,
        user_id: str,
        status: BudgetStatus,
    ) -> None:
        """Emit a budget event via the event dispatcher."""
        try:
            dispatcher = get_event_dispatcher()
            await dispatcher.emit(event_type, {
                "app_id": app_id,
                "user_id": user_id,
                "budget_type": status.budget_type,
                "limit_usd": status.limit_usd,
                "spent_usd": status.spent_usd,
                "remaining_usd": status.remaining_usd,
                "usage_percent": status.usage_percent,
                "is_exceeded": status.is_exceeded,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
        except Exception as e:
            logger.debug(f"Failed to emit budget event: {e}")

        # Notify listeners
        for listener in self._listeners:
            try:
                await listener(event_type, status)
            except Exception as e:
                logger.debug(f"Budget listener error: {e}")


# ---------------------------------------------------------------------------
# Singleton Budget Tracker
# ---------------------------------------------------------------------------

_budget_tracker: Optional[BudgetTracker] = None


def get_budget_tracker() -> BudgetTracker:
    """Get the global budget tracker singleton."""
    global _budget_tracker
    if _budget_tracker is None:
        _budget_tracker = BudgetTracker()
    return _budget_tracker


# ---------------------------------------------------------------------------
# Cost Event Emission
# ---------------------------------------------------------------------------

async def emit_cost_event(
    *,
    chat_id: str,
    app_id: str,
    user_id: str,
    workflow_name: str,
    cost_result: CostResult,
    agent_name: Optional[str] = None,
    invocation_id: Optional[str] = None,
) -> None:
    """Emit a cost calculation event for platform consumption.

    This is called automatically by the telemetry pipeline but can also
    be called manually for custom cost tracking scenarios.
    """
    if not _is_cost_tracking_enabled():
        return

    try:
        dispatcher = get_event_dispatcher()
        await dispatcher.emit(COST_CALCULATED_EVENT, {
            "chat_id": chat_id,
            "app_id": app_id,
            "user_id": user_id,
            "workflow_name": workflow_name,
            "agent_name": agent_name,
            "invocation_id": invocation_id,
            "model_name": cost_result.model_name,
            "prompt_tokens": cost_result.prompt_tokens,
            "completion_tokens": cost_result.completion_tokens,
            "total_tokens": cost_result.total_tokens,
            "input_cost_usd": cost_result.input_cost_usd,
            "output_cost_usd": cost_result.output_cost_usd,
            "total_cost_usd": cost_result.total_cost_usd,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
    except Exception as e:
        logger.debug(f"Failed to emit cost event: {e}")


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------

__all__ = [
    # Pricing
    "ModelPricing",
    "get_model_pricing",
    # Cost calculation
    "CostResult",
    "calculate_cost",
    # Budget tracking
    "BudgetConfig",
    "BudgetStatus",
    "BudgetTracker",
    "get_budget_tracker",
    # Events
    "emit_cost_event",
    "COST_CALCULATED_EVENT",
    "BUDGET_WARNING_EVENT",
    "BUDGET_EXCEEDED_EVENT",
]
