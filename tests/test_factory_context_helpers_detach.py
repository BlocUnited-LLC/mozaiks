"""A context read that gets type-tested must return plain data, not a frozen view.

`ContextVariablesBridge.get` returns `freeze(value)`, so every live read hands
back a `MappingProxyType`, and `isinstance(x, dict)` on it is False. A tool that
type-tests an undetached read does not crash -- it takes the else branch and
derives nothing. The build completes and the artifact is quietly poorer, which
is why all three instances so far were found by a live run rather than by CI:

  #708  DesignDocs `_reject_undeclared_workflow_surfaces` never fired in any
        real build; its tests passed because they used plain dicts.
  #709  the same shape in SubscriptionContractDesigner's monetization guard.
  here  `save_app_schema._derive_module_id_for_page` and
        `_derive_submit_action_id` returned None in production for every page,
        so generated pages were never bound to their owning module or submit
        action from `app_build_plan`:

            plain dict (what the tests use):  'tasks'
            live production container:         None

The first two were fixed at the call site, leaving the helper wrong and the
next reader exposed. This pins the helper.

The rule enforced here is the harm condition, not a style preference: a helper
may return a frozen value only while nothing type-tests its result. Adding an
`isinstance(..., dict)` on an undetached read fails this test, so the next
instance is caught when it is written instead of on a live build.

`detach()` before dictionary validation is the documented convention --
docs/architecture/app/generated-app-functional-acceptance.md.

Twenty-six near-identical copies of this helper exist across factory tools;
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
TOOLS = sorted((ROOT / "factory_app/workflows").glob("*/tools/*.py"))
HELPER_NAMES = {"_context_get", "_cv_get"}


def _module_path(path: pathlib.Path) -> str:
    return path.relative_to(ROOT).with_suffix("").as_posix().replace("/", ".")


def _helper_names(tree: ast.AST) -> set[str]:
    return {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in HELPER_NAMES
    }


def _type_tested_reads(tree: ast.AST) -> list[tuple[str, str, int]]:
    """Sites doing isinstance(x, dict) where x came straight from a helper read."""
    found: list[tuple[str, str, int]] = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        reads: dict[str, str] = {}
        for node in ast.walk(fn):
            if (
                isinstance(node, ast.Assign)
                and isinstance(node.value, ast.Call)
                and getattr(node.value.func, "id", None) in HELPER_NAMES
                and node.targets
                and isinstance(node.targets[0], ast.Name)
            ):
                key = (
                    node.value.args[1].value
                    if len(node.value.args) > 1 and isinstance(node.value.args[1], ast.Constant)
                    else "?"
                )
                reads[node.targets[0].id] = str(key)
        for node in ast.walk(fn):
            if (
                isinstance(node, ast.Call)
                and getattr(node.func, "id", None) == "isinstance"
                and len(node.args) == 2
                and isinstance(node.args[0], ast.Name)
                and node.args[0].id in reads
                and getattr(node.args[1], "id", None) == "dict"
            ):
                found.append((fn.name, reads[node.args[0].id], node.lineno))
    return found


def _returns_plain_data(path: pathlib.Path, name: str) -> bool | None:
    """Call the real helper with the real production container. None if uncallable."""
    try:
        helper = getattr(importlib.import_module(_module_path(path)), name, None)
    except Exception:  # pragma: no cover - an unimportable tool is another test's problem
        return None
    if helper is None:
        return None
    live = StructuredOutputOverlay(ContextVariablesBridge({"probe": {"a": 1}}), {})
    try:
        return isinstance(helper(live, "probe"), dict)
    except TypeError:  # pragma: no cover - unusual signature
        return None


CASES = [(path, name) for path in TOOLS for name in sorted(_helper_names(ast.parse(path.read_text(encoding="utf-8"))))]


@pytest.mark.parametrize(
    ("path", "name"),
    CASES,
    ids=[f"{p.parent.parent.name}/{p.name}::{n}" for p, n in CASES],
)
def test_a_type_tested_read_returns_plain_data(path: pathlib.Path, name: str) -> None:
    """The harm condition: frozen result + isinstance(..., dict) = a branch that never runs."""
    sites = _type_tested_reads(ast.parse(path.read_text(encoding="utf-8")))
    if not sites:
        pytest.skip("no isinstance(..., dict) on this helper's results")
    plain = _returns_plain_data(path, name)
    if plain is None:
        pytest.skip("helper not callable in isolation")
    listed = ", ".join(f"{fn}() key={key!r} line {line}" for fn, key, line in sites)
    assert plain, (
        f"{path.relative_to(ROOT).as_posix()}::{name} returns a frozen value, and these "
        f"sites type-test its result against dict: {listed}. Live containers freeze on "
        "read, so each of those branches is dead in production -- the caller silently "
        "derives nothing. Wrap the helper's returned reads in detach()."
    )


def test_the_known_good_helpers_have_not_regressed() -> None:
    """The ones fixed here, pinned by behaviour rather than by source shape."""
    fixed = [
        ("factory_app/workflows/AppGenerator/tools/save_app_schema.py", "_context_get"),
        ("factory_app/workflows/AgentGenerator/tools/workflow_quality_gate.py", "_context_get"),
        ("factory_app/workflows/DesignDocs/tools/save_design_doc.py", "_cv_get"),
        ("factory_app/workflows/SubscriptionContractDesigner/tools/save_subscription_contract.py", "_cv_get"),
        ("factory_app/workflows/RuntimeTaskBatchSmoke/tools/hook_task_batch_synthesis.py", "_context_get"),
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
