"""The plan gate must fix what it can already derive, not reject and re-ask.

A live build of a habit tracker reached AppPlanAgent, failed review three times,
and killed the run in 38 seconds. The stored context says exactly what happened:

    app_plan_outcome    = 'blocked'
    app_plan_attempts   = 3
    app_task_batch_status = None

    Plan ownership errors:
    - habits_module: generated module capability_pack_id must match its
      approved surface_id, not the source product category
    - t1: module task capability_pack_id='crud_pack',
      surface_id='habits_module', path modules=['habits'] must resolve to one
      declared module capability
    - t2: ... same
    - t3: ... same

Four errors, one mistake: the planner named the module after the product
category it came from rather than the approved surface, and every ownership
check downstream keyed off the wrong name. The validator's own message states
the correct value. Asking a model to guess a name the system already knows
cost three rounds and then the run.

These tests execute the real validators. Asserting on the repair's return
strings would pass while the plan still failed review - which is the failure
mode that has cost the most time on this workflow.
"""

from __future__ import annotations

from typing import Any

import pytest

from factory_app.workflows.AppGenerator.tools.app_plan_review import (
    _repair_plan,
    validate_plan_origins,
)


class _Context:
    """Minimal stand-in for the runtime context the validators read."""

    def __init__(self, values: dict[str, Any]) -> None:
        self._values = dict(values)

    def get(self, key: str, default: Any = None) -> Any:
        return self._values.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._values[key] = value


def _design_surface_map() -> dict[str, Any]:
    return {
        "surfaces": [
            {
                "surface_id": "habits_module",
                "owner": "app",
                "surface_kind": "module",
                "primary_entities": ["Habit", "HabitCheckIn"],
            }
        ]
    }


def _plan_as_the_model_wrote_it() -> dict[str, Any]:
    """The exact shape that failed the live run.

    The product category ("crud_pack") stands in for the approved surface
    ("habits_module") in the pack id, in every task, and in the directory the
    owned paths sit under.
    """
    return {
        "capability_packs": [
            {
                "capability_pack_id": "crud_pack",
                "surface_id": "habits_module",
                "surface_kind": "module",
                "capability_source": "generated_module",
                "primary_entities": ["Habit"],
            }
        ],
        "build_tasks": [
            {
                "task_id": f"t{index}",
                "capability_pack_id": "crud_pack",
                "surface_id": "habits_module",
                "owned_paths": [f"modules/habits/backend/{name}.py"],
            }
            for index, name in enumerate(("schemas", "services", "handler"), start=1)
        ],
    }


def _context() -> _Context:
    return _Context({"design_surface_map": _design_surface_map(), "capability_packs": []})


def test_the_live_failure_is_reproduced_before_repair() -> None:
    """Without repair this plan fails exactly as the real run did."""
    with pytest.raises(ValueError) as excinfo:
        validate_plan_origins(_plan_as_the_model_wrote_it(), _context())

    message = str(excinfo.value)
    assert "must match its approved surface_id" in message
    assert "must resolve to one declared module capability" in message
    # One mistake, many errors - which is why re-asking the model never helped.
    # The live run produced exactly four; this fixture also drifts
    # primary_entities, so pin the shape rather than an exact count.
    assert message.count("\n- ") >= 4
    # Every one of them names the same module.
    assert message.count("habits_module") >= 4


def test_repair_makes_the_same_plan_pass() -> None:
    plan = _plan_as_the_model_wrote_it()
    context = _context()

    repairs = _repair_plan(plan, context)
    assert repairs, "the derivable mismatch must be repaired, not passed through"

    # The assertion that matters: the real validator now accepts it.
    validate_plan_origins(plan, context)


def test_repair_adopts_the_approved_identity_everywhere() -> None:
    plan = _plan_as_the_model_wrote_it()
    _repair_plan(plan, _context())

    assert plan["capability_packs"][0]["capability_pack_id"] == "habits_module"
    assert plan["capability_packs"][0]["primary_entities"] == ["Habit", "HabitCheckIn"]
    for task in plan["build_tasks"]:
        assert task["capability_pack_id"] == "habits_module"
        assert task["surface_id"] == "habits_module"
        # The ownership rule requires module_ids == {pack_id}, so the directory
        # has to move with the name or the plan still fails.
        assert all(path.startswith("modules/habits_module/") for path in task["owned_paths"])


