"""One catalog of standard admin pages, everywhere.

``mozaiksai/core/admin/contract.py`` owns the standard admin pages. The
generator contract, the generator's structured-output schema and prompts, the
frontend, and the docs each repeat that list in a form they can consume. These
tests fail when any copy differs, so a page cannot exist in one place and not
another: that drift is how the portal ended up with three different slot lists.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import get_args

import yaml

from factory_app.workflows.AppGenerator.tools.app_backend_admin_contract import (
    APP_BACKEND_ADMIN_SECTION_IDS,
    AppBackendAdminConfig,
    AppBackendAdminPanel,
)
from mozaiksai.core.admin.contract import (
    DEFAULT_RUNTIME_PANELS,
    STANDARD_ADMIN_PAGE_IDS,
    STANDARD_ADMIN_PAGES,
)
from mozaiksai.core.admin.registry import AdminRegistry

ROOT = Path(__file__).resolve().parents[1]
CANONICAL_LINE = " | ".join(STANDARD_ADMIN_PAGE_IDS)


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def _frontend_catalog() -> list[dict]:
    module_uri = (ROOT / "chat-ui/src/admin/contracts/adminPages.js").resolve().as_uri()
    script = f"""
      import {{ STANDARD_ADMIN_PAGES, STANDARD_ADMIN_PAGE_IDS }} from {json.dumps(module_uri)};
      import {{ APP_ADMIN_SECTION_IDS }} from {json.dumps(
          (ROOT / "chat-ui/src/admin/contracts/appAdminContract.js").resolve().as_uri()
      )};
      console.log(JSON.stringify({{
        pages: STANDARD_ADMIN_PAGES,
        ids: STANDARD_ADMIN_PAGE_IDS,
        sections: APP_ADMIN_SECTION_IDS,
      }}));
    """
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def test_catalog_is_a_valid_registry() -> None:
    registry = AdminRegistry.model_validate(
        {"pages": [{**page, "scope": "app"} for page in STANDARD_ADMIN_PAGES]}
    )
    assert [page.id for page in registry.enabled_pages()] == list(STANDARD_ADMIN_PAGE_IDS)
    assert len(set(STANDARD_ADMIN_PAGE_IDS)) == len(STANDARD_ADMIN_PAGE_IDS)
    assert len({page["path"] for page in STANDARD_ADMIN_PAGES}) == len(STANDARD_ADMIN_PAGES)
    # Generated-app admin routes stay in the /admin family.
    assert all(page["path"] == "/admin" or page["path"] == f"/admin/{page['id']}" for page in STANDARD_ADMIN_PAGES)


def test_app_backend_panel_sections_are_the_standard_pages() -> None:
    assert APP_BACKEND_ADMIN_SECTION_IDS == STANDARD_ADMIN_PAGE_IDS
    assert get_args(AppBackendAdminPanel.model_fields["section"].annotation) == STANDARD_ADMIN_PAGE_IDS


def test_structured_output_schema_names_the_standard_pages() -> None:
    schema = yaml.safe_load(_read("factory_app/workflows/AppGenerator/structured_outputs.yaml"))
    section = schema["models"]["AppBackendAdminPanel"]["fields"]["section"]
    assert tuple(section["values"]) == STANDARD_ADMIN_PAGE_IDS


def test_frontend_catalog_matches() -> None:
    frontend = _frontend_catalog()
    assert tuple(frontend["ids"]) == STANDARD_ADMIN_PAGE_IDS
    assert tuple(frontend["sections"]) == STANDARD_ADMIN_PAGE_IDS
    assert [(page["id"], page["label"]) for page in frontend["pages"]] == [
        (page["id"], page["label"]) for page in STANDARD_ADMIN_PAGES
    ]
    # Every standard page tells the admin what it is for.
    assert all(page.get("description") for page in frontend["pages"])


def test_admin_registry_agent_declares_each_standard_page_exactly() -> None:
    agents = _read("factory_app/workflows/AppGenerator/agents.yaml")
    guidance = agents[agents.index("- name: AdminRegistryAgent"):]
    declared = re.findall(
        r"^\s*- `([a-z-]+)` — label `([^`]+)`; path `([^`]+)`; icon `([^`]+)`; order (\d+)\s*$",
        guidance,
        flags=re.MULTILINE,
    )
    assert declared == [
        (page["id"], page["label"], page["path"], page["icon"], str(page["order"]))
        for page in STANDARD_ADMIN_PAGES
    ]


def _agent_sections(agents: str) -> dict[str, str]:
    """The text of each agent's entry in agents.yaml, by agent name."""
    starts = [(match.start(), match.group(1)) for match in re.finditer(r"^- name: (\w+)\s*$", agents, flags=re.MULTILINE)]
    return {
        name: agents[start : (starts[index + 1][0] if index + 1 < len(starts) else len(agents))]
        for index, (start, name) in enumerate(starts)
    }


