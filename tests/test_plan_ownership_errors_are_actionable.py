"""A plan rejection must name a change that is not already true.

The 2026-09-24 acceptance run at OSS 95a2a584 got the furthest yet: `app_kind:
saas`, `contract_required: true`, and a `subscription_config` task declared
(#715 and #716 both held). `review_app_build_plan` then refused the plan three
times with seven ownership errors, one of which could not be acted on:

    - 2: this task owns ['crud_pack', 'tasks'] under modules/ and declares
         surface_id='task_management', but claims capability_pack_id='crud_pack'.
         A module task's capability_pack_id is the module directory it writes.
         Set it to 'crud_pack', or move the files to the module that capability owns.

`sorted(module_ids)[0]` picked alphabetically from a two-element set and landed
on the value the task already declared. The instruction was a no-op, so all
three revision attempts re-emitted the same plan and the build died. The
comment above that branch already records an earlier version of this same
failure -- one message covering distinct faults -- fixed for the
wrong-single-id case but not for the several-directories case.

The real fault there is different in kind: the task writes into two module
directories, and a module task owns exactly one. That needs "split it", not
"rename the field".

The other six errors shared one root. The plan declared capabilities
`mozaikspay`, `crud_pack`, `analytics_pack` against surfaces `tasks`,
`subscriptions`, `subscription_management` -- product categories used as
identities. The guidance for that is explicit and upstream anchoring is
stronger: ValueEngine's capability_pack_hints are literally
`['crud_pack', 'analytics_pack', 'billing_pack']`, and DesignDocs carries
`source_capability_packs: ('crud_pack',)`. The "found 0" message said a
capability was missing without naming what to emit, so it could not teach the
agent out of the mistake on retry.

Retry feedback is the only lever that corrects a plan inside the run. These
tests pin that each message names a change the agent can make.
"""

from __future__ import annotations

import pytest

pytest.importorskip("factory_app.workflows.AppGenerator.tools.app_plan_review")

from factory_app.workflows.AppGenerator.tools.app_plan_review import (  # noqa: E402
    validate_plan_origins,
)
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge  # noqa: E402

GENERATED = "generated_module"


def _pack(pack_id: str, surface_id: str, source: str = GENERATED) -> dict:
    return {
        "capability_pack_id": pack_id,
        "surface_id": surface_id,
        "surface_kind": "module",
        "capability_source": source,
        "primary_entities": [],
    }


def _surface(surface_id: str) -> dict:
    return {
        "surface_id": surface_id,
        "owner": "app",
        "surface_kind": "module",
        "primary_entities": [],
    }


def _errors(plan: dict, surfaces: list[dict]) -> list[str]:
    """The individual findings, without the 'Plan ownership errors:' header."""
    try:
        validate_plan_origins(plan, ContextVariablesBridge({"design_surface_map": {"surfaces": surfaces}}))
    except Exception as exc:  # the validator raises with the joined message
        return [
            line.lstrip("- ").strip()
            for line in str(exc).splitlines()
            if line.startswith("- ")
        ]
    return []


def test_a_task_spanning_two_modules_is_told_to_split() -> None:
    """The live payload. The old message told it to set a field to its own value."""
    plan = {
        "capability_packs": [_pack("crud_pack", "task_management")],
        "build_tasks": [
            {
                "task_id": "2",
                "capability_pack_id": "crud_pack",
                "surface_id": "task_management",
                "owned_paths": ["modules/crud_pack/module.yaml", "modules/tasks/module.yaml"],
            }
        ],
    }
    joined = "\n".join(_errors(plan, [_surface("task_management")]))
    assert "2 module directories" in joined, "name the actual fault: it writes two modules"
    assert "Split it into one task per module" in joined
    assert "Set it to 'crud_pack'" not in joined, (
        "the old message prescribed the value already declared, which no revision can satisfy"
    )


def test_a_single_wrong_module_id_still_gets_the_rename() -> None:
    """The case the old message was right for must keep working."""
    plan = {
        "capability_packs": [_pack("crud_pack", "task_management")],
        "build_tasks": [
            {
                "task_id": "3",
                "capability_pack_id": "crud_pack",
                "surface_id": "task_management",
                "owned_paths": ["modules/tasks/module.yaml"],
            }
        ],
    }
    joined = "\n".join(_errors(plan, [_surface("task_management")]))
    assert "modules/tasks/" in joined
    assert "Set it to 'tasks'" in joined, "a single differing directory is a rename, not a split"


def test_unapproved_module_capabilities_must_be_removed_before_ownership_review() -> None:
    plan = {
        "capability_packs": [_pack("crud_pack", "crud_pack"), _pack("analytics_pack", "analytics_pack")],
        "build_tasks": [],
    }
    joined = "\n".join(_errors(plan, [_surface("tasks")]))
    assert "crud_pack" in joined
    assert "analytics_pack" in joined
    assert "unapproved" in joined
    assert "remove its capability and tasks" in joined
    assert "relabel" in joined


def test_a_correct_plan_raises_nothing() -> None:
    """The messages must not come from a validator that rejects valid plans."""
    plan = {
        "capability_packs": [_pack("tasks", "tasks")],
        "build_tasks": [
            {
                "task_id": "1",
                "capability_pack_id": "tasks",
                "surface_id": "tasks",
                "owned_paths": ["modules/tasks/module.yaml", "modules/tasks/backend/service.py"],
            }
        ],
    }
    assert _errors(plan, [_surface("tasks")]) == []


def test_every_ownership_message_prescribes_an_action() -> None:
    """The property behind all of the above, across the faults this validator reports."""
    cases = [
        (
            {
                "capability_packs": [_pack("crud_pack", "task_management")],
                "build_tasks": [
                    {
                        "task_id": "2",
                        "capability_pack_id": "crud_pack",
                        "surface_id": "task_management",
                        "owned_paths": ["modules/crud_pack/m.yaml", "modules/tasks/m.yaml"],
                    }
                ],
            },
            [_surface("task_management")],
        ),
        ({"capability_packs": [_pack("crud_pack", "crud_pack")], "build_tasks": []}, [_surface("tasks")]),
    ]
    verbs = ("Split", "Set it to", "Emit a", "Use the", "move the files", "remove its capability and tasks")
    for plan, surfaces in cases:
        for line in _errors(plan, surfaces):
            assert any(verb in line for verb in verbs), (
                f"this rejection states a problem with no action the agent can take: {line}"
            )
