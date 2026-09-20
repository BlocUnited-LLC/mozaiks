"""Runtime-reserved context identifier vocabulary.

Some context identifiers are runtime-owned: the runtime serves them as
transient projections and applications must never declare them as ordinary
context state. ``structured_output`` is the auto-tool projection of the exact
validated agent output — declaring it in ``context_variables.yaml`` would give
one identifier two meanings (application state vs runtime projection), so
every canonical context declaration surface rejects it through this single
shared authority.

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
    return str(name)


__all__ = [
    "RUNTIME_RESERVED_CONTEXT_KEYS",
    "STRUCTURED_OUTPUT_CONTEXT_KEY",
    "require_application_context_name_allowed",
]
