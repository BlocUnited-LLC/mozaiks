"""Review model-authored plans before the existing task executor sees them."""

from __future__ import annotations

import logging
import re
from typing import Annotated, Any

from factory_app.workflows.AppGenerator.tools.app_build_plan import (
    _CANONICAL_INITIAL_AGENTS,
    _context_available_pack_map,
    _normalized_owned_paths,
    _pack_id_from_descriptor,
    app_build_plan,
)
from mozaiksai.core.workflow.context.frozen import detach
from mozaiksai.core.workflow.dependency_graph import deterministic_topological_order
from mozaiksai.core.workflow.generator_support.code_files import _page_file_stem
from mozaiksai.core.workflow.outputs.structured import load_workflow_structured_outputs

logger = logging.getLogger(__name__)


def _clear_plan(context: Any) -> None:
    context.set("app_plan_ready", False)
    context.set("app_build_plan", None)
    context.set("app_task_batch_items", [])
    context.set("app_task_batch_status", None)


def _repair_plan(plan: dict[str, Any], context: Any) -> list[str]:
    """Fix plan fields whose correct value the validator already knows.

    A live build of a habit tracker failed three times and killed the run on
    four errors that were one mistake: the planner named its module after the
    product category it came from ("crud_pack") instead of the approved surface
    ("habits_module"), and the validator's own message said so - "must match its
    approved surface_id". Every downstream ownership check then keyed off the
    wrong name and failed too.

    Rejecting a plan over a name the system can derive, and asking the model to
    guess it again, costs three LLM rounds and then the whole run. Where the
    approved design already states the answer, apply it and say so. Anything not
    derivable is still rejected by the validators, unchanged.
    """
    repairs: list[str] = []
    design = detach(context.get("design_surface_map")) or {}
    packs = plan.get("capability_packs") or []

    approved = {
        surface["surface_id"]: surface
        for surface in design.get("surfaces") or []
        if surface.get("owner") == "app" and surface.get("surface_kind") == "module"
    }

    # 0. An approved module surface with no capability at all. The design
    #    states the identity and the entities; the source is generated_module
    #    by definition for app-owned code. A live build lost its whole run to
    #    two surfaces missing here, which then produced no build tasks and no
    #    bundle. Only absence is repaired: several packs claiming one surface
    #    is a genuine ambiguity and stays an error.
    #    Only when every pack already maps to an approved surface. A pack
    #    pointing somewhere unapproved is a mislabelled module, not a missing
    #    one, and synthesizing here would duplicate it under two identities.
    every_pack_is_approved = all(
        pack.get("surface_id") in approved
        for pack in packs
        if pack.get("surface_kind") == "module"
    )
    for surface_id, surface in (approved.items() if every_pack_is_approved else []):
        if any(pack.get("surface_id") == surface_id for pack in packs):
            continue
        packs.append({
            "surface_id": surface_id,
            "surface_kind": "module",
            "capability_source": "generated_module",
            "capability_pack_id": surface_id,
            "primary_entities": list(surface.get("primary_entities") or []),
        })
        repairs.append(f"{surface_id}: approved module had no capability -> generated_module")
    plan["capability_packs"] = packs

    # 1. The approved surface_id is the module's identity. Adopt it.
    renames: dict[str, str] = {}
    available_now = _context_available_pack_map(context)
    # A surface claimed by more than one pack is the ambiguity step 0 refuses to
    # resolve, and adopting the surface_id on each would give them identical
    # capability_pack_ids - turning two distinguishable packs into twins and
    # making the ambiguity harder to read, or to merge later. Seen live:
    # "habit_registry: requires exactly one module capability (found 2)".
    claims: dict[str, int] = {}
    for pack in packs:
        if pack.get("surface_kind") == "module" and pack.get("surface_id") in approved:
            claims[str(pack["surface_id"])] = claims.get(str(pack["surface_id"]), 0) + 1
    for pack in packs:
        surface_id = pack.get("surface_id")
        surface = approved.get(surface_id)
        if surface is None or pack.get("surface_kind") != "module":
            continue
        if claims.get(str(surface_id), 0) > 1:
            continue
        source = pack.get("capability_source")
        if source not in {"generated_module", None, ""}:
            # A provider source with no installed provider, on a surface the
            # design approved as app-owned, is app code mislabelled. The
            # validator says so outright: "For app-owned code use
            # generated_module with capability_pack_id=surface_id=...". Seen live
            # as operator_pack, and previously as managed_capability.
            if str(_pack_id_from_descriptor(pack)) in available_now:
                continue
            pack["capability_source"] = "generated_module"
            repairs.append(f"{surface_id}: {source} with no installed provider -> generated_module")
        pack.setdefault("capability_source", "generated_module")
        current = _pack_id_from_descriptor(pack)
        if current != surface_id:
            renames[str(current)] = str(surface_id)
            pack["capability_pack_id"] = surface_id
            repairs.append(f"capability_pack_id {current!r} -> approved surface_id {surface_id!r}")
        approved_entities = list(surface.get("primary_entities") or [])
        if set(pack.get("primary_entities") or []) != set(approved_entities):
            pack["primary_entities"] = approved_entities
            repairs.append(f"{surface_id}: primary_entities -> approved {approved_entities}")

    # A capability sourced from a provider that does not exist, for a surface the
    # design never approved, is invented scope. The live run produced exactly
    # one: a "notifications_pack" declared managed_capability on a habit tracker
    # whose approved design has no notifications surface and whose concept never
    # mentioned them. Three attempts produced it every time, so feedback does not
    # remove it - and one unapproved capability fails the whole plan.
    available = _context_available_pack_map(context)
    surviving: list[dict[str, Any]] = []
    dropped: set[str] = set()
    for pack in packs:
        pack_id = str(_pack_id_from_descriptor(pack))
        source = pack.get("capability_source")
        unbacked = source in {"managed_capability", "framework_pack", "operator_pack"} and pack_id not in available
        if unbacked and pack.get("surface_id") not in approved:
            dropped.add(pack_id)
            repairs.append(
                f"dropped {pack_id!r}: {source} with no registered provider and no approved surface"
            )
            continue
        surviving.append(pack)

    if dropped:
        plan["capability_packs"] = surviving
        kept_tasks = []
        for task in plan.get("build_tasks") or []:
            if str(task.get("capability_pack_id") or "") in dropped:
                repairs.append(f"dropped task {task.get('task_id')!r}: built a capability that was dropped")
                continue
            kept_tasks.append(task)
        dropped_ids = {
            str(t.get("task_id"))
            for t in (plan.get("build_tasks") or [])
            if str(t.get("capability_pack_id") or "") in dropped
        }
        for task in kept_tasks:
            depends = [d for d in (task.get("depends_on") or []) if str(d) not in dropped_ids]
            if len(depends) != len(task.get("depends_on") or []):
                task["depends_on"] = depends
                repairs.append(f"{task.get('task_id')}: dropped dependency on a removed task")
        plan["build_tasks"] = kept_tasks
        packs = surviving

    if not renames:
        return repairs

    # 2. Tasks must agree with the pack they build, including the directory
    #    their owned paths sit under - the rule is module_ids == {pack_id}.
    pack_surface = {_pack_id_from_descriptor(p): p.get("surface_id") for p in packs}
    for task in plan.get("build_tasks") or []:
        pack_id = str(task.get("capability_pack_id") or "")
        new_pack_id = renames.get(pack_id)
        module_dirs = {
            path.split("/")[1]
            for path in _normalized_owned_paths(task)
            if path.startswith("modules/") and len(path.split("/")) > 1
        }
        if new_pack_id is None and len(module_dirs) == 1:
            # The directory is the other way of naming the same module.
            new_pack_id = renames.get(next(iter(module_dirs)))
        if new_pack_id is None:
            continue

        if task.get("capability_pack_id") != new_pack_id:
            task["capability_pack_id"] = new_pack_id
            repairs.append(f"{task.get('task_id')}: capability_pack_id -> {new_pack_id!r}")
        surface_id = pack_surface.get(new_pack_id)
        if surface_id and task.get("surface_id") != surface_id:
            task["surface_id"] = surface_id
            repairs.append(f"{task.get('task_id')}: surface_id -> {surface_id!r}")

        stale = {d for d in module_dirs if d != new_pack_id}
        if stale:
            paths = list(task.get("owned_paths") or [])
            for index, path in enumerate(paths):
                parts = str(path).split("/")
                if len(parts) > 1 and parts[0] == "modules" and parts[1] in stale:
                    parts[1] = new_pack_id
                    paths[index] = "/".join(parts)
            task["owned_paths"] = paths
            repairs.append(
                f"{task.get('task_id')}: owned paths moved from modules/{sorted(stale)[0]}/ to modules/{new_pack_id}/"
            )

    return repairs

