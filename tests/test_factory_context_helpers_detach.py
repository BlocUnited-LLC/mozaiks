"""A context read that gets type-tested must return plain data, not a frozen view.

`ContextVariablesBridge.get` returns `freeze(value)`, so every live read hands
back a `MappingProxyType` (or a tuple for a list), and `isinstance(x, dict)` or
`isinstance(x, list)` on it is False. A reader that type-tests an undetached
read does not crash -- it takes the else branch and derives nothing. The build
completes and the artifact is quietly poorer, which is why every instance so far
was found by a live run rather than by CI:

  #708  DesignDocs `_reject_undeclared_workflow_surfaces` never fired in any
        real build; its tests passed because they used plain dicts.
  #709  the same shape in SubscriptionContractDesigner's monetization guard.
  #712  `save_app_schema._derive_module_id_for_page` and
        `_derive_submit_action_id` returned None in production for every page,
        so generated pages were never bound to their owning module or submit
        action from `app_build_plan`.
  #718  the subscription contract injector reached for `.data`, which no live
        container has, and never ran.
  then  an audit that called every remaining reader both ways found thirteen
        more: the context-graph hook dumped `str(mappingproxy)` into every
        agent prompt, the managed-capabilities hook lost every facade, the
        AppGenerator before_chat hydration erased the trigger events it had
        just read, the module-contract quality gate audited zero files, four
        AI-pack hooks never injected, domain scoring ignored the concept,
        `collect_integration_needs` found nothing so `config/integrations.yaml`
        came out empty, and the runtime's `context_get` dropped media assets.

  #723  four before_chat lifecycle tools (the ExistingAppDiscovery collector,
        overview card and recovery card, and the ThemeCapture collector)
        passed frozen reads through `_coerce_mapping`/`_dict_value` wrappers.
        The #719 version of this guard only looked for an `isinstance` on the
        read itself, so a type test one call away was invisible to it: the
        recovery card never emitted, the discovery launch inputs were dropped,
        and the theme collector ignored the parent theme.

So the rule is now universal: every context-reading helper returns plain data.
Where the result goes afterwards cannot be traced statically, and a detached
copy costs no more than the frozen view it replaces. Reads made straight
through `context_variables.get(...)` and then type-tested are held to the same
rule, because every live container freezes those too.

`detach()` before dictionary validation is the documented convention --
docs/architecture/app/generated-app-functional-acceptance.md.

Many near-identical copies of this helper exist across factory tools and hooks;
consolidating them into `factory_app/workflows/_shared/` is tracked separately.
"""

from __future__ import annotations

import ast
import importlib
import pathlib

import pytest

from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge
from mozaiksai.core.workflow.context.structured_output_overlay import StructuredOutputOverlay

ROOT = pathlib.Path(__file__).resolve().parents[1]
FILES = sorted(
    list((ROOT / "factory_app/workflows").glob("*/tools/*.py"))
    + list((ROOT / "factory_app/workflows/_shared").rglob("*.py"))
    + list((ROOT / "mozaiksai/core/workflow/generator_support").glob("*.py"))
    + [ROOT / "mozaiksai/core/utils/context_vars.py", ROOT / "mozaiksai/core/media/middleware.py"]
)
HELPER_NAMES = {"_context_get", "_cv_get", "_ctx_get", "context_get", "_get"}
CONTAINER_NAMES = {"context_variables"}
TYPE_NAMES = {"dict", "list"}


def _module_path(path: pathlib.Path) -> str:
    return path.relative_to(ROOT).with_suffix("").as_posix().replace("/", ".")


def _helper_names(tree: ast.AST) -> set[str]:
    """Context-reading helpers: a known name whose first parameter is the context container."""
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in HELPER_NAMES
        and node.args.args
        and node.args.args[0].arg in {"context_variables", "context", "ctx", "cv"}
    }