def test_repair_leaves_a_correct_plan_alone() -> None:
    plan = _plan_as_the_model_wrote_it()
    _repair_plan(plan, _context())
    already_correct = {
        "capability_packs": [dict(p) for p in plan["capability_packs"]],
        "build_tasks": [dict(t) for t in plan["build_tasks"]],
    }

    assert _repair_plan(already_correct, _context()) == []
    validate_plan_origins(already_correct, _context())


def test_repair_does_not_invent_an_unapproved_module() -> None:
    """Only surfaces the approved design declares may be adopted."""
    plan = _plan_as_the_model_wrote_it()
    plan["capability_packs"][0]["surface_id"] = "not_in_the_design"
    plan["build_tasks"][0]["surface_id"] = "not_in_the_design"

    assert _repair_plan(plan, _context()) == []
    # Still rejected, which is correct - this one is not derivable.
    with pytest.raises(ValueError):
        validate_plan_origins(plan, _context())


def _plan_with_an_invented_capability() -> dict[str, Any]:
    """The second live failure: a capability nobody approved.

    After the identity repair landed, a rerun failed on exactly one error:

        notifications_pack: managed_capability requires a registered provider
        pack; a product category is not a managed service

    The concept was a habit tracker. Nothing asked for notifications, the
    approved design declares no notifications surface, and no provider pack is
    registered to supply one. All three attempts produced it, so feedback does
    not remove it - and one unapproved capability fails the entire plan.
    """
    plan = _plan_as_the_model_wrote_it()
    plan["capability_packs"].append(
        {
            "capability_pack_id": "notifications_pack",
            "surface_id": "notifications",
            "surface_kind": "module",
            "capability_source": "managed_capability",
        }
    )
    plan["build_tasks"].append(
        {
            "task_id": "t_notify",
            "capability_pack_id": "notifications_pack",
            "surface_id": "notifications",
            "owned_paths": ["modules/notifications/backend/services.py"],
        }
    )
    plan["build_tasks"][0]["depends_on"] = ["t_notify"]
    return plan


def test_invented_capability_is_dropped_and_the_plan_passes() -> None:
    plan = _plan_with_an_invented_capability()
    context = _context()

    repairs = _repair_plan(plan, context)

    assert any("dropped 'notifications_pack'" in r for r in repairs)
    assert [p["capability_pack_id"] for p in plan["capability_packs"]] == ["habits_module"]
    assert all(t["task_id"] != "t_notify" for t in plan["build_tasks"])
    # A task that depended on the removed one must not keep a dangling edge.
    assert plan["build_tasks"][0].get("depends_on") == []

    # The assertion that matters: the real validator accepts what is left.
    validate_plan_origins(plan, context)


def test_a_registered_managed_capability_is_kept() -> None:
    """Dropping is for capabilities with no provider, not for managed ones."""
    plan = _plan_with_an_invented_capability()
    context = _Context(
        {
            "design_surface_map": _design_surface_map(),
            # A real provider pack exists for it, so it is legitimate.
            "capability_packs": [
                {"id": "notifications_pack", "capability_source": "managed_capability"}
            ],
        }
    )

    repairs = _repair_plan(plan, context)

    assert not any("dropped" in r for r in repairs)
    assert any(p["capability_pack_id"] == "notifications_pack" for p in plan["capability_packs"])


from factory_app.workflows.AppGenerator.tools.app_plan_review import (  # noqa: E402
    _repair_coverage,
    validate_plan_coverage,
)


def _coverage_context() -> _Context:
    return _Context(
        {
            "design_surface_map": _design_surface_map(),
            "capability_packs": [],
            "experience_spec": {
                "pages": [
                    {"name": "Dashboard", "route": "/dashboard"},
                    {"name": "Habits", "route": "/habits"},
                ]
            },
        }
    )