def _repair_coverage(plan: dict[str, Any], context: Any) -> list[str]:
    """Supply the coverage facts the validator already computes.

    With ownership repaired, a live run failed on two errors that both name
    their own answer:

      - pages must preserve the approved name/route inventory:
        [('Dashboard', '/dashboard'), ('Habits', '/habits')]
      - habit_registry/business_services is incomplete; missing
        ['modules/habit_registry/backend/account_data_handler.py',
         'modules/habit_registry/backend/policy.py']

    The approved inventory comes from experience_spec and the required file set
    is derived from the pack itself, so neither needs a model. Anything the
    validator cannot derive is still left for it to reject.
    """
    repairs: list[str] = []
    tasks = plan.get("build_tasks") or []
    if not tasks or context.get("build_mode") == "revision" or context.get("brownfield_build_path"):
        return repairs

    # 1. The approved page inventory is authority.
    experience = detach(context.get("experience_spec")) or {}
    expected = [
        (page["name"], page["route"])
        for page in experience.get("pages") or []
        if page.get("name") and page.get("route")
    ]
    if expected:
        planned = plan.get("pages") or []
        by_route = {page.get("route"): page for page in planned}
        by_name = {page.get("name"): page for page in planned}
        if {(p.get("name"), p.get("route")) for p in planned} != set(expected):
            rebuilt = []
            for name, route in expected:
                existing = by_route.get(route) or by_name.get(name) or {}
                page = dict(existing)
                page["name"], page["route"] = name, route
                rebuilt.append(page)
            plan["pages"] = rebuilt
            repairs.append(f"pages -> approved inventory {sorted(expected)}")

    # 2. page_bundle must own app.json and every materialized page file.
    page_paths = [f"ui/pages/{_page_file_stem(page)}.yaml" for page in plan.get("pages") or []]
    bundle_tasks = [task for task in tasks if task.get("task_type") == "page_bundle"]
    if bundle_tasks and page_paths:
        required_paths = ["app.json", *page_paths]
        wanted_stems = {path.lower() for path in required_paths}

        # A plan may split page work across several page_bundle tasks. The
        # coverage rule takes the union of what they own, but a separate rule
        # forbids two tasks owning the same artifact - "task batches cannot
        # race". So assign each required path to exactly one task: leave it
        # where it already lives, and give the rest to the first task.
        assigned: dict[str, dict[str, Any]] = {}
        for task in bundle_tasks:
            for path in _normalized_owned_paths(task):
                if path.lower() in wanted_stems:
                    assigned.setdefault(path, task)

        for path in required_paths:
            assigned.setdefault(path, bundle_tasks[0])

        for task in bundle_tasks:
            mine = [path for path in required_paths if assigned.get(path) is task]
            others: list[str] = []
            for path in task.get("owned_paths") or []:
                text = str(path)
                if text.lower() in wanted_stems:
                    continue
                # Rewriting pages to the approved inventory can orphan a page
                # file the planner invented. Keeping it fails materialization
                # with "page has no approved plan identity", because no approved
                # page claims that stem. Non-page assets are untouched.
                if text.startswith("ui/pages/") and text.endswith((".yaml", ".yml")):
                    repairs.append(f"{task.get('task_id')}: dropped {text!r}, not an approved page")
                    continue
                others.append(text)
            merged = others + mine
            if merged != list(task.get("owned_paths") or []):
                task["owned_paths"] = merged
                repairs.append(
                    f"{task.get('task_id')}: page_bundle owns {len(mine)} of "
                    f"{len(required_paths)} page artifact(s), no shared ownership"
                )

        empty = [
            task
            for task in bundle_tasks
            if task.get("task_type") == "page_bundle" and not (task.get("owned_paths") or [])
        ]
        if empty:
            empty_ids = {str(task.get("task_id")) for task in empty}
            tasks = [task for task in tasks if task not in empty]
            for task in tasks:
                depends = [d for d in (task.get("depends_on") or []) if str(d) not in empty_ids]
                if len(depends) != len(task.get("depends_on") or []):
                    task["depends_on"] = depends
            for task_id in sorted(empty_ids):
                repairs.append(f"dropped task {task_id!r}: no approved page left to build")
            plan["build_tasks"] = tasks

    # 3. Every generated module needs its required files owned by a task of the
    #    right type. The required set is derived exactly as the validator does.
    for pack in plan.get("capability_packs") or []:
        if pack.get("surface_kind") != "module" or pack.get("capability_source") != "generated_module":
            continue
        module_id = _pack_id_from_descriptor(pack)
        module_tasks = [task for task in tasks if task.get("capability_pack_id") == module_id]
        required: dict[str, set[str]] = {
            "module_contract": {f"modules/{module_id}/module.yaml"},
            "data_models": {f"modules/{module_id}/backend/schemas.py"},
            "business_services": {f"modules/{module_id}/backend/{name}.py" for name in ("handler", "service")},
        }
        if pack.get("primary_entities"):
            required["business_services"].update(
                f"modules/{module_id}/backend/{name}.py" for name in ("repo", "policy")
            )
        if pack.get("user_data_scope") is True:
            required["business_services"].add(f"modules/{module_id}/backend/account_data_handler.py")

        for kind, paths in required.items():
            typed = [t for t in module_tasks if t.get("task_type") == kind]
            owned = {path for t in typed for path in _normalized_owned_paths(t)}
            missing = sorted(paths - owned)
            if not missing:
                continue
            if typed:
                target = typed[0]
                target["owned_paths"] = list(target.get("owned_paths") or []) + missing
                repairs.append(f"{target.get('task_id')}: {kind} gained {missing}")
            else:
                synthesized = {
                    "task_id": f"task_{module_id}_{kind}",
                    "task_type": kind,
                    "capability_pack_id": module_id,
                    "surface_id": pack.get("surface_id"),
                    "surface_kind": "module",
                    "initial_agent": _CANONICAL_INITIAL_AGENTS[kind],
                    "execution_target": _CANONICAL_INITIAL_AGENTS[kind],
                    "description": _synthesized_task_brief(kind, module_id, pack),
                    "initial_message": _synthesized_task_brief(kind, module_id, pack),
                    "owned_paths": missing,
                    "depends_on": [],
                }
                tasks.append(synthesized)
                module_tasks.append(synthesized)
                repairs.append(f"synthesized {synthesized['task_id']!r} owning {missing}")

    plan["build_tasks"] = tasks
    repairs.extend(_repair_module_task_dependencies(plan))
    return repairs