def test_generator_prompts_place_panels_only_on_standard_pages() -> None:
    agents = _read("factory_app/workflows/AppGenerator/agents.yaml")
    placing = {
        name: set(re.findall(r"→ `page: ([a-z-]+)`", text))
        for name, text in _agent_sections(agents).items()
        if "→ `page: " in text
    }
    # The planner, the module contract agent, and the registry agent each state
    # the placement rule. Each copy must name every standard page, so one copy
    # cannot lose a page while the others hide it.
    assert {"AppPlanAgent", "AdminRegistryAgent"} <= set(placing)
    assert len(placing) == 3
    for name, placed in placing.items():
        assert placed == set(STANDARD_ADMIN_PAGE_IDS), f"{name} places panels on {sorted(placed)}"
    # A module panel names its page with `page`; `section` belongs to app-backend panels.
    assert "→ `section: " not in agents


def _pack_template_admin_pages() -> dict[str, set[str]]:
    """Page ids the shipped capability packs' own admin panels render on, by pack id."""
    pages: dict[str, set[str]] = {}
    for contract in sorted((ROOT / "factory_app/build_context").glob("*/templates/modules/*/contracts/admin.yaml")):
        pack_id = contract.relative_to(ROOT / "factory_app/build_context").parts[0]
        panels = yaml.safe_load(contract.read_text(encoding="utf-8")).get("panels") or []
        pages.setdefault(pack_id, set()).update(panel["page"] for panel in panels)
    return pages


def test_registry_agent_declares_the_pages_capability_pack_panels_use() -> None:
    """A pack's admin.yaml is copied in at assembly, after the registry agent runs.

    The agent cannot read those panels, so its prompt lists each pack's page.
    If a pack gains a panel on another page and this list is not updated, the
    first registry misses the page and the bundle scanner rejects the bundle.
    """
    shipped = _pack_template_admin_pages()
    assert shipped, "expected at least one capability pack with admin panels"
    registry_agent = _agent_sections(_read("factory_app/workflows/AppGenerator/agents.yaml"))["AdminRegistryAgent"]
    listed: dict[str, set[str]] = {}
    for pack_id, page in re.findall(r"^\s*- `([a-z_]+)` pack: page `([a-z-]+)`\s*$", registry_agent, flags=re.MULTILINE):
        listed.setdefault(pack_id, set()).add(page)
    assert listed == shipped
    # The plan field the agent matches against is named, and it is told why.
    assert "`capability_pack_id`" in registry_agent
    assert "after you run" in registry_agent


def test_registry_agent_inclusion_rules() -> None:
    registry_agent = _agent_sections(_read("factory_app/workflows/AppGenerator/agents.yaml"))["AdminRegistryAgent"]
    # The four pages the framework fills are always declared.
    assert "- `overview`, `usage`, `operations`, and `settings` — always include." in registry_agent
    assert "1. Always include `overview`, `usage`, `operations`, and `settings`." in registry_agent
    # Access follows sign-in: DesignDocs routes user administration to it.
    assert "- `access` — include when the app has sign-in (`app_build_plan.auth_strategy`" in registry_agent
    # The pages with no framework content are declared only for a panel.
    assert "- `billing`, `activity`, `integrations`, and `support` — the framework shows" in registry_agent
    # Both places a panel reference lives are named by field.
    assert "`admin_yaml.panels[].page`" in registry_agent
    assert "`app_backend_admin_config.panels[].section`" in registry_agent
    # The retired rule (pages inferred from whether the app "runs AI workflows") is gone.
    assert "runs AI workflows" not in registry_agent
    assert "agent_backend_required" not in registry_agent


