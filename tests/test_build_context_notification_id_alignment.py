"""Cross-pack drift guard: notification_id references in reactions.yaml must be declared.

When a reaction declares ``target.kind: notification`` with a ``notification_id``,
the runtime looks up that ID in the module's ``contracts/notifications.yaml`` to
find the notification template and audience configuration. If the ID is referenced
but never declared, the event fires and the reaction is dispatched — but the
notification lookup silently returns nothing, so no in-app notification is sent
and no error is raised.

This test file verifies two complementary contracts:

1. Every ``notification_id`` referenced in a reactions.yaml resolves to a
   declared notification in the corresponding module's ``contracts/notifications.yaml``.

2. Every pack whose ``contract.yaml`` names ``notifications.yaml`` as a
   required output actually ships that file under ``templates/``.

Tests are parametrized so a failure names the exact pack, reaction, and
missing notification ID rather than producing a bulk assertion error.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

WORKSPACE = Path(__file__).resolve().parents[1]
BUILD_CONTEXT = WORKSPACE / "factory_app" / "build_context"


def _read_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


# ---------------------------------------------------------------------------
# notification_id reference → declared notification
# ---------------------------------------------------------------------------

def _collect_notification_ref_cases() -> list[tuple[str, str, str, Path]]:
    """Return (pack_id, reaction_id, notification_id, notifications_yaml_path)."""
    cases: list[tuple[str, str, str, Path]] = []
    for reactions_yaml in sorted(BUILD_CONTEXT.rglob("reactions.yaml")):
        if "__pycache__" in reactions_yaml.parts:
            continue
        try:
            pack_id = reactions_yaml.relative_to(BUILD_CONTEXT).parts[0]
        except ValueError:
            continue
        data = _read_yaml(reactions_yaml)
        # notifications.yaml lives alongside reactions.yaml in the contracts/ dir
        notifs_file = reactions_yaml.parent / "notifications.yaml"
        for rxn in data.get("reactions") or []:
            target = rxn.get("target") or {}
            if target.get("kind") != "notification":
                continue
            nid = target.get("notification_id")
            if nid:
                cases.append((pack_id, rxn.get("id", "?"), nid, notifs_file))
    return cases


_NOTIF_REF_CASES = _collect_notification_ref_cases()


@pytest.mark.parametrize(
    "pack_id,reaction_id,notification_id,notifs_file",
    _NOTIF_REF_CASES,
    ids=[f"{p}/reaction={r}/notification_id={n}" for p, r, n, _ in _NOTIF_REF_CASES],
)
def test_notification_id_is_declared(
    pack_id: str, reaction_id: str, notification_id: str, notifs_file: Path
) -> None:
    """Every notification_id referenced in reactions.yaml must be declared in notifications.yaml.

    A missing declaration means the notification lookup silently returns nothing
    at event time — the buyer/operator never receives the notification.
    """
    assert notifs_file.exists(), (
        f"{pack_id}: reaction {reaction_id!r} references notification_id={notification_id!r} "
        f"but {notifs_file.relative_to(BUILD_CONTEXT)} does not exist. "
        f"Create the file and declare the notification."
    )

    data = _read_yaml(notifs_file)
    declared_ids = {n["id"] for n in (data.get("notifications") or [])}

    assert notification_id in declared_ids, (
        f"{pack_id}: reaction {reaction_id!r} references notification_id={notification_id!r} "
        f"which is not declared in {notifs_file.relative_to(BUILD_CONTEXT)}. "
        f"Declared IDs: {sorted(declared_ids)}. "
        f"Add the notification definition or fix the reference."
    )


# ---------------------------------------------------------------------------
# contract.yaml required_outputs → notifications.yaml file exists
# ---------------------------------------------------------------------------

def _collect_contract_notif_cases() -> list[tuple[str, Path]]:
    """Return (pack_id, expected_notifications_yaml_path) for each contract that requires it."""
    cases: list[tuple[str, Path]] = []
    for contract_yaml in sorted(BUILD_CONTEXT.glob("*/contract.yaml")):
        if "__pycache__" in contract_yaml.parts:
            continue
        try:
            pack_id = contract_yaml.relative_to(BUILD_CONTEXT).parts[0]
        except ValueError:
            continue
        data = _read_yaml(contract_yaml)
        for output in data.get("required_outputs") or []:
            path_str = output.get("path", "")
            if "notifications.yaml" in path_str:
                # Resolve expected file path under templates/
                expected = BUILD_CONTEXT / pack_id / "templates" / path_str
                cases.append((pack_id, expected))
    return cases


_CONTRACT_NOTIF_CASES = _collect_contract_notif_cases()


@pytest.mark.parametrize(
    "pack_id,expected_path",
    _CONTRACT_NOTIF_CASES,
    ids=[f"{p}" for p, _ in _CONTRACT_NOTIF_CASES],
)
def test_contract_required_notifications_yaml_exists(
    pack_id: str, expected_path: Path
) -> None:
    """If contract.yaml lists notifications.yaml as a required_output, the file must exist.

    A contract declaration without the template file means AppGenerator will
    emit a module that references notifications which can never be resolved.
    """
    assert expected_path.exists(), (
        f"{pack_id}: contract.yaml declares {expected_path.relative_to(BUILD_CONTEXT / pack_id / 'templates')} "
        f"as a required_output but the file does not exist under templates/. "
        f"Create the notifications.yaml template."
    )
