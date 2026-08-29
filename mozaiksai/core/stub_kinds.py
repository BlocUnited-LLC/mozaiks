"""Shared leaf contract for bounded customization-stub kinds.

The semantic compiler and application layout registry both consume this
closed vocabulary.  Keeping it in a dependency-free leaf prevents either
layer from becoming a second authority or importing the other subsystem.
"""

from enum import StrEnum


class StubKind(StrEnum):
    """Bounded customization stub kinds a family may reference."""

    PYTHON_BACKEND = "python_backend"
    JS_FRONTEND = "js_frontend"


__all__ = ["StubKind"]
