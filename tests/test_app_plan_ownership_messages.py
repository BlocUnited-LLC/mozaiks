"""An ownership rejection must name the signal that disagrees.

One message covered three distinct faults:

    {task_id}: module task capability_pack_id=..., surface_id=..., path
    modules=[...] must resolve to one declared module capability

A live build spent all three revision attempts re-emitting the same mismatch --
a task writing `modules/overdue_management/` while claiming
`capability_pack_id='analytics_pack'`. The message listed the three values but
never said which was wrong or what to set it to, so there was nothing for the
agent to act on and the run died with the plan unbuilt.

Two of the three signals agreed. The fix is to say so.
"""

from __future__ import annotations

import pytest

from factory_app.workflows.AppGenerator.tools.app_plan_review import validate_plan_origins
from tests.test_app_plan_review import _context, _plan


@pytest.fixture(autouse=True)
def _contracts():
    from tests.test_continuous_deterministic_materialization import _load_models

    _load_models()


def _module_task(plan: dict) -> dict:
    return next(
        task
        for task in plan["build_tasks"]
        if any(str(p).startswith("modules/reports/") for p in (task.get("owned_paths") or []))
    )


def test_a_pack_id_that_names_nothing_lists_what_is_declared() -> None:
    plan = _plan()
    _module_task(plan)["capability_pack_id"] = "analytics_pack"
    with pytest.raises(ValueError) as err:
        validate_plan_origins(plan, _context())
    message = str(err.value)
    assert "analytics_pack" in message
    assert "reports" in message, "the declared capability ids must be listed so the agent can pick one"


def test_a_path_that_disagrees_with_the_pack_says_what_to_set() -> None:
    """The live failure: paths and surface_id agree, capability_pack_id does not."""
    plan = _plan()
    plan["capability_packs"].append({
        "capability_pack_id": "analytics_pack", "surface_id": "reports",
        "surface_kind": "module", "capability_source": "generated_module",
        "pack_type": "custom_domain", "label": "Analytics", "summary": "Analytics",
        "implementation_mode": "hybrid", "primary_entities": ["Report"],
    })
    _module_task(plan)["capability_pack_id"] = "analytics_pack"
    with pytest.raises(ValueError) as err:
        validate_plan_origins(plan, _context())
    message = str(err.value)
    assert "'reports'" in message, "the value to set must appear, not just the conflict"
    assert "module directory it writes" in message, "say which signal is authoritative"


def test_the_generic_catch_all_wording_is_gone() -> None:
    plan = _plan()
    _module_task(plan)["capability_pack_id"] = "analytics_pack"
    with pytest.raises(ValueError) as err:
        validate_plan_origins(plan, _context())
    assert "must resolve to one declared module capability" not in str(err.value), (
        "that phrasing described three faults at once and named none of them"
    )


def test_an_agreeing_plan_still_passes() -> None:
    validate_plan_origins(_plan(), _context())