def _read_source(value: ast.AST) -> tuple[str, str] | None:
    """('helper', key) for `helper(ctx, key)`, ('direct', key) for `context_variables.get(key)`; also inside `... or {}`."""
    if isinstance(value, ast.Call):
        func = value.func
        if isinstance(func, ast.Name) and func.id in HELPER_NAMES:
            key = value.args[1].value if len(value.args) > 1 and isinstance(value.args[1], ast.Constant) else "?"
            return (func.id, str(key))
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "get"
            and isinstance(func.value, ast.Name)
            and func.value.id in CONTAINER_NAMES
        ):
            key = value.args[0].value if value.args and isinstance(value.args[0], ast.Constant) else "?"
            return ("direct", str(key))
        return None
    if isinstance(value, ast.BoolOp):
        for operand in value.values:
            found = _read_source(operand)
            if found:
                return found
    return None


def _type_names(node: ast.Call) -> set[str]:
    target = node.args[1]
    if isinstance(target, ast.Name):
        return {target.id} & TYPE_NAMES
    if isinstance(target, ast.Tuple):
        return {element.id for element in target.elts if isinstance(element, ast.Name)} & TYPE_NAMES
    return set()


def _type_tested_reads(tree: ast.AST) -> list[tuple[str, str, str, int]]:
    """(function, source, key, line) for isinstance(x, dict|list) where x came straight from a context read."""
    found: list[tuple[str, str, str, int]] = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        reads: dict[str, tuple[str, str]] = {}
        for node in ast.walk(fn):
            if isinstance(node, ast.Assign) and node.targets and isinstance(node.targets[0], ast.Name):
                target, value = node.targets[0], node.value
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
                target, value = node.target, node.value
            else:
                continue
            source = _read_source(value)
            if source:
                reads[target.id] = source
        for node in ast.walk(fn):
            if (
                isinstance(node, ast.Call)
                and getattr(node.func, "id", None) == "isinstance"
                and len(node.args) == 2
                and isinstance(node.args[0], ast.Name)
                and node.args[0].id in reads
                and _type_names(node)
            ):
                source, key = reads[node.args[0].id]
                found.append((fn.name, source, key, node.lineno))
    return found


def _returns_plain_data(path: pathlib.Path, name: str) -> bool | None:
    """Call the real helper with the real production container. None if uncallable."""
    try:
        helper = getattr(importlib.import_module(_module_path(path)), name, None)
    except Exception:  # pragma: no cover - an unimportable module is another test's problem
        return None
    if helper is None:
        return None
    live = StructuredOutputOverlay(ContextVariablesBridge({"probe": {"a": 1}, "items": [{"b": 2}]}), {})
    try:
        return isinstance(helper(live, "probe"), dict) and isinstance(helper(live, "items"), list)
    except TypeError:  # pragma: no cover - unusual signature
        return None


