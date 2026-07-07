"""Feature flag system for canary rollouts and gradual capability enablement.

Flags are evaluated per-request. The default backend reads from environment
variables, with an optional MongoDB-backed backend for runtime toggling
without redeployment.
"""

from mozaiksai.core.flags.feature_flags import FeatureFlags, get_flags, is_enabled

__all__ = ["FeatureFlags", "get_flags", "is_enabled"]