def _repair_module_task_dependencies(plan: dict[str, Any]) -> list[str]:
    """Supply direct contract inputs after every module task has been synthesized."""
    repairs: list[str] = []
    tasks = plan.get("build_tasks") or []
    persistence_tasks = [
        str(task["task_id"])
        for task in tasks
        if task.get("task_type") == "persistence_contract"
        and "data/contract.json" in _normalized_owned_paths(task)
    ]
    for pack in plan.get("capability_packs") or []:
        if pack.get("surface_kind") != "module" or pack.get("capability_source") != "generated_module":
            continue
        module_id = _pack_id_from_descriptor(pack)
        module_tasks = [task for task in tasks if task.get("capability_pack_id") == module_id]
        prerequisites: list[str] = []
        for kind, path in (
            ("module_contract", f"modules/{module_id}/module.yaml"),
            ("data_models", f"modules/{module_id}/backend/schemas.py"),
        ):
            owners = [
                task for task in module_tasks
                if task.get("task_type") == kind and path in _normalized_owned_paths(task)
            ]
            if len(owners) != 1:
                raise ValueError(
                    f"{path}: expected exactly one {kind} task owner; "
                    f"found {[task.get('task_id') for task in owners]}"
                )
            prerequisites.append(str(owners[0]["task_id"]))

        for task in module_tasks:
            kind = task.get("task_type")
            if kind not in {"data_models", "business_services"}:
                continue
            required = prerequisites[:1] if kind == "data_models" else list(prerequisites)
            if pack.get("primary_entities"):
                required.extend(persistence_tasks)
            declared = list(task.get("depends_on") or [])
            missing = [task_id for task_id in required if task_id not in declared]
            if missing:
                task["depends_on"] = [*declared, *missing]
                repairs.append(
                    f"{task.get('task_id')}: added prerequisite contract inputs {missing}"
                )
    return repairs

