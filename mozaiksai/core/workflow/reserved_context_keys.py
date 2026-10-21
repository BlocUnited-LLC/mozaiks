"""Runtime-reserved context identifier vocabulary.

Some context identifiers are runtime-owned: the runtime serves them as
transient projections and applications must never declare them as ordinary
context state. ``structured_output`` is the auto-tool projection of the exact
validated agent output — declaring it in ``context_variables.yaml`` would give
one identifier two meanings (application state vs runtime projection), so
every canonical context declaration surface rejects it through this single
shared authority.

AG2 also reserves the ``ag:`` and ``a2a:`` key prefixes for its own
control-plane state, and drops such keys from every variables payload that
crosses an A2A, AG-UI, A2UI, or NLIP transport. A declared key with either
prefix would validate and then silently never reach a remote agent, so the
same authority rejects those prefixes.

This module is a dependency-free leaf so declarative contract validation,
typed context schema validation, and runtime context code can all consume the
same vocabulary without import cycles.
"""

from __future__ import annotations

#: The documented public auto-tool context key for validated agent output.
STRUCTURED_OUTPUT_CONTEXT_KEY = "structured_output"

#: Context identifiers owned by the runtime. Applications must not declare
#: them in context_variables.yaml definitions or agent views; no authority
#: metadata (authority_class, writer_ids, persisted, source, triggers) can
#: legalize such a declaration.
RUNTIME_RESERVED_CONTEXT_KEYS = frozenset({STRUCTURED_OUTPUT_CONTEXT_KEY})

#: Context-key prefixes AG2 reserves (``ag2.context.RESERVED_VARIABLE_PREFIXES``).
#: Kept here so this module stays a dependency-free leaf; a test keeps the two equal.
AG2_RESERVED_CONTEXT_PREFIXES = ("ag:", "a2a:")


def require_application_context_name_allowed(name: object, *, where: str) -> str:
    """Fail closed when an application declares a runtime-reserved identifier.

    Returns the (unmodified) name so validators can use this as a pass-through
    check. ``where`` names the declaring surface for the error message.
    """
    key = str(name or "").strip()
    if key in RUNTIME_RESERVED_CONTEXT_KEYS:
        raise ValueError(
            f"{where} must not declare {key!r}: it is reserved runtime "
            "vocabulary. The auto-tool structured_output projection is "
            "runtime-owned and transient; tools read it via "
            "context_variables.get(\"structured_output\") without any "
            "declaration, and no declaration metadata can claim it."
        )
    if key.startswith(AG2_RESERVED_CONTEXT_PREFIXES):
        prefixes = " and ".join(repr(prefix) for prefix in AG2_RESERVED_CONTEXT_PREFIXES)
        raise ValueError(
            f"{where} must not declare {key!r}: AG2 reserves the {prefixes} "
            "key prefixes for its own state and drops such keys from A2A, "
            "AG-UI, A2UI, and NLIP transports, so the value would never reach "
            "a remote agent. Rename the key without the prefix."
        )
    return str(name)


__all__ = [
    "AG2_RESERVED_CONTEXT_PREFIXES",
    "RUNTIME_RESERVED_CONTEXT_KEYS",
    "STRUCTURED_OUTPUT_CONTEXT_KEY",
    "require_application_context_name_allowed",
]