def test_controller_example_validates_against_its_own_output_schema() -> None:
    """The panel shape the controller's prompt shows must be one its schema accepts.

    A review round replaced this example with a schema panel carrying
    ``config.api_endpoint``. The controller's structured-output model forbids
    that field, so an agent following the example would have been refused.
    """
    controller = _agent_sections(_read("factory_app/workflows/AppGenerator/agents.yaml"))["ControllerAgent"]
    example = re.search(r'"panels": (\[\{.*\}\])\s*$', controller, flags=re.MULTILINE)
    assert example, "ControllerAgent must show an app-backend admin panel example"
    panels = json.loads(example.group(1).replace('"..."', '"x"'))
    assert len(panels) == 1
    panel = panels[0]
    # No field outside the typed panel model, and no free-form endpoint string.
    schema = yaml.safe_load(_read("factory_app/workflows/AppGenerator/structured_outputs.yaml"))
    assert set(panel) <= set(schema["models"]["AppBackendAdminPanel"]["fields"])
    assert "api_endpoint" not in example.group(1)
    # With real values in place of the placeholders it is a valid config.
    AppBackendAdminConfig.model_validate(
        {
            "schema_version": "mozaiks.admin.app_backend.v1",
            "panels": [{**panel, "section": "overview", "builtin_panel": "stats"}],
        }
    )


def test_registry_icon_hints_resolve_in_the_portal() -> None:
    layout = _read("chat-ui/src/admin/components/AdminWorkspaceLayout.jsx")
    icon_map = layout[layout.index("const ICON_MAP = {"):]
    icon_keys = set(re.findall(r"^\s*([a-z]+):\s+Ri", icon_map[: icon_map.index("}")], flags=re.MULTILINE))
    assert {page["icon"] for page in STANDARD_ADMIN_PAGES} <= icon_keys


def test_runtime_panels_bind_to_standard_pages_the_portal_renders() -> None:
    panel_sources = {
        "usage": _read("chat-ui/src/admin/pages/UsageSection.jsx"),
        "operations": _read("chat-ui/src/admin/pages/OperationsSection.jsx"),
    }
    assert [(panel["id"], panel["page"]) for panel in DEFAULT_RUNTIME_PANELS] == [
        ("stats", "usage"),
        ("runs", "usage"),
        ("sessions", "operations"),
    ]
    for panel in DEFAULT_RUNTIME_PANELS:
        assert panel["page"] in STANDARD_ADMIN_PAGE_IDS
        # The page's section file registers a component under this panel id and label.
        source = panel_sources[panel["page"]]
        assert re.search(rf"^\s*{panel['id']}:\s+\{{ label: '{re.escape(panel['label'])}'", source, flags=re.MULTILINE)

    # The fallback used before /api/admin/config answers names the same bindings.
    admin_page = _read("chat-ui/src/pages/AdminPage.jsx")
    assert re.findall(r"\{ id: '([a-z]+)', page: '([a-z]+)' \}", admin_page) == [
        (panel["id"], panel["page"]) for panel in DEFAULT_RUNTIME_PANELS
    ]


def test_docs_state_the_standard_pages_once_each() -> None:
    for doc in (
        "docs/architecture/app/admin-system.md",
        "docs/architecture/app/admin-ui-tiers.md",
        "docs/architecture/builder/app-backend-admin-contract.md",
    ):
        text = _read(doc)
        assert CANONICAL_LINE in text, f"{doc} must list the standard admin pages as: {CANONICAL_LINE}"
        # No second, different pipe-separated list of admin page ids.
        for listed in re.findall(r"(?:[a-z]+ \| ){3,}[a-z]+", text):
            members = listed.split(" | ")
            if set(members) <= set(STANDARD_ADMIN_PAGE_IDS):
                assert listed == CANONICAL_LINE, f"{doc} lists a different set of admin pages: {listed}"
