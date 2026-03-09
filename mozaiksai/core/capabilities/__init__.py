"""Non-AG2 capability execution primitives.

This package provides runtime-owned, product-agnostic helpers for executing
single-agent "capabilities" (non-AG2) as simple LLM loops.
"""

from .simple_llm import SimpleLLMCapabilityService, get_general_capability_service

__all__ = [
    "SimpleLLMCapabilityService",
    "get_general_capability_service",
]

