"""A text trigger that routes the workflow must match a whole line, not a substring.

The runtime matcher (`_matches_text_conditions`) uses ``re.search``, so an
unanchored pattern matches anywhere in a reply. When the key it writes is a
routing flag, that turns ordinary prose into a routing decision.

Found live on PR #521: AgentGenerator's ``workflow_review_approved`` used
``(?i)\b(approved?|looks good|...)\b``. Against the real matcher, "this is not
approved", "I do not approve this design" and "looks good so far" all set the
flag to True — and because the write is persisted and never reset, a stray
"looks good" during the interview silently pre-approved the review gate that had
not happened yet.

The asymmetry this guard encodes: a false-positive flag that ADVANCES past a
human gate is unsafe, while one that routes back into more review is not. So
anchoring is required by default, and any exemption has to be argued in
SAFE_UNANCHORED rather than merely tolerated.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

FACTORY_WORKFLOWS = Path(__file__).resolve().parents[1] / "factory_app" / "workflows"

#: (workflow, context_key) -> why a loose match is acceptable for this flag.
#: Only ever add an entry whose false positives fail SAFE — i.e. the flag sends
#: the builder back into review. Never exempt a flag that advances a gate.
SAFE_UNANCHORED: dict[tuple[str, str], str] = {
    ("AgentGenerator", "workflow_review_revision_requested"): (
        "A revision request is naturally a sentence ('please change the sequence "
        "diagram'), so it cannot be a line sentinel. Failing loose is safe here: a "
        "spurious match routes back to PatternAgent for more review, never past the "
        "gate. The graph also evaluates this rule BEFORE the approval rule, so a reply "
        "matching both is treated as a change request."
    ),
}

_TEXT_TRIGGERS = {"user_text", "agent_text"}


def _workflow_dirs() -> list[Path]:
    return sorted(
        d for d in FACTORY_WORKFLOWS.iterdir()
        if d.is_dir() and (d / "context_variables.yaml").exists()
    )


def _load(path: Path) -> dict:
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _routing_keys(workflow_dir: Path) -> set[str]:
    """Context keys the compiled graph branches on — i.e. keys that route."""
    rules = _load(workflow_dir / "transition_graph.yaml").get("transition_rules") or []
    return {r["condition_key"] for r in rules if isinstance(r, dict) and r.get("condition_key")}


def _is_anchored(match: dict) -> bool:
    """An exact `equals`, or a regex pinned to both ends of a line."""
    if str(match.get("equals") or "").strip():
        return True
    regex = str(match.get("regex") or "")
    return "^" in regex and "$" in regex


def _text_triggers(definitions: dict):
    for key, definition in definitions.items():
        if not isinstance(definition, dict):
            continue
        source = definition.get("source")
        if not isinstance(source, dict):
            continue
        for trigger in source.get("triggers") or []:
            if isinstance(trigger, dict) and trigger.get("type") in _TEXT_TRIGGERS:
                yield key, trigger


@pytest.mark.parametrize("workflow_dir", _workflow_dirs(), ids=lambda p: p.name)
def test_routing_text_triggers_match_a_whole_line(workflow_dir: Path) -> None:
    definitions = _load(workflow_dir / "context_variables.yaml").get("definitions") or {}
    routing_keys = _routing_keys(workflow_dir)

    violations = []
    for key, trigger in _text_triggers(definitions):
        if key not in routing_keys:
            continue  # not a routing decision; a loose match cannot misroute
        match = trigger.get("match") or {}
        if _is_anchored(match):
            continue
        if (workflow_dir.name, key) in SAFE_UNANCHORED:
            continue
        violations.append(
            f"{key} ({trigger.get('type')}): {match!r} is an unanchored search over the "
            f"whole reply, but {key} routes the workflow"
        )

    assert not violations, (
        f"{workflow_dir.name} routes on unanchored text triggers:\n  "
        + "\n  ".join(violations)
        + "\n\nAnchor the pattern to a line (^...$) or use `equals`. If a loose match is "
        "genuinely correct, add it to SAFE_UNANCHORED with the reason its false "
        "positives fail safe."
    )


def test_every_exemption_still_applies() -> None:
    """An exemption left behind after its trigger changed is a silent hole."""
    stale = []
    for (workflow, key), reason in SAFE_UNANCHORED.items():
        assert reason.strip(), f"{workflow}/{key} exemption must document why it is safe"
        workflow_dir = FACTORY_WORKFLOWS / workflow
        if not workflow_dir.exists():
            stale.append(f"{workflow} no longer exists")
            continue
        definitions = _load(workflow_dir / "context_variables.yaml").get("definitions") or {}
        matches = [m for k, t in _text_triggers(definitions) if k == key for m in [t.get("match") or {}]]
        if not matches:
            stale.append(f"{workflow}/{key} has no text trigger")
        elif all(_is_anchored(m) for m in matches):
            stale.append(f"{workflow}/{key} is now anchored — drop the exemption")
    assert not stale, "SAFE_UNANCHORED is out of date: " + "; ".join(stale)