# What each synthesized task tells its worker to do. A task the coverage
# repair invents still has to run: task_batches rejects any task whose
# prompt field is empty, so a task created without one does not merely lack
# polish - it kills the build. Six such tasks ended a live run 55 seconds in.
#
# Phrasing follows the planner's own, so a synthesized task reads like the
# ones beside it rather than announcing itself as machine-filled.
_SYNTHESIZED_TASK_BRIEFS = {
    "business_services": (
        "Implement the services required for managing {subject} operations, "
        "including the business logic and events the module contract declares."
    ),
    "data_models": (
        "Create the data models required for the {subject} entity based on the "
        "contract and persistence rules."
    ),
    "module_contract": (
        "Define the module contract that outlines the actions and relationships "
        "for managing {subject}."
    ),
    "persistence_contract": (
        "Establish data ownership over the {subject} collection with its schema "
        "and lifecycle policies in place."
    ),
    "data_migrations": (
        "Define the additive migrations required for the {subject} entity."
    ),
    "service_foundation": (
        "Establish the shared service foundation {subject} depends on."
    ),
}


def _synthesized_task_brief(kind: str, module_id: str, pack: dict[str, Any]) -> str:
    """Describe the work a synthesized task covers, from what the plan states.

    The entity the pack owns is the subject where one exists, matching how the
    planner writes these ("managing Tool operations"). A pack owning no entity
    falls back to the module id, which is still specific enough to act on.
    """
    entities = [str(e).strip() for e in (pack.get("primary_entities") or []) if str(e).strip()]
    subject = entities[0] if entities else str(module_id).replace("_", " ")
    template = _SYNTHESIZED_TASK_BRIEFS.get(kind)
    if template:
        return template.format(subject=subject)
    readable = str(kind).replace("_", " ")
    return f"Complete the {readable} work for {subject} as the approved plan describes."

WORD = chr(92) + "b"  # regex word boundary

_READ_OPERATION_PREFIXES = ("list_", "get_", "search_", "read_", "fetch_")


def _plural_entity_slug(entity: str) -> str:
    """Plural lowercase form of an entity, matching the convention in use.

    Every other action id in a generated bundle is entity-based - the live
    module declared create_habit and checkin_habit, and the page agent's own
    worked example is list_tickets. Naming a synthesized read after the module
    instead (list_habit_registry) would put a third convention in play, and the
    page agent would most plausibly emit list_habits and orphan against an
    action that exists under a name nobody guesses. That is harder to diagnose
    than the missing action this repair exists to prevent.

    The planner states the same rule for module ids: "use the plural lowercase
    entity id".
    """
    slug = re.sub(r"(?<!^)(?=[A-Z])", "_", str(entity).strip()).lower()
    slug = re.sub(r"[^a-z0-9]+", "_", slug).strip("_")
    if not slug:
        return ""
    if slug.endswith("y") and not slug.endswith(("ay", "ey", "iy", "oy", "uy")):
        return f"{slug[:-1]}ies"
    if slug.endswith(("s", "x", "z", "ch", "sh")):
        return f"{slug}es"
    return f"{slug}s"


