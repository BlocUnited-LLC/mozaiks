"""Reader for AG2 context variables, which arrive as mappings or attribute bags.

Every value comes back as plain data. Live containers freeze reads (mapping
proxies and tuples), and a caller that type-tests the result against `list` or
`dict` silently takes the other branch.
"""

from __future__ import annotations

from typing import Any

from mozaiksai.core.workflow.context.frozen import detach

__all__ = ["context_get"]


def context_get(context_variables: Any, key: str, default: Any = None) -> Any:
    if context_variables is None:
        return default
    if hasattr(context_variables, "get"):
        try:
            return detach(context_variables.get(key, default))
        except Exception:
            return default
    data = getattr(context_variables, "data", None)
    if isinstance(data, dict):
        return data.get(key, default)
    if isinstance(context_variables, dict):
        return context_variables.get(key, default)
    return default
