"""Typed persistence error classification for repositories using injected stores."""

from __future__ import annotations

from pymongo.errors import DuplicateKeyError


def is_unique_constraint_violation(error: BaseException) -> bool:
    """Recognize a storage uniqueness conflict without exposing driver imports.

    Other failures remain unclassified. Callers should re-raise them and may
    recover a uniqueness conflict only after checking the expected scoped row.
    This predicate does not change exceptions raised by existing adapters.
    """
    return isinstance(error, DuplicateKeyError)