def _repair_missing_read_operation(plan: dict[str, Any], context: Any) -> list[str]:
    """A module whose pages list its records must expose a way to read them.

    A generated habit tracker shipped a module declaring create_habit and
    checkin_habit and nothing else, while its dashboard rendered two tables of
    habits. Acceptance rejected the bundle:

        module_action_wiring: 3 page endpoint(s) reference unknown module actions
          orphaned_pages:   dashboard/habit-overview, dashboard/habit-list,
                            habits/habit-form/submit  -> all call /api/habits
          orphaned_actions: habit_registry/create_habit, habit_registry/checkin_habit

    A module that can create records but never list them is incoherent on its
    own terms, whichever agent dropped the operation.

    The operation has to be written into the owning module_contract task's
    initial_message, not only into the pack. The contract agent is told to
    "Treat the action list in `current_build_task.initial_message` as a closed
    contract" - it never reads capability_packs[].operations. An earlier version
    of this repair set operations[] alone and was inert: a live run logged
    "declared 'list_habits'" and emitted a module.yaml with create and checkoff
    and no read at all. The pages then had nothing to bind to and fell back to
    an invented /api/habits.

    Only genuine absence is filled. A pack that already declares any read is
    left alone, including under names this does not recognise, because the
    entity it reads is the planner's call and not derivable from the design.
    """
    repairs: list[str] = []
    design = detach(context.get("design_surface_map")) or {}
    approved = {
        surface["surface_id"]: surface
        for surface in design.get("surfaces") or []
        if surface.get("owner") == "app" and surface.get("surface_kind") == "module"
    }

    for pack in plan.get("capability_packs") or []:
        if pack.get("surface_kind") != "module" or pack.get("capability_source") != "generated_module":
            continue
        entities = [str(e) for e in (pack.get("primary_entities") or []) if str(e).strip()]
        if not entities or pack.get("surface_id") not in approved:
            continue
        operations = [str(op) for op in (pack.get("operations") or []) if str(op).strip()]
        if any(op.lower().startswith(_READ_OPERATION_PREFIXES) for op in operations):
            continue

        module_id = str(_pack_id_from_descriptor(pack))
        # Name it after the entity, not the module - see _plural_entity_slug.
        operation = f"list_{_plural_entity_slug(entities[0]) or module_id}"
        pack["operations"] = [*operations, operation]

        contract = next(
            (
                task
                for task in plan.get("build_tasks") or []
                if task.get("task_type") == "module_contract"
                and str(_pack_id_from_descriptor(task)) == module_id
            ),
            None,
        )
        if contract is None:
            # Say so rather than logging a success the bundle will not contain.
            repairs.append(
                f"{module_id}: owns {entities} with no read operation and no module_contract "
                f"task to declare {operation!r} in; the module will ship without a read"
            )
            continue
        message = str(contract.get("initial_message") or "").rstrip()
        note = (
            f"Required action (added by plan review): `{operation}` - read action "
            f"returning the {entities[0]} records the caller may see. Emit it in "
            "actions[] with the other actions named above. Its output_schema is an "
            'object with one array property holding the records, not a bare '
            '`type: "array"` - the renderer rejects a non-object schema that '
            "declares properties or required."
        )
        contract["initial_message"] = "\n\n".join(part for part in (message, note) if part)
        repairs.append(
            f"{module_id}: owns {entities} with no read operation; declared {operation!r} "
            f"in {contract.get('task_id')!r} so its pages have something to read"
        )
    return repairs



def _repair_page_contract_dependencies(plan: dict[str, Any], context: Any) -> list[str]:
    """Let a page_bundle task see the module contracts it is told to copy from.

    The page agent is instructed to "Copy every module action id exactly from
    dependency module.yaml outputs". A task only receives the outputs of its
    direct depends_on entries, and a live plan wired both page bundles to
    data_models and business_services and not to module_contract - the task that
    owns modules/*/module.yaml. So the agent was told to copy from a set that was
    always empty, and it did the only other thing available: it guessed.

    It guessed /api/modules/habits/create_habit for a module whose id is
    habits_registry. The canonical form was right because the rule is stated
    four times; the identity was invented because the contract never arrived.

    Ordering already held - business_services depends on module_contract, so the
    pages ran after it. Only visibility was missing.
    """
    repairs: list[str] = []
    tasks = plan.get("build_tasks") or []
    contracts = [
        str(task.get("task_id"))
        for task in tasks
        if task.get("task_type") == "module_contract" and str(task.get("task_id") or "").strip()
    ]
    if not contracts:
        return repairs

    for task in tasks:
        if task.get("task_type") != "page_bundle":
            continue
        declared = [str(dep) for dep in (task.get("depends_on") or []) if str(dep).strip()]
        # Every module contract, not a guessed subset: which modules a page binds
        # to is the page agent's call, and a page denied one contract is exactly
        # the failure this repairs. They are already ordered ahead of pages, so
        # naming them adds visibility and not serialization.
        missing = [contract for contract in contracts if contract not in declared]
        if not missing:
            continue
        task["depends_on"] = [*declared, *missing]
        repairs.append(
            f"{task.get('task_id')}: page_bundle could not see {missing}; "
            "added so its endpoints can name the real module"
        )
    return repairs