def _plan_missing_coverage() -> dict[str, Any]:
    """The third live failure, once ownership passed.

        Incomplete build plan:
        - pages must preserve the approved name/route inventory:
          [('Dashboard', '/dashboard'), ('Habits', '/habits')]
        - habit_registry/business_services is incomplete; missing
          ['.../account_data_handler.py', '.../policy.py']

    Both name their own answer: the inventory comes from experience_spec and the
    required file set is derived from the pack.
    """
    return {
        "capability_packs": [
            {
                "capability_pack_id": "habits_module",
                "surface_id": "habits_module",
                "surface_kind": "module",
                "capability_source": "generated_module",
                "primary_entities": ["Habit", "HabitCheckIn"],
                "user_data_scope": True,
            }
        ],
        # The planner invented a page nobody approved and dropped one that was.
        "pages": [{"name": "Dashboard", "route": "/dashboard"}, {"name": "Settings", "route": "/settings"}],
        "build_tasks": [
            {
                "task_id": "t_contract",
                "task_type": "module_contract",
                "capability_pack_id": "habits_module",
                "surface_id": "habits_module",
                "owned_paths": ["modules/habits_module/module.yaml"],
            },
            {
                "task_id": "t_services",
                "task_type": "business_services",
                "capability_pack_id": "habits_module",
                "surface_id": "habits_module",
                "owned_paths": [
                    "modules/habits_module/backend/handler.py",
                    "modules/habits_module/backend/service.py",
                    "modules/habits_module/backend/repo.py",
                ],
            },
            {
                "task_id": "t_pages",
                "task_type": "page_bundle",
                "capability_pack_id": "habits_module",
                "surface_id": "habits_module",
                "owned_paths": [],
            },
        ],
    }


def test_coverage_failure_is_reproduced_before_repair() -> None:
    with pytest.raises(ValueError) as excinfo:
        validate_plan_coverage(_plan_missing_coverage(), _coverage_context())

    message = str(excinfo.value)
    assert "approved name/route inventory" in message
    assert "business_services is incomplete" in message


def test_coverage_repair_makes_the_plan_pass() -> None:
    plan = _plan_missing_coverage()
    context = _coverage_context()

    repairs = _repair_coverage(plan, context)
    assert repairs

    # The assertion that matters: the real coverage validator accepts it.
    validate_plan_coverage(plan, context)


def test_coverage_repair_restores_the_approved_pages() -> None:
    plan = _plan_missing_coverage()
    _repair_coverage(plan, _coverage_context())

    assert [(p["name"], p["route"]) for p in plan["pages"]] == [
        ("Dashboard", "/dashboard"),
        ("Habits", "/habits"),
    ]
    bundle = next(t for t in plan["build_tasks"] if t["task_type"] == "page_bundle")
    assert "app.json" in bundle["owned_paths"]


def test_coverage_repair_synthesizes_a_missing_task_type() -> None:
    """A module with no data_models task at all still has to get one."""
    plan = _plan_missing_coverage()
    assert not any(t["task_type"] == "data_models" for t in plan["build_tasks"])

    _repair_coverage(plan, _coverage_context())

    models = [t for t in plan["build_tasks"] if t["task_type"] == "data_models"]
    assert len(models) == 1
    assert models[0]["initial_agent"] == "ModelAgent"
    assert models[0]["depends_on"] == ["t_contract"]


def test_coverage_repair_is_idempotent() -> None:
    plan = _plan_missing_coverage()
    context = _coverage_context()
    _repair_coverage(plan, context)

    assert _repair_coverage(plan, context) == []
    validate_plan_coverage(plan, context)


def test_unbacked_provider_source_on_an_approved_surface_becomes_generated_module() -> None:
    """The fourth live variant: app code labelled as somebody else's pack.

        crud_pack: operator_pack requires an installed pack in capability_packs
        context. For app-owned code use generated_module with
        capability_pack_id=surface_id='habit_registry'

    The surface IS approved, so dropping it would delete the app. The source is
    a provider that is not installed, so validating it as-is always fails. The
    validator prescribes the fix in its own message, so apply it.

    Seen live as operator_pack; managed_capability reaches the same rule.
    """
    for source in ("operator_pack", "framework_pack", "managed_capability"):
        plan = _plan_as_the_model_wrote_it()
        plan["capability_packs"][0]["capability_source"] = source
        context = _context()

        repairs = _repair_plan(plan, context)

        assert any("generated_module" in r for r in repairs), source
        assert plan["capability_packs"][0]["capability_source"] == "generated_module"
        assert plan["capability_packs"][0]["capability_pack_id"] == "habits_module"
        # The real validator accepts it.
        validate_plan_origins(plan, context)


