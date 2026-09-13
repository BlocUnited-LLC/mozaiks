"""Every channel-close reason AG2 can emit must be an explicit decision.

``_resolve_close_status`` maps a terminal close reason onto a run status.
Anything it does not name falls through to the incoming status — which is how a
severed run gets reported as a clean completion, and how the build sequence
advances past a stage that never finished.

That failure mode has now happened twice on PR #521. ``no_transition_matched``
(graph exhaustion) reported success until d5ac85d0, and ``max_turns`` (AG2's
turn-budget cutoff) still did afterwards, because the repair named two of the
three reasons and nobody enumerated the rest.

So this guard reads the reasons out of the *installed* AG2 rather than trusting
a hand-written list: a version bump that introduces a new close reason fails
here until someone classifies it. Silence is not an answer — every reason is
either mapped to a failure or recorded in KNOWN_BENIGN with its rationale.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
RUNNER = REPO_ROOT / "mozaiksai" / "core" / "adapters" / "ag2_network_runner.py"

#: Close reasons that legitimately leave the incoming run status untouched.
#: Adding an entry is a claim that the run really did reach a declared end (or a
#: declared pause) — not merely that the failure is inconvenient to surface.
KNOWN_BENIGN: dict[str, str] = {
    "workflow_complete": "A graph-declared TerminateTarget reason: the workflow said it finished.",
    "awaiting_user_input": "A pause, not a close: the runner sets this when handing control to the builder.",
    "finished": "AG2's completed-run fallback; the channel reached its declared end.",
    "workflow_terminated": (
        "AG2's generic terminate fallback when the graph closed without naming a reason. "
        "Reached only via an explicit terminate target, so the run ended deliberately."
    ),
    "consulting_complete": (
        "Emitted by AG2's consulting adapter, which no Mozaiks factory workflow uses. "
        "If a workflow ever adopts it, reclassify rather than deleting this entry."
    ),
}


def _ag2_package_root() -> Path | None:
    spec = importlib.util.find_spec("ag2")
    if spec is None or not spec.origin:
        return None
    return Path(spec.origin).parent


def _emitted_close_reasons(package_root: Path) -> set[str]:
    """String literals AG2 can hand back as a channel-close reason."""
    reasons: set[str] = set()
    patterns = (
        re.compile(r'auto_close_reason\s*=\s*"([a-z_]+)"'),
        # `reason = state.pending_close_reason or "workflow_terminated"`
        re.compile(r'pending_close_reason\s+or\s+"([a-z_]+)"'),
        re.compile(r'close_reason\s+or\s+"([a-z_]+)"'),
    )
    for path in package_root.rglob("*.py"):
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for pattern in patterns:
            reasons.update(pattern.findall(text))
    return reasons


def _reasons_mapped_to_failure() -> set[str]:
    """Literals `_resolve_close_status` explicitly compares against."""
    source = RUNNER.read_text(encoding="utf-8")
    start = source.index("def _resolve_close_status")
    body = source[start : source.index("\ndef ", start + 1)]
    return set(re.findall(r'close_reason\s*==\s*"([a-z_]+)"', body))


def test_resolve_close_status_still_maps_the_known_silent_skips() -> None:
    """The three reasons that have actually caused silent skips stay mapped."""
    mapped = _reasons_mapped_to_failure()
    for reason in ("workflow_failed", "no_transition_matched", "max_turns"):
        assert reason in mapped, (
            f"{reason!r} is no longer mapped to a failure in _resolve_close_status. "
            "Each of these reported a severed run as a clean completion in a live build."
        )


def test_every_ag2_close_reason_is_classified() -> None:
    package_root = _ag2_package_root()
    if package_root is None:
        pytest.skip("ag2 is not installed")

    emitted = _emitted_close_reasons(package_root)
    assert emitted, "found no close-reason literals in ag2 — the scan patterns are stale"

    classified = _reasons_mapped_to_failure() | set(KNOWN_BENIGN)
    unclassified = sorted(emitted - classified)

    assert not unclassified, (
        f"AG2 can emit close reasons this repo never classifies: {unclassified}.\n"
        "Each one currently falls through to the incoming run status, so a severed run "
        "would be reported as a clean completion and the build sequence would advance "
        "past an unfinished stage.\n"
        "Map it to a failure in _resolve_close_status, or add it to KNOWN_BENIGN with "
        "the reason the run really did reach a declared end."
    )


def test_benign_classifications_are_documented_and_not_double_counted() -> None:
    for reason, rationale in KNOWN_BENIGN.items():
        assert rationale.strip(), f"{reason!r} needs a rationale, not an empty string"
    overlap = sorted(set(KNOWN_BENIGN) & _reasons_mapped_to_failure())
    assert not overlap, (
        f"{overlap} are both mapped to failure and listed as benign — the intent is "
        "ambiguous. Keep each reason in exactly one place."
    )