def _parsed(path: pathlib.Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8"))


HELPER_CASES = [(path, name) for path in FILES for name in sorted(_helper_names(_parsed(path)))]
DIRECT_CASES = [path for path in FILES if any(source == "direct" for _, source, _, _ in _type_tested_reads(_parsed(path)))]


@pytest.mark.parametrize(
    ("path", "name"),
    HELPER_CASES,
    ids=[f"{p.parent.parent.name}/{p.name}::{n}" for p, n in HELPER_CASES],
)
def test_a_context_helper_returns_plain_data(path: pathlib.Path, name: str) -> None:
    """Frozen result + a type test anywhere downstream = a branch that never runs."""
    plain = _returns_plain_data(path, name)
    assert plain is not None, f"{path.relative_to(ROOT).as_posix()}::{name} is not callable as (context, key)"
    sites = [site for site in _type_tested_reads(_parsed(path)) if site[1] == name]
    listed = ", ".join(f"{fn}() key={key!r} line {line}" for fn, _, key, line in sites) or "none visible here"
    assert plain, (
        f"{path.relative_to(ROOT).as_posix()}::{name} returns a frozen value. Live containers "
        "freeze on read, so any isinstance(..., dict | list) on its result -- directly "
        f"(sites: {listed}) or inside a wrapper such as _coerce_mapping or _dict_value -- is "
        "dead in production and the caller silently derives nothing. Wrap the helper's "
        "returned reads in detach()."
    )


@pytest.mark.parametrize("path", DIRECT_CASES, ids=[f"{p.parent.parent.name}/{p.name}" for p in DIRECT_CASES])
def test_a_direct_container_read_is_not_type_tested(path: pathlib.Path) -> None:
    """`context_variables.get(...)` is frozen by construction; type-testing it needs detach() at the read."""
    sites = [site for site in _type_tested_reads(_parsed(path)) if site[1] == "direct"]
    listed = ", ".join(f"{fn}() key={key!r} line {line}" for fn, _, key, line in sites)
    pytest.fail(
        f"{path.relative_to(ROOT).as_posix()} type-tests a value read straight from "
        f"context_variables.get(): {listed}. The bridge freezes that read, so the branch is "
        "dead in production. Read it as detach(context_variables.get(...)) or through a detaching helper."
    )


def test_the_known_good_helpers_have_not_regressed() -> None:
    """The ones fixed so far, pinned by behaviour rather than by source shape."""
    fixed = [
        ("factory_app/workflows/AppGenerator/tools/save_app_schema.py", "_context_get"),
        ("factory_app/workflows/AgentGenerator/tools/workflow_quality_gate.py", "_context_get"),
        ("factory_app/workflows/DesignDocs/tools/save_design_doc.py", "_cv_get"),
        ("factory_app/workflows/SubscriptionContractDesigner/tools/save_subscription_contract.py", "_cv_get"),
        ("factory_app/workflows/RuntimeTaskBatchSmoke/tools/hook_task_batch_synthesis.py", "_context_get"),
        ("factory_app/workflows/_shared/workflow_integration.py", "_context_get"),
        ("factory_app/workflows/AppGenerator/tools/review_module_contract_quality.py", "_context_get"),
        ("factory_app/workflows/AppGenerator/tools/hook_ai_pack_workflow_context.py", "_context_get"),
        ("factory_app/workflows/AppGenerator/tools/ui_quality.py", "_context_get"),
        ("factory_app/workflows/AppGenerator/tools/save_admin_registry.py", "_context_get"),
        ("factory_app/workflows/AppGenerator/tools/generate_and_download.py", "_context_get"),
        ("factory_app/workflows/AgentGenerator/tools/hook_ai_pack_archetype_context.py", "_context_get"),
        ("factory_app/workflows/DesignDocs/tools/hook_ai_pack_surface_context.py", "_context_get"),
        ("factory_app/workflows/ExistingAppDiscovery/tools/source_context_retrieval.py", "_ctx_get"),
        ("mozaiksai/core/workflow/generator_support/connector_request.py", "_context_get"),
        ("mozaiksai/core/utils/context_vars.py", "context_get"),
    ]
    for rel, name in fixed:
        assert _returns_plain_data(ROOT / rel, name) is True, f"{rel}::{name} returns a frozen value again"


def test_the_live_defect_stays_fixed() -> None:
    """The actual production symptom: a page bound to its module through the real container."""
    from factory_app.workflows.AppGenerator.tools.save_app_schema import _derive_module_id_for_page

    plan = {"modules": [{"module_id": "tasks"}], "pages": [{"route": "/tasks"}]}
    live = StructuredOutputOverlay(ContextVariablesBridge({"app_build_plan": plan}), {})
    assert _derive_module_id_for_page({"route": "/tasks"}, live) == "tasks", (
        "this returned None for every page in production while returning 'tasks' in tests"
    )


def test_the_premise_still_holds() -> None:
    """If bridge reads ever stop freezing, this whole guard is obsolete -- say so here."""
    value = ContextVariablesBridge({"probe": {"a": 1}}).get("probe")
    assert not isinstance(value, dict), (
        "ContextVariablesBridge.get no longer freezes. This file exists because it did; "
        "revisit it rather than leaving it asserting a dead contract."
    )