def test_an_installed_provider_pack_is_not_rewritten() -> None:
    """Only unbacked sources are adopted; a real provider pack stays as it is."""
    plan = _plan_as_the_model_wrote_it()
    plan["capability_packs"][0]["capability_pack_id"] = "habits_module"
    plan["capability_packs"][0]["capability_source"] = "framework_pack"
    context = _Context(
        {
            "design_surface_map": _design_surface_map(),
            "capability_packs": [
                {"id": "habits_module", "capability_source": "framework_pack"}
            ],
        }
    )

    repairs = _repair_plan(plan, context)

    assert not any("generated_module" in r for r in repairs)
    assert plan["capability_packs"][0]["capability_source"] == "framework_pack"


def _plan_with_two_page_bundles() -> dict[str, Any]:
    """A plan that splits page work across two page_bundle tasks.

    The coverage rule takes the union of what page_bundle tasks own, so a split
    is legal - but a separate rule forbids two tasks owning the same artifact,
    because task batches would race for it. An earlier version of this repair
    assumed one bundle task and force-assigned every page file to the first,
    which produced exactly that collision on a live run:

        Build tasks declare overlapping owned_paths:
        {'ui/pages/habits.yaml': ['page_bundle_habit_dashboard',
                                  'page_bundle_habit_list']}
    """
    return {
        "capability_packs": [
            {
                "capability_pack_id": "habits_module",
                "surface_id": "habits_module",
                "surface_kind": "module",
                "capability_source": "generated_module",
                "primary_entities": ["Habit"],
            }
        ],
        "pages": [
            {"name": "Dashboard", "route": "/dashboard"},
            {"name": "Habits", "route": "/habits"},
        ],
        "build_tasks": [
            {
                "task_id": "page_bundle_dashboard",
                "task_type": "page_bundle",
                "capability_pack_id": "habits_module",
                "surface_id": "habits_module",
                "owned_paths": ["ui/pages/dashboard.yaml"],
            },
            {
                "task_id": "page_bundle_habits",
                "task_type": "page_bundle",
                "capability_pack_id": "habits_module",
                "surface_id": "habits_module",
                "owned_paths": ["ui/pages/habits.yaml"],
            },
        ],
    }


def test_split_page_bundles_do_not_end_up_sharing_a_file() -> None:
    plan = _plan_with_two_page_bundles()
    _repair_coverage(plan, _coverage_context())

    bundles = [t for t in plan["build_tasks"] if t["task_type"] == "page_bundle"]
    owners: dict[str, list[str]] = {}
    for task in bundles:
        for path in task["owned_paths"]:
            owners.setdefault(path, []).append(task["task_id"])

    shared = {path: ids for path, ids in owners.items() if len(ids) > 1}
    assert not shared, f"page artifacts must have one owner each, got {shared}"

    # Every required artifact is still owned by somebody.
    assert set(owners) >= {"app.json", "ui/pages/dashboard.yaml", "ui/pages/habits.yaml"}
    # And each task kept the page it already owned rather than being reshuffled.
    by_id = {t["task_id"]: t["owned_paths"] for t in bundles}
    assert "ui/pages/habits.yaml" in by_id["page_bundle_habits"]
    assert "ui/pages/dashboard.yaml" in by_id["page_bundle_dashboard"]


def test_a_page_file_with_no_approved_page_is_dropped() -> None:
    """Rewriting pages to the approved inventory can orphan a page file.

    A live run approved exactly one page:

        plan pages: [('Dashboard', '/dashboard')]
          page_bundle_dashboard        -> ['app.json', 'ui/pages/dashboard.yaml']
          page_bundle_habit_management -> ['ui/pages/habits.yaml']

    The planner had invented a habits page. The repair replaced the inventory
    but left the orphaned file owned, and materialization failed with
    "ui/pages/habits.yaml: page has no approved plan identity".
    """
    plan = _plan_with_two_page_bundles()
    # Only Dashboard is approved this time.
    context = _Context(
        {
            "design_surface_map": _design_surface_map(),
            "capability_packs": [],
            "experience_spec": {"pages": [{"name": "Dashboard", "route": "/dashboard"}]},
        }
    )

    repairs = _repair_coverage(plan, context)

    assert any("not an approved page" in r for r in repairs)
    owned = {
        path
        for task in plan["build_tasks"]
        if task.get("task_type") == "page_bundle"
        for path in task.get("owned_paths") or []
    }
    assert "ui/pages/habits.yaml" not in owned
    assert owned == {"app.json", "ui/pages/dashboard.yaml"}


