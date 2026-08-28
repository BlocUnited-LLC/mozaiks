"""ADR 0006 / ADR 0007 capability-advertisement interlock.

ADR 0007 assigns two capability identifiers to its compiler contract:
``semantic_taxonomy_v1`` and ``semantic_reference_contracts_v1``.  ADR 0007's
rollout table requires that they are advertised **together**, and only after
rollout slices 1 and 2 "both pass outside advisory mode".  A class existing in
source, an advisory registry mode, or one advertised capability without the
other does not unlock ADR 0006's production ``JourneyExecutionPort.start``.

The Slice 1 taxonomy currently validates only on the explicit advisory path
(``taxonomy_advisory=True`` / ``advisory=True``); every production consumer
defaults it off.  Slice 1 therefore has not passed *outside* advisory mode,
so truthful joint advertisement is not yet possible without activating
later-slice authority-cutover behavior.  This module is the single authority
for that answer: it advertises neither identifier and states the exact gate.
"""

from __future__ import annotations

from typing import Final

SEMANTIC_TAXONOMY_CAPABILITY: Final[str] = "semantic_taxonomy_v1"
SEMANTIC_REFERENCE_CONTRACTS_CAPABILITY: Final[str] = "semantic_reference_contracts_v1"

#: The exact remaining gate blocking joint advertisement.
_ADVERTISEMENT_GATE: Final[str] = (
    "blocked: Slice 1 taxonomy validation passes only in advisory "
    "(test/development) mode — production consumers construct with "
    "taxonomy_advisory=False, so 'slices 1 and 2 both pass outside advisory "
    "mode' (ADR 0007 rollout slice 2) is not yet satisfied. Flipping taxonomy "
    "enforcement on for production consumers is authority-cutover work owned "
    "by a later slice. Until then neither capability is advertised; partial "
    "advertisement is prohibited."
)


def advertised_semantic_compiler_capabilities() -> tuple[str, ...]:
    """Return the ADR 0007 compiler capabilities that are truthfully provable.

    Returns an empty tuple: the two identifiers must be advertised jointly or
    not at all, and the joint precondition is not met (see
    :func:`semantic_capability_advertisement_gate`).  This function is the only
    place a later slice may flip the answer, and it may only ever return
    ``()`` or both identifiers together.
    """
    return ()


def semantic_capability_advertisement_gate() -> str:
    """Describe the exact remaining gate blocking joint advertisement."""
    return _ADVERTISEMENT_GATE


__all__ = [
    "SEMANTIC_REFERENCE_CONTRACTS_CAPABILITY",
    "SEMANTIC_TAXONOMY_CAPABILITY",
    "advertised_semantic_compiler_capabilities",
    "semantic_capability_advertisement_gate",
]
