"""Characterize fidelity guidance and its early prompt-projection boundary.

These are source/contract tests, not live generation or visual-quality scores.
"""

from __future__ import annotations

import ast
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from tests.import_utils import import_module_directly

ui_surface_taxonomy = import_module_directly("mozaiksai.core.workflow.ui_surface_taxonomy")
ROOT = Path(__file__).resolve().parents[1]
HEADER = "[UI SURFACE TAXONOMY]"


class _Agent:
    def __init__(self, name: str, message: str = "[ROLE]\nDesign this app.") -> None:
        self.name = name
        self.system_message = message
        self.context_variables = {"approved_design": {"revision": "design-a"}}
        self.updates = 0

    def update_system_message(self, message: str) -> None:
        self.system_message = message
        self.updates += 1


def _hook():
    return import_module_directly("factory_app.workflows._shared.ui_surface_context")


def test_experience_policy_keeps_the_existing_surface_vocabulary() -> None:
    assert ui_surface_taxonomy.get_ui_surface_ids() == (
        "declarative_page", "custom_react_page", "agent_tool", "transition",
    )
    assert ui_surface_taxonomy.validate_ui_surface_ids(
        ["declarative_page", "custom_react_page", "declarative_page"], context="fidelity",
    ) == ["declarative_page", "custom_react_page"]


@pytest.mark.parametrize("invented", ["editorial_page", "canvas_page", "nextgen_page", True])
def test_experience_policy_does_not_make_styles_runtime_surface_types(invented) -> None:
    with pytest.raises(ValueError, match="unsupported UI surfaces"):
        ui_surface_taxonomy.validate_ui_surface_ids([invented], context="fidelity")


@pytest.mark.parametrize("dimension", [
    "composition", "visual hierarchy", "responsive behavior", "interaction",
])
def test_experience_policy_accounts_for_more_than_backend_features(dimension: str) -> None:
    surfaces = {entry.surface_id: entry for entry in ui_surface_taxonomy.get_ui_surface_contracts()}
    assert dimension in surfaces["declarative_page"].use_when
    assert dimension in surfaces["custom_react_page"].use_when


def test_experience_policy_does_not_reject_custom_ui_based_on_page_category() -> None:
    custom = next(
        entry for entry in ui_surface_taxonomy.get_ui_surface_contracts()
        if entry.surface_id == "custom_react_page"
    )
    assert "category alone neither requires nor forbids" in custom.avoid_when
    assert "only brand token values" in custom.avoid_when
    assert "can be simplified" not in custom.avoid_when


def test_experience_policy_retains_authority_and_scoped_revision_boundaries() -> None:
    guidance = ui_surface_taxonomy.format_ui_surface_taxonomy_guidance()
    for clause in (
        "Use `declarative_page` by default",
        "all approved experience requirements survive",
        "Shared runtime contracts do not require a shared product layout",
        "existing ExperienceSpec",
        "task acceptance_criteria",
        "supplied approved theme and design context",
        "owning design/plan",
        "supported shell modes and extension contracts",
        "module authorization, context-write rules, or workflow response contracts",
        "built, validated, and registered before mounting",
    ):
        assert clause in guidance


@pytest.mark.parametrize(("workflow", "agent_name"), [
    ("DesignDocs", "DesignDocsAgent"), ("AppGenerator", "AppPlanAgent"),
])
def test_early_producers_receive_the_shared_taxonomy(workflow: str, agent_name: str) -> None:
    workflow_dir = ROOT / "factory_app" / "workflows" / workflow
    config = yaml.safe_load((workflow_dir / "middleware.yaml").read_text(encoding="utf-8"))
    entries = [
        entry for entry in config["prompt_middleware"]
        if entry.get("function") == "inject_ui_surface_context"
    ]
    assert entries == [{
        "agent": agent_name,
        "filename": "../_shared/ui_surface_context.py",
        "function": "inject_ui_surface_context",
    }]
    assert (workflow_dir / entries[0]["filename"]).resolve() == (
        ROOT / "factory_app/workflows/_shared/ui_surface_context.py"
    ).resolve()
    assert (workflow_dir / entries[0]["filename"]).is_file()


@pytest.mark.parametrize("name", ["DesignDocsAgent", "AppPlanAgent"])
def test_repeated_projection_replaces_one_section_without_replaying_context(name: str) -> None:
    hook = _hook()
    agent = _Agent(name)
    messages = [{"role": "user", "content": "Keep the approved editorial direction."}]
    messages_before = deepcopy(messages)
    context = agent.context_variables
    context_before = deepcopy(context)
    hook.inject_ui_surface_context(agent, messages)
    first = agent.system_message
    for _ in range(10):
        hook.inject_ui_surface_context(agent, messages)
    assert agent.system_message == first
    assert agent.system_message.count(HEADER) == 1
    assert agent.updates == 1
    assert ui_surface_taxonomy.format_ui_surface_taxonomy_guidance() in first
    assert messages == messages_before
    assert agent.context_variables is context
    assert context == context_before
    assert "design-a" not in first
    assert messages[0]["content"] not in first


def test_projection_replaces_old_guidance_and_preserves_following_sections() -> None:
    agent = _Agent(
        "DesignDocsAgent",
        f"[ROLE]\nPreserve this role.\n\n{HEADER}\nOld guidance.\n\n[CONTEXT]\nKeep this context.",
    )
    _hook().inject_ui_surface_context(agent, [])
    first = agent.system_message
    _hook().inject_ui_surface_context(agent, [])
    assert "Old guidance." not in first
    assert first.startswith("[ROLE]\nPreserve this role.")
    assert first.endswith("[CONTEXT]\nKeep this context.")
    assert first.count(HEADER) == 1
    assert agent.system_message == first
    assert agent.updates == 1


@pytest.mark.parametrize("name", ["AppSchemaAgent", "ServiceAgent", "UIFileGenerator", "", None])
def test_shared_hook_does_not_change_other_agents(name) -> None:
    agent = _Agent(name)
    before = agent.system_message
    _hook().inject_ui_surface_context(agent, [])
    assert agent.system_message == before
    assert agent.updates == 0


def test_shared_hook_ignores_objects_without_an_agent_name() -> None:
    agent = SimpleNamespace(system_message="untouched")
    _hook().inject_ui_surface_context(agent, [])
    assert vars(agent) == {"system_message": "untouched"}


def test_projection_reads_the_canonical_owner_instead_of_a_second_policy(monkeypatch) -> None:
    hook = _hook()
    monkeypatch.setattr(hook, "format_ui_surface_taxonomy_guidance", lambda: "CURRENT OWNER CONTENT")
    agent = _Agent("AppPlanAgent")
    hook.inject_ui_surface_context(agent, [])
    assert agent.system_message.endswith(f"{HEADER}\nCURRENT OWNER CONTENT")


def test_page_catalog_guidance_preserves_fidelity_without_weakening_bindings() -> None:
    source = (ROOT / "factory_app/workflows/AppGenerator/tools/hook_primitive_catalog.py").read_text(
        encoding="utf-8",
    )
    # Inspect actual emitted string literals, rather than comments or formatting.
    strings = "\n".join(
        node.value for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    )
    for clause in (
        "faithfully express the approved experience",
        "preserve the approved custom_react_page realization",
        "Do not invent primitive names",
        "request design/plan repair before changing it",
        "Do not render editable identifier, ownership, or server timestamp fields",
        "accepted module contracts",
        "require backend idempotency",
    ):
        assert clause in strings