def validate_plan_dependencies(plan: dict[str, Any], context: Any) -> None:
    """Every depends_on must name a task the plan actually declares.

    Nothing checked this. The repairs above only strip dependencies on tasks
    they themselves removed, so a task the planner never declared in the first
    place survived review and killed the run at batch-build time instead:

        AG2 turn failed for AppPlanAgent: task batch 'app_build_tasks' has
        unresolved or cyclic dependencies: {'task_completion_4': [...]}

    That is a fatal ValueError with no feedback path, so the plan is discarded
    and the build is over. Raising here routes the same problem into the normal
    revision loop, where the agent is told which ids are missing.

    Rejecting rather than dropping the edge is deliberate. A task waiting on
    an id that was never declared usually means the planner intended that work
    and omitted it; silently deleting the dependency would hand back a plan
    that looks valid and builds an app missing whatever those tasks owned.
    """
    tasks = plan.get("build_tasks") or []
    declared = {
        str(task.get("task_id"))
        for task in tasks
        if str(task.get("task_id") or "").strip()
    }
    dangling = {}
    for task in tasks:
        missing = [
            str(dep)
            for dep in (task.get("depends_on") or [])
            if str(dep).strip() and str(dep) not in declared
        ]
        if missing:
            dangling[str(task.get("task_id"))] = missing
    if dangling:
        detail = "; ".join(
            f"{task_id} waits on {missing}" for task_id, missing in sorted(dangling.items())
        )
        raise ValueError(
            "Every depends_on must name a task_id this plan declares. "
            f"{detail}. Either declare the missing tasks, or drop the dependency "
            "if the work is already covered by a task that is present."
        )
    deterministic_topological_order(
        tasks,
        item_id=lambda task: str(task.get("task_id") or ""),
        dependencies=lambda task: task.get("depends_on") or [],
    )



def _repair_contract_task_operations(plan: dict[str, Any], context: Any) -> list[str]:
    """Name the planner's operations in the task the contract agent reads.

    ConfigMiddlewareAgent is told to "Treat the action list in
    current_build_task.initial_message as a closed contract. Emit every named
    action exactly once." Nothing guarantees that message names any actions.

    A live plan declared operations ['create_habit', 'list_habits',
    'record_checkin'] on the pack and gave the contract task this entire
    initial_message:

        Module to manage habits including creation, check-in recording,
        and retrieval.

    The closed contract was prose. The agent inferred create_habit and
    record_checkin from "creation, check-in recording", missed "retrieval", and
    shipped a module with no read at all. Its pages then bound to list_habits,
    which no module declared, and acceptance rejected the bundle.

    #634 covered the case where the planner omits a read entirely. This covers
    the larger one: the planner declared the operation and it never reached the
    agent. Operations already named in the message are left alone, so the two
    repairs compose rather than duplicate.
    """
    repairs: list[str] = []
    tasks = plan.get("build_tasks") or []

    for pack in plan.get("capability_packs") or []:
        if pack.get("surface_kind") != "module" or pack.get("capability_source") != "generated_module":
            continue
        operations = [str(op).strip() for op in (pack.get("operations") or []) if str(op).strip()]
        if not operations:
            continue
        module_id = str(_pack_id_from_descriptor(pack))
        contract = next(
            (
                task
                for task in tasks
                if task.get("task_type") == "module_contract"
                and str(_pack_id_from_descriptor(task)) == module_id
            ),
            None,
        )
        if contract is None:
            continue
        message = str(contract.get("initial_message") or "").rstrip()
        # Word-boundary, so record_checkin does not mask checkin.
        missing = [op for op in operations if not re.search(WORD + re.escape(op) + WORD, message)]
        if not missing:
            continue
        named = ", ".join(f"`{op}`" for op in operations)
        note = (
            f"Actions for this module (authoritative, from the approved plan): {named}. "
            "Emit every one of them in actions[] exactly once, using these ids verbatim. "
            "The prose above describes the module; this list defines it."
        )
        contract["initial_message"] = "\n\n".join(part for part in (message, note) if part)
        repairs.append(
            f"{module_id}: contract task {str(contract.get('task_id'))!r} did not name "
            f"{missing}; added the approved operation list"
        )
    return repairs


