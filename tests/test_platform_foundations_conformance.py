from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

from tests.import_utils import import_module_directly

_contracts = import_module_directly("mozaiksai.core.automation.contracts")

ROOT = Path(__file__).resolve().parents[1]
PLATFORM = ROOT / "platform"

_EVENT_TYPE_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")


def _load_json(relative_path: str) -> dict:
    with open(PLATFORM / relative_path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def _load_yaml(relative_path: str) -> dict:
    with open(PLATFORM / relative_path, "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def test_platform_bundle_families_exist() -> None:
    required = [
        "app.json",
        "FLAGSHIP_USE_CASE.md",
        "config/navigation_config.json",
        "config/theme_config.json",
        "config/module_registry.json",
        "config/admin.json",
        "config/settings_config.json",
        "config/notifications_config.json",
        "config/subscription_config.json",
        "automations/event_catalog.json",
        "automations/routes.json",
        "workflows/_pack/workflow_graph.json",
    ]
    for relative_path in required:
        assert (PLATFORM / relative_path).exists(), f"missing platform example file: {relative_path}"


def test_navigation_uses_semantic_header_controls() -> None:
    navigation = _load_json("config/navigation_config.json")
    landing_spot = navigation.get("landing_spot")
    assert isinstance(landing_spot, str)
    assert landing_spot.startswith("/")
    pages = navigation.get("pages", [])
    assert any(page.get("path") == "/admin" and page.get("component") == "AdminPortal" for page in pages)
    assert any(page.get("path") == "/discover" and page.get("component") == "DiscoverPage" for page in pages)

    controls = navigation.get("header_controls", [])
    ids = [item.get("id") for item in controls if item.get("visible", True)]
    assert ids == ["UserProfile", "Notifications", "Discover"]
    discover_control = next(item for item in controls if item.get("id") == "Discover")
    assert discover_control.get("path") == "/discover"


def test_admin_config_declares_nav_zones() -> None:
    admin_cfg = _load_json("config/admin.json")
    navigation = admin_cfg.get("navigation", {})
    assert isinstance(navigation.get("topbar", []), list)
    assert isinstance(navigation.get("sidebar", []), list)
    assert navigation.get("topbar") or navigation.get("sidebar"), "admin.json should define at least one nav zone entry"


def test_module_registry_matches_module_declaratives() -> None:
    module_registry = _load_json("config/module_registry.json").get("modules", [])
    registry_names = {entry.get("name") for entry in module_registry}

    module_dirs = [path for path in (PLATFORM / "modules").iterdir() if path.is_dir()]
    declarative_names = set()
    for directory in module_dirs:
        module_json = directory / "module.json"
        handler_py = directory / "handler.py"
        ui_index = directory / "ui" / "index.js"
        if not module_json.exists():
            continue
        assert handler_py.exists(), f"module missing handler.py: {directory.name}"
        assert ui_index.exists(), f"module missing ui/index.js: {directory.name}"
        payload = json.loads(module_json.read_text(encoding="utf-8"))
        declarative_names.add(payload.get("name"))

    assert declarative_names == registry_names


def test_global_workflow_graph_declares_flagship_journey() -> None:
    graph = _load_json("workflows/_pack/workflow_graph.json")
    workflow_ids = {entry.get("id") for entry in graph.get("workflows", [])}
    assert workflow_ids == {"GreenRoom", "WritersRoom", "MainStage"}

    journeys = graph.get("journeys", [])
    assert journeys, "expected at least one global journey in workflow graph"
    backstage = next((item for item in journeys if item.get("id") == "backstage_showcase"), None)
    assert backstage is not None, "expected backstage_showcase journey"
    assert backstage.get("steps") == ["GreenRoom", "WritersRoom", "MainStage"]


def test_each_workflow_has_stable_contract_files() -> None:
    required_files = [
        "orchestrator.yaml",
        "agents.yaml",
        "handoffs.yaml",
        "context_variables.yaml",
        "structured_outputs.yaml",
        "tools.yaml",
        "ui_config.yaml",
        "hooks.yaml",
        "_pack/workflow_graph.json",
    ]

    for workflow_name in ("GreenRoom", "WritersRoom", "MainStage"):
        workflow_root = PLATFORM / "workflows" / workflow_name
        for relative_path in required_files:
            assert (workflow_root / relative_path).exists(), (
                f"workflow '{workflow_name}' missing '{relative_path}'"
            )
        assert any((workflow_root / "tools").glob("*.py")), f"workflow '{workflow_name}' missing tools/*.py"
        assert (workflow_root / "ui" / "index.js").exists(), f"workflow '{workflow_name}' missing ui/index.js"


def test_orchestrators_use_expected_runtime_modes() -> None:
    for workflow_name in ("GreenRoom", "WritersRoom", "MainStage"):
        orchestrator = _load_yaml(f"workflows/{workflow_name}/orchestrator.yaml")
        assert orchestrator.get("workflow_name") == workflow_name
        assert orchestrator.get("human_in_the_loop") is True
        assert orchestrator.get("startup_mode") in {"UserDriven", "AgentDriven"}


def test_writers_room_declares_mfj() -> None:
    writers_graph = _load_json("workflows/WritersRoom/_pack/workflow_graph.json")
    journeys = writers_graph.get("mid_flight_journeys", [])
    assert journeys, "expected WritersRoom to define mid_flight_journeys"
    mfj = journeys[0]
    assert mfj.get("id") == "writers_room_cycle"
    assert (mfj.get("fan_out") or {}).get("spawn_mode") == "workflow"
    assert (mfj.get("fan_in") or {}).get("inject_as") == "mfj_writers_room_results"


def test_ui_tool_emission_covers_inline_and_artifact() -> None:
    writers_tools = _load_yaml("workflows/WritersRoom/tools.yaml").get("tools", [])
    main_stage_tools = _load_yaml("workflows/MainStage/tools.yaml").get("tools", [])
    assert any(tool.get("tool_type") == "UI_Tool" for tool in writers_tools + main_stage_tools)

    set_board_code = (PLATFORM / "workflows" / "WritersRoom" / "tools" / "set_board.py").read_text(
        encoding="utf-8"
    )
    final_set_code = (PLATFORM / "workflows" / "MainStage" / "tools" / "final_set_stage.py").read_text(
        encoding="utf-8"
    )
    merged = set_board_code + "\n" + final_set_code
    assert 'display_type="inline"' in merged
    assert 'display_type="artifact"' in merged


def test_event_catalog_uses_domain_events_and_core_services() -> None:
    catalog = _load_json("automations/event_catalog.json")
    event_types = {entry.get("event_type") for entry in catalog.get("events", [])}
    assert event_types == {"report.requested", "module.executed"}

    for event_type in event_types:
        assert _EVENT_TYPE_RE.match(str(event_type))

    workflow_tokens = {"greenroom", "writersroom", "mainstage"}
    for event_type in event_types:
        lowered = str(event_type).lower()
        assert all(token not in lowered for token in workflow_tokens)


def test_automation_routes_cover_foundation_effect_surface() -> None:
    routes_payload = _load_json("automations/routes.json")
    _contracts.AutomationConfigBundle.model_validate(
        {
            "events": _load_json("automations/event_catalog.json").get("events", []),
            "routes": routes_payload.get("routes", []),
        }
    )

    routes = routes_payload.get("routes", [])
    enabled_kinds = {route.get("effect", {}).get("kind") for route in routes if route.get("enabled", True)}
    assert enabled_kinds == {"workflow.run", "workflow.resume"}


def test_automation_workflow_targets_exist_in_global_workflow_graph() -> None:
    routes = _load_json("automations/routes.json").get("routes", [])
    workflow_ids = {
        entry.get("id")
        for entry in _load_json("workflows/_pack/workflow_graph.json").get("workflows", [])
    }
    for route in routes:
        if not route.get("enabled", True):
            continue
        effect = route.get("effect", {})
        if effect.get("kind") in {"workflow.run", "workflow.resume"}:
            assert effect.get("workflow") in workflow_ids
            bindings = route.get("bindings", {})
            assert "app_id" in bindings
            assert "user_id" in bindings
