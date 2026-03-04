"""
MozaiksCore Observability Module

Provides unified telemetry, tracing, cost tracking, and performance monitoring
for AG2-based agentic workflows.

Components:
- PerformanceManager: In-memory metrics tracking and MongoDB persistence
- RealtimeTokenLogger: AG2 BaseLogger for token usage capture
- AG2RuntimeLoggingController: Session-scoped AG2 runtime logging
- OTELTracing: OpenTelemetry integration for distributed tracing
- CostTracker: Cost calculation and budget management utilities

Quick Start:
    # Initialize OTEL at app startup
    from mozaiksai.core.observability import initialize_otel_tracing
    initialize_otel_tracing()

    # Instrument AG2 patterns
    from mozaiksai.core.observability import instrument_pattern
    instrument_pattern(my_ag2_pattern)

    # Calculate costs
    from mozaiksai.core.observability import calculate_cost
    result = calculate_cost(prompt_tokens=100, completion_tokens=50, model_name="gpt-4o")
"""

from .performance_manager import (
    PerformanceManager,
    PerformanceConfig,
    get_performance_manager,
)

from .otel_tracing import (
    initialize_otel_tracing,
    shutdown_otel_tracing,
    get_tracer_provider,
    instrument_llm_globally,
    instrument_agent,
    instrument_pattern,
    traced_workflow,
)

from .cost_tracker import (
    ModelPricing,
    get_model_pricing,
    CostResult,
    calculate_cost,
    BudgetConfig,
    BudgetStatus,
    BudgetTracker,
    get_budget_tracker,
    emit_cost_event,
)

__all__ = [
    # Performance Manager
    "PerformanceManager",
    "PerformanceConfig",
    "get_performance_manager",
    # OTEL Tracing
    "initialize_otel_tracing",
    "shutdown_otel_tracing",
    "get_tracer_provider",
    "instrument_llm_globally",
    "instrument_agent",
    "instrument_pattern",
    "traced_workflow",
    # Cost Tracking
    "ModelPricing",
    "get_model_pricing",
    "CostResult",
    "calculate_cost",
    "BudgetConfig",
    "BudgetStatus",
    "BudgetTracker",
    "get_budget_tracker",
    "emit_cost_event",
]

