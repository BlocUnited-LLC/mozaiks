"""Cross-pack drift guard: events.yaml declared event types must appear in service files.

Each generated module that produces domain events ships a
``contracts/events.yaml`` that declares the event type strings it emits.
These declarations are the contract that downstream subscribers (other
modules' ``reactions.yaml``, notification configs, external integrations)
rely on.

If a declared event type is never actually emitted by the module's backend
service — because the emit call was forgotten, renamed, or removed without
updating the declaration — the contract promises an event that never fires.
Subscribers register for a ghost event with no startup warning.

This test file walks every ``contracts/events.yaml`` in every pack template
and asserts that each declared event type string appears as a literal in the
corresponding module's backend service files (``service.py``,
``base_handler.py``, or ``handler.py``).

Tests are parametrized so a mismatch names the exact pack, module, and
event type — not just a bulk assertion failure.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import json
import pytest
import yaml

WORKSPACE = Path(__file__).resolve().parents[1]
BUILD_CONTEXT = WORKSPACE / "factory_app" / "build_context"


def _read_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _backend_source(module_dir: Path) -> str | None:
    """Return concatenated source of all backend handler/service files."""
    sources: list[str] = []
    for name in ("service.py", "base_handler.py", "handler.py"):
        f = module_dir / "backend" / name
        if f.exists():
            sources.append(f.read_text(encoding="utf-8"))
    return "\n".join(sources) if sources else None


def _collect_cases() -> list[tuple[str, str, str]]:
    """Return (pack_id, module_id, event_type) for every events.yaml declaration."""
    cases: list[tuple[str, str, str]] = []
    for events_file in sorted(BUILD_CONTEXT.rglob("events.yaml")):
        if "__pycache__" in events_file.parts:
            continue
        # Accept both YAML and JSON (some packs use .json migrations, events.yaml is always YAML)
        try:
            pack_id = events_file.relative_to(BUILD_CONTEXT).parts[0]
        except ValueError:
            continue

        data = _read_yaml(events_file)
        events = data.get("events") or []
        if not events:
            continue

        # Module directory is two levels up from contracts/events.yaml
        module_dir = events_file.parent.parent
        module_id = module_dir.name

        for evt in events:
            # Support both {type: ...} and {event_type: ...} shapes
            etype = evt.get("type") or evt.get("event_type") or evt.get("id")
            if etype:
                cases.append((pack_id, module_id, etype))

    return cases


_CASES = _collect_cases()


@pytest.mark.parametrize(
    "pack_id,module_id,event_type",
    _CASES,
    ids=[f"{p}/{m}/event={e}" for p, m, e in _CASES],
)
def test_events_yaml_event_type_appears_in_service(
    pack_id: str, module_id: str, event_type: str
) -> None:
    """Every event type declared in contracts/events.yaml must appear in the backend service.

    A declaration without a matching emit call means the contract promises
    an event that never fires — downstream subscribers register for a ghost.
    """
    module_dir = BUILD_CONTEXT / pack_id / "templates" / "modules" / module_id

    # Fallback: glob for the module dir in case the path segment differs
    if not module_dir.is_dir():
        candidates = list(
            (BUILD_CONTEXT / pack_id / "templates" / "modules").glob(f"*{module_id}*")
        )
        exact = [d for d in candidates if d.name == module_id]
        module_dir = (exact or candidates)[0] if (exact or candidates) else module_dir

    assert module_dir.is_dir(), (
        f"Could not locate module directory for {pack_id}/{module_id}"
    )

    src = _backend_source(module_dir)
    assert src is not None, (
        f"{pack_id}/{module_id}: events.yaml declares {event_type!r} but no "
        f"service.py / base_handler.py / handler.py found at {module_dir / 'backend'}"
    )

    assert event_type in src, (
        f"{pack_id}/{module_id}: contracts/events.yaml declares event type "
        f"{event_type!r} but that string does not appear in any backend service file. "
        f"Either add the ctx.emit() call or remove the stale event declaration."
    )
