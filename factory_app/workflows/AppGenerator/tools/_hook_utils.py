"""
Re-export shim — the canonical implementation has moved to the shared layer.

Use `factory_app.workflows._shared.hook_utils` for new code.
Existing AppGenerator hook imports are preserved via this re-export.
"""

from factory_app.workflows._shared.hook_utils import update_agent_section  # noqa: F401

__all__ = ["update_agent_section"]