def validate_plan_origins(plan: dict[str, Any], context: Any) -> None:
    available = _context_available_pack_map(context)
    packs = plan.get("capability_packs") or []
    errors: list[str] = []
    for pack in packs:
        pack_id = _pack_id_from_descriptor(pack)
        source = pack.get("capability_source")
        registered = available.get(pack_id)
        if source == "managed_capability" and not registered:
            errors.append(f"{pack_id}: managed_capability requires a registered provider pack; a product category is not a managed service")
        if source in {"framework_pack", "operator_pack"} and not registered:
            errors.append(f"{pack_id}: {source} requires an installed pack in capability_packs context. For app-owned code use generated_module with capability_pack_id=surface_id={pack.get('surface_id')!r}; product categories belong only in pack_type")
        if registered and source != registered.get("capability_source"):
            errors.append(f"{pack_id}: capability_source must match the registered pack ({registered.get('capability_source')})")

    design = detach(context.get("design_surface_map")) or {}
    for surface in design.get("surfaces") or []:
        if surface.get("owner") != "app" or surface.get("surface_kind") != "module":
            continue
        surface_id = surface["surface_id"]
        matching = [pack for pack in packs if pack.get("surface_id") == surface_id]
        if len(matching) != 1 or matching[0].get("surface_kind") != "module" or matching[0].get("capability_source") not in {"generated_module", "framework_pack", "operator_pack"}:
            # "found 0" alone does not say what to emit. The usual cause is a
            # product category used as an identity: upstream hints and
            # source_capability_packs are full of names like crud_pack, so a plan
            # declares those as capabilities and no pack carries the surface_id.
            detail = (
                f"{surface_id}: the approved app-owned module requires exactly one module "
                f"capability, normally generated_module; preserve its surface_id "
                f"(found {len(matching)})"
            )
            if not matching:
                declared = sorted({str(_pack_id_from_descriptor(pack)) for pack in packs})
                detail += (
                    f". Emit a generated_module capability with surface_id={surface_id!r} and "
                    f"capability_pack_id={surface_id!r}. Declared capabilities are {declared}; "
                    "a product category such as crud_pack or billing_pack belongs in pack_type, "
                    "never in capability_pack_id or surface_id"
                )
            errors.append(detail)
            continue
        pack = matching[0]
        if pack.get("capability_source") == "generated_module":
            if _pack_id_from_descriptor(pack) != surface_id:
                errors.append(
                    f"{surface_id}: generated module capability_pack_id must match its approved "
                    f"surface_id, not the source product category. Set it to {surface_id!r} "
                    f"(currently {_pack_id_from_descriptor(pack)!r}); the category belongs in pack_type"
                )
            if set(pack.get("primary_entities") or []) != set(surface.get("primary_entities") or []):
                errors.append(f"{surface_id}: preserve the approved primary_entities: {surface.get('primary_entities') or []}")

    for task in plan.get("build_tasks") or []:
        module_ids = {path.split("/")[1] for path in _normalized_owned_paths(task) if path.startswith("modules/")}
        if not module_ids:
            continue
        pack_id = task.get("capability_pack_id")
        matching = [pack for pack in packs if _pack_id_from_descriptor(pack) == pack_id]
        task_id = task.get("task_id")
        # One message for three different faults told the agent a disagreement
        # existed but not which signal was wrong or what to set it to. A live
        # build spent all three revision attempts re-emitting the same mismatch.
        if len(matching) != 1:
            declared = sorted({str(_pack_id_from_descriptor(pack)) for pack in packs})
            errors.append(
                f"{task_id}: capability_pack_id={pack_id!r} matches "
                f"{len(matching)} declared capabilities; it must name exactly one of {declared}"
            )
        elif len(module_ids) > 1:
            # Two faults shared one message. With several module directories,
            # "set it to sorted(module_ids)[0]" names whichever sorts first --
            # frequently the value already declared, so the instruction is a
            # no-op and the revision attempts re-emit the same plan. A live
            # build spent all three that way on
            # owned_paths spanning modules/crud_pack/ and modules/tasks/.
            errors.append(
                f"{task_id}: this task owns paths in {len(module_ids)} module directories "
                f"{sorted(module_ids)} under modules/. A module task writes exactly one module. "
                f"Split it into one task per module, each with capability_pack_id equal to the "
                f"module directory it writes. If {sorted(module_ids)} are meant to be the same "
                "module, a product category is being used as a directory name: the module "
                "directory is the surface_id, and the category belongs in pack_type."
            )
        elif module_ids != {pack_id}:
            owned = sorted(module_ids)[0]
            errors.append(
                f"{task_id}: this task owns modules/{owned}/ and declares "
                f"surface_id={task.get('surface_id')!r}, but claims capability_pack_id={pack_id!r}. "
                "A module task's capability_pack_id is the module directory it writes. Set it to "
                f"{owned!r}, or move the files to the module that capability owns."
            )
        elif task.get("surface_id") != matching[0].get("surface_id"):
            errors.append(
                f"{task_id}: surface_id={task.get('surface_id')!r} disagrees with capability "
                f"{pack_id!r}, which declares surface_id={matching[0].get('surface_id')!r}. "
                "Use the capability's surface_id."
            )

    if errors:
        raise ValueError("Plan ownership errors:\n- " + "\n- ".join(errors))