def test_a_bundle_task_left_with_nothing_is_removed() -> None:
    """A task owning no files would materialize nothing and fail the batch."""
    plan = _plan_with_two_page_bundles()
    context = _Context(
        {
            "design_surface_map": _design_surface_map(),
            "capability_packs": [],
            "experience_spec": {"pages": [{"name": "Dashboard", "route": "/dashboard"}]},
        }
    )

    _repair_coverage(plan, context)

    bundles = [t for t in plan["build_tasks"] if t.get("task_type") == "page_bundle"]
    assert all(t.get("owned_paths") for t in bundles)
    # And nothing still depends on a task that no longer exists.
    ids = {str(t.get("task_id")) for t in plan["build_tasks"]}
    for task in plan["build_tasks"]:
        assert set(map(str, task.get("depends_on") or [])) <= ids


def test_non_page_assets_are_not_dropped() -> None:
    """Only orphaned page files go; other owned assets stay put."""
    plan = _plan_with_two_page_bundles()
    plan["build_tasks"][1]["owned_paths"] = ["ui/pages/habits.yaml", "brand/logo.svg"]
    context = _Context(
        {
            "design_surface_map": _design_surface_map(),
            "capability_packs": [],
            "experience_spec": {"pages": [{"name": "Dashboard", "route": "/dashboard"}]},
        }
    )

    _repair_coverage(plan, context)

    kept = {
        path
        for task in plan["build_tasks"]
        for path in task.get("owned_paths") or []
    }
    assert "brand/logo.svg" in kept
    assert "ui/pages/habits.yaml" not in kept


def _two_approved_modules() -> dict[str, Any]:
    """The shape a live tool-lending library build produced.

    The design approved three app-owned modules; the planner emitted a
    capability for one and omitted the other two.
    """
    return {
        "surfaces": [
            {
                "surface_id": "catalogue_management",
                "owner": "app",
                "surface_kind": "module",
                "primary_entities": ["Tool"],
            },
            {
                "surface_id": "borrow_requests_management",
                "owner": "app",
                "surface_kind": "module",
                "primary_entities": ["BorrowRequest"],
            },
            {
                "surface_id": "overdue_management",
                "owner": "app",
                "surface_kind": "module",
                "primary_entities": ["OverdueItem"],
            },
        ]
    }


def _plan_missing_two_capabilities() -> dict[str, Any]:
    return {
        "capability_packs": [
            {
                "capability_pack_id": "catalogue_management",
                "surface_id": "catalogue_management",
                "surface_kind": "module",
                "capability_source": "generated_module",
                "primary_entities": ["Tool"],
            }
        ],
        "build_tasks": [
            {
                "task_id": "t1",
                "capability_pack_id": "catalogue_management",
                "surface_id": "catalogue_management",
                "owned_paths": ["modules/catalogue/backend/handler.py"],
            }
        ],
    }


def test_an_approved_module_with_no_capability_is_repaired_not_rejected() -> None:
    """The failure that ended a live run at stage 8.

    Two approved modules had no capability pack. Review rejected the plan, the
    task batch produced no items, and nothing was generated — `generated/apps`
    stayed empty. Every value needed is in the approved design.
    """
    plan = _plan_missing_two_capabilities()
    context = _Context({"design_surface_map": _two_approved_modules(), "capability_packs": []})

    repairs = _repair_plan(plan, context)

    assert any("borrow_requests_management" in line for line in repairs)
    assert any("overdue_management" in line for line in repairs)

    # The real validator, not the repair's own account of itself. It raises on
    # any remaining error, so reaching the end is the assertion.
    try:
        validate_plan_origins(plan, context)
    except ValueError as error:
        assert "requires exactly one module capability" not in str(error), (
            f"plan still fails ownership review after repair: {error}"
        )


