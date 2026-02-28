"""Multi-tenant (app-scoped) helpers.

This package centralizes app_id normalization and MongoDB scoping filters used
throughout the runtime (persistence, tokens, orchestration).

It is workflow-agnostic and applies uniformly to all workflows.
"""

from .app_ids import (
    build_app_scope_filter,
    coalesce_app_id,
    dual_write_app_scope,
    extract_app_id,
    normalize_app_id,
)

__all__ = [
    "normalize_app_id",
    "coalesce_app_id",
    "build_app_scope_filter",
    "dual_write_app_scope",
    "extract_app_id",
]