def validate_plan_coverage(plan: dict[str, Any], context: Any) -> None:
    tasks = plan.get("build_tasks") or []
    if not tasks:
        raise ValueError("A build plan must declare materializing build_tasks, not just a page or capability inventory")
    if context.get("build_mode") == "revision" or context.get("brownfield_build_path"):
        return

    errors: list[str] = []
    pages = plan.get("pages") or []
    planned_routes = [page.get("route") for page in pages]
    if len(set(planned_routes)) != len(planned_routes):
        errors.append("page routes must be unique")
    experience = detach(context.get("experience_spec")) or {}
    expected_pages = {(page["name"], page["route"]) for page in experience.get("pages") or []}
    if expected_pages and {(page["name"], page["route"]) for page in pages} != expected_pages:
        errors.append(f"pages must preserve the approved name/route inventory: {sorted(expected_pages)}")
    page_paths = [f"ui/pages/{_page_file_stem(page)}.yaml" for page in pages]
    if len(set(page_paths)) != len(page_paths):
        errors.append("page routes resolve to colliding materialized filenames")
    page_owned = {
        path for task in tasks if task.get("task_type") == "page_bundle"
        for path in _normalized_owned_paths(task)
    }
    missing = {"app.json", *page_paths} - page_owned
    if missing:
        errors.append(f"page_bundle/AppSchemaAgent must own all page files and app.json; missing {sorted(missing)}")
        errors.append(
            "Page paths are case-sensitive and use the materializer's lowercase route-derived stem, "
            f"not the display name. Required page paths: {page_paths}; received page_bundle paths: {sorted(page_owned)}. "
            "Replace differently cased filenames; do not add both spellings."
        )

    for pack in plan.get("capability_packs") or []:
        if pack.get("surface_kind") != "module" or pack.get("capability_source") != "generated_module":
            continue
        module_id = _pack_id_from_descriptor(pack)
        module_tasks = [task for task in tasks if task.get("capability_pack_id") == module_id]
        required = {
            "module_contract": {f"modules/{module_id}/module.yaml"},
            "data_models": {f"modules/{module_id}/backend/schemas.py"},
            "business_services": {f"modules/{module_id}/backend/{name}.py" for name in ("handler", "service")},
        }
        if pack.get("primary_entities"):
            required["business_services"].update(f"modules/{module_id}/backend/{name}.py" for name in ("repo", "policy"))
        if pack.get("user_data_scope") is True:
            required["business_services"].add(f"modules/{module_id}/backend/account_data_handler.py")
        for kind, paths in required.items():
            owned = {path for task in module_tasks if task.get("task_type") == kind for path in _normalized_owned_paths(task)}
            if paths - owned:
                errors.append(f"{module_id}/{kind} is incomplete; missing {sorted(paths - owned)}")
    if errors:
        raise ValueError("Incomplete build plan:\n- " + "\n- ".join(errors))


def review_app_build_plan(
    *,
    AppBuildPlan: Annotated[dict[str, Any] | None, "Complete AppBuildPlan output"] = None,
    context_variables: Annotated[Any | None, "Runtime-owned workflow context"] = None,
) -> dict[str, Any]:
    if context_variables is None:
        raise ValueError("Plan review requires runtime context")
    _clear_plan(context_variables)
    context_variables.set("app_plan_outcome", "blocked")
    attempts = context_variables.get("app_plan_attempts") or 0
    if type(attempts) is not int or attempts < 0:
        raise ValueError("Invalid runtime plan attempt counter")
    if attempts >= 3:
        return {"outcome": "blocked", "error": "Plan review attempt budget exhausted"}
    attempts += 1
    context_variables.set("app_plan_attempts", attempts)
    try:
        models, _ = load_workflow_structured_outputs("AppGenerator")
        plan = models["AppBuildPlan"].model_validate(detach(AppBuildPlan)).model_dump(mode="json")
        for repair in (
            *_repair_plan(plan, context_variables),
            *_repair_missing_read_operation(plan, context_variables),
            *_repair_coverage(plan, context_variables),
            *_repair_contract_task_operations(plan, context_variables),
            *_repair_page_contract_dependencies(plan, context_variables),
        ):
            logger.info("[AppGenerator] plan repaired: %s", repair)
        validate_plan_dependencies(plan, context_variables)
        validate_plan_origins(plan, context_variables)
        validate_plan_coverage(plan, context_variables)
        app_build_plan(AppBuildPlan=plan, context_variables=context_variables)
        cached = detach(context_variables.get("app_build_plan"))
        if not context_variables.get("app_plan_ready") or not isinstance(cached, dict):
            raise RuntimeError("Validated plan was not cached")
        validate_plan_dependencies(cached, context_variables)
        validate_plan_coverage(cached, context_variables)
    except ValueError as error:
        _clear_plan(context_variables)
        feedback = str(error)[:6000]
        context_variables.set("app_plan_feedback", feedback)
        outcome = "needs_revision" if attempts < 3 else "blocked"
        context_variables.set("app_plan_outcome", outcome)
        # Without ok=False the runtime's failure detector reads a rejection as
        # success, so three rejected plans logged as three clean completions and
        # the real validator errors never reached the log at all.
        return {"ok": False, "outcome": outcome, "error": feedback}
    except BaseException:
        _clear_plan(context_variables)
        raise
    context_variables.set("app_plan_feedback", "")
    context_variables.set("app_plan_outcome", "ready")
    return {"outcome": "ready", "task_count": len(cached["build_tasks"])}