def test_the_repaired_capability_carries_the_approved_identity() -> None:
    plan = _plan_missing_two_capabilities()
    context = _Context({"design_surface_map": _two_approved_modules(), "capability_packs": []})

    _repair_plan(plan, context)

    added = {
        pack["surface_id"]: pack
        for pack in plan["capability_packs"]
        if pack["surface_id"] in {"borrow_requests_management", "overdue_management"}
    }
    assert set(added) == {"borrow_requests_management", "overdue_management"}
    for surface_id, pack in added.items():
        assert pack["capability_pack_id"] == surface_id, "identity is the approved surface_id"
        assert pack["capability_source"] == "generated_module", "app-owned code is generated"
    assert added["overdue_management"]["primary_entities"] == ["OverdueItem"], (
        "the approved entities must be preserved, not invented"
    )


def test_an_existing_capability_is_left_alone() -> None:
    """Repair fills absence; it must not overwrite what the planner chose."""
    plan = _plan_missing_two_capabilities()
    before = dict(plan["capability_packs"][0])
    context = _Context({"design_surface_map": _two_approved_modules(), "capability_packs": []})

    _repair_plan(plan, context)

    kept = next(p for p in plan["capability_packs"] if p["surface_id"] == "catalogue_management")
    assert kept["capability_pack_id"] == before["capability_pack_id"]
    assert kept["primary_entities"] == before["primary_entities"]


def test_two_capabilities_claiming_one_surface_is_still_an_error() -> None:
    """Ambiguity is not derivable, so it stays rejected.

    Absence has exactly one correct answer. A surface claimed twice does not,
    and guessing which to drop would silently discard planner intent.
    """
    plan = _plan_missing_two_capabilities()
    plan["capability_packs"].append({
        "capability_pack_id": "catalogue_management_v2",
        "surface_id": "catalogue_management",
        "surface_kind": "module",
        "capability_source": "generated_module",
        "primary_entities": ["Tool"],
    })
    context = _Context({"design_surface_map": _two_approved_modules(), "capability_packs": []})

    _repair_plan(plan, context)

    with pytest.raises(ValueError) as raised:
        validate_plan_origins(plan, context)
    assert "requires exactly one module capability" in str(raised.value), (
        "a doubly-claimed surface must still be rejected"
    )


def test_the_ownership_error_states_how_many_it_found() -> None:
    """'not exactly one' is undiagnosable in a log without the count."""
    plan = _plan_missing_two_capabilities()
    plan["capability_packs"].append({
        "capability_pack_id": "catalogue_management_v2",
        "surface_id": "catalogue_management",
        "surface_kind": "module",
        "capability_source": "generated_module",
        "primary_entities": ["Tool"],
    })
    context = _Context({"design_surface_map": _two_approved_modules(), "capability_packs": []})

    with pytest.raises(ValueError) as raised:
        validate_plan_origins(plan, context)
    assert "(found 2)" in str(raised.value)


def test_a_doubly_claimed_surface_keeps_its_packs_distinguishable() -> None:
    """Live: "habit_registry: requires exactly one module capability (found 2)".

    Two packs claiming one approved surface is the ambiguity the synthesis step
    deliberately refuses to resolve. Adopting the surface_id on each would give
    them identical capability_pack_ids - two distinguishable packs become twins,
    and whoever later decides how to merge them loses the information needed to
    do it.
    """
    plan = _plan_as_the_model_wrote_it()
    plan["capability_packs"].append(
        {
            "capability_pack_id": "analytics_pack",
            "surface_id": "habits_module",
            "surface_kind": "module",
            "capability_source": "generated_module",
            "primary_entities": ["Habit"],
        }
    )

    _repair_plan(plan, _context())

    ids = [p["capability_pack_id"] for p in plan["capability_packs"]]
    assert len(set(ids)) == len(ids), f"packs must stay distinguishable, got {ids}"
    assert {"crud_pack", "analytics_pack"} == set(ids)

    # And it is still rejected, with the count that makes it diagnosable.
    with pytest.raises(ValueError) as excinfo:
        validate_plan_origins(plan, _context())
    assert "(found 2)" in str(excinfo.value)


def test_a_singly_claimed_surface_is_still_adopted() -> None:
    """The ambiguity guard must not disable the ordinary repair."""
    plan = _plan_as_the_model_wrote_it()

    _repair_plan(plan, _context())

    assert plan["capability_packs"][0]["capability_pack_id"] == "habits_module"
