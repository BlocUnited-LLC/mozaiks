"""
Tests for factory_app.control_plane.tools._module_inventory.

All tests use synthetic in-memory file_maps — no filesystem, no database,
no network access.
"""
from __future__ import annotations

import copy

from factory_app.control_plane.tools._module_inventory import (
    ModuleInventoryEntry,
    classify_module_carry_forward,
    extract_module_inventory,
)

# ---------------------------------------------------------------------------
# Synthetic fixtures
# ---------------------------------------------------------------------------

_PROJECTS_MODULE_YAML = """\
schema_version: mozaiks.module.v1
module:
  id: projects
  display_name: Projects
  version: 1.0.0
  description: Project management module.
  owner: app
  visibility: private
  handler: backend.handler:ProjectsModule
actions:
  - id: create_project
    description: Create a new project.
    handler_method: create_project
    emits:
      - domain.projects.project_created
  - id: list_projects
    description: List projects.
    handler_method: list_projects
  - id: update_project
    description: Update a project.
    handler_method: update_project
    emits:
      - domain.projects.project_updated
  - id: delete_project
    description: Delete a project.
    handler_method: delete_project
"""

_PROJECTS_EVENTS_YAML = """\
schema_version: mozaiks.events.v1
events:
  - type: domain.projects.project_created
    version: 1
    description: Emitted when a project is created.
    producer: projects
  - type: domain.projects.project_updated
    version: 1
    description: Emitted when a project is updated.
    producer: projects
"""

_LANDING_MODULE_YAML = """\
schema_version: mozaiks.module.v1
module:
  id: landing
  display_name: Landing
  version: 1.0.0
  description: Static landing page module.
  owner: app
  visibility: public
  handler: backend.handler:LandingModule
actions:
  - id: get_landing_content
    description: Return landing page content.
    handler_method: get_landing_content
"""

# Full module: all contracts + all backend files
_FULL_FILE_MAP: dict[str, str] = {
    # module.yaml
    "modules/projects/module.yaml": _PROJECTS_MODULE_YAML,
    # contracts
    "modules/projects/contracts/events.yaml": _PROJECTS_EVENTS_YAML,
    "modules/projects/contracts/reactions.yaml": "schema_version: mozaiks.reactions.v1\nreactions: []",
    "modules/projects/contracts/notifications.yaml": "schema_version: mozaiks.notifications.v1\nnotifications: []",
    "modules/projects/contracts/settings.yaml": "schema_version: mozaiks.settings.v1\nsettings: []",
    "modules/projects/contracts/admin.yaml": "schema_version: mozaiks.admin.v1\npanels: []",
    # profile and runtime_extensions
    "modules/projects/contracts/profile.yaml": "schema_version: mozaiks.profile.v1\npanels: []",
    "modules/projects/contracts/relationships.yaml": "schema_version: mozaiks.relationships.v1\nproviders: []",
    "modules/projects/runtime_extensions.yaml": "api_router: backend.router:router",
    # backend
    "modules/projects/backend/handler.py": "class ProjectsModule: pass",
    "modules/projects/backend/service.py": "class ProjectsService: pass",
    "modules/projects/backend/repo.py": "class ProjectsRepo: pass",
    "modules/projects/backend/policy.py": "class ProjectsPolicy: pass",
    "modules/projects/backend/schemas.py": "class ProjectSchema: pass",
    # non-module files that must be ignored
    "app.json": '{"name": "myapp"}',
    "ui/pages/dashboard.yaml": "route: /dashboard",
    "config/shell.json": "{}",
}

# Minimal module: module.yaml + handler only
_MINIMAL_FILE_MAP: dict[str, str] = {
    "modules/landing/module.yaml": _LANDING_MODULE_YAML,
    "modules/landing/backend/handler.py": "class LandingModule: pass",
}

# Combined map with both modules + extra noise
_COMBINED_FILE_MAP: dict[str, str] = {**_FULL_FILE_MAP, **_MINIMAL_FILE_MAP}


# ---------------------------------------------------------------------------
# Full module (projects)
# ---------------------------------------------------------------------------

class TestFullModule:
    def setup_method(self):
        self.entries = extract_module_inventory(_FULL_FILE_MAP)

    def test_returns_one_entry(self):
        assert len(self.entries) == 1

    def test_module_id(self):
        assert self.entries[0].module_id == "projects"

    def test_has_persistence_true_due_to_repo(self):
        assert self.entries[0].has_persistence is True

    def test_action_ids_extracted(self):
        assert self.entries[0].action_ids == [
            "create_project",
            "list_projects",
            "update_project",
            "delete_project",
        ]

    def test_event_types_extracted(self):
        assert self.entries[0].event_types == [
            "domain.projects.project_created",
            "domain.projects.project_updated",
        ]

    def test_has_reactions(self):
        assert self.entries[0].has_reactions is True

    def test_has_notifications(self):
        assert self.entries[0].has_notifications is True

    def test_has_settings(self):
        assert self.entries[0].has_settings is True

    def test_has_admin(self):
        assert self.entries[0].has_admin is True

    def test_has_profile(self):
        assert self.entries[0].has_profile is True

    def test_has_relationships(self):
        assert self.entries[0].has_relationships is True

    def test_has_runtime_extensions(self):
        assert self.entries[0].has_runtime_extensions is True

    def test_has_handler(self):
        assert self.entries[0].has_handler is True

    def test_has_service(self):
        assert self.entries[0].has_service is True

    def test_has_repo(self):
        assert self.entries[0].has_repo is True

    def test_has_policy(self):
        assert self.entries[0].has_policy is True

    def test_backend_files_includes_all_py(self):
        assert "modules/projects/backend/handler.py" in self.entries[0].backend_files
        assert "modules/projects/backend/service.py" in self.entries[0].backend_files
        assert "modules/projects/backend/repo.py" in self.entries[0].backend_files
        assert "modules/projects/backend/policy.py" in self.entries[0].backend_files
        assert "modules/projects/backend/schemas.py" in self.entries[0].backend_files
        assert len(self.entries[0].backend_files) == 5

    def test_contract_files_includes_all_yaml(self):
        expected = {
            "modules/projects/contracts/admin.yaml",
            "modules/projects/contracts/events.yaml",
            "modules/projects/contracts/notifications.yaml",
            "modules/projects/contracts/profile.yaml",
            "modules/projects/contracts/relationships.yaml",
            "modules/projects/contracts/reactions.yaml",
            "modules/projects/contracts/settings.yaml",
        }
        assert set(self.entries[0].contract_files) == expected

    def test_non_module_files_not_in_backend_or_contract_lists(self):
        all_backend = self.entries[0].backend_files
        all_contracts = self.entries[0].contract_files
        for f in ["app.json", "ui/pages/dashboard.yaml", "config/shell.json"]:
            assert f not in all_backend
            assert f not in all_contracts


# ---------------------------------------------------------------------------
# Minimal module (landing)
# ---------------------------------------------------------------------------

class TestMinimalModule:
    def setup_method(self):
        self.entries = extract_module_inventory(_MINIMAL_FILE_MAP)

    def test_returns_one_entry(self):
        assert len(self.entries) == 1

    def test_module_id(self):
        assert self.entries[0].module_id == "landing"

    def test_has_persistence_false(self):
        assert self.entries[0].has_persistence is False

    def test_action_ids(self):
        assert self.entries[0].action_ids == ["get_landing_content"]

    def test_event_types_empty(self):
        assert self.entries[0].event_types == []

    def test_all_contract_flags_false(self):
        e = self.entries[0]
        assert e.has_reactions is False
        assert e.has_notifications is False
        assert e.has_settings is False
        assert e.has_admin is False
        assert e.has_profile is False
        assert e.has_runtime_extensions is False

    def test_has_handler_only(self):
        assert self.entries[0].has_handler is True
        assert self.entries[0].has_service is False
        assert self.entries[0].has_repo is False
        assert self.entries[0].has_policy is False

    def test_backend_files_only_handler(self):
        assert self.entries[0].backend_files == ["modules/landing/backend/handler.py"]

    def test_contract_files_empty(self):
        assert self.entries[0].contract_files == []


# ---------------------------------------------------------------------------
# Combined file map — ordering and isolation
# ---------------------------------------------------------------------------

class TestCombinedMap:
    def setup_method(self):
        self.entries = extract_module_inventory(_COMBINED_FILE_MAP)

    def test_returns_two_entries(self):
        assert len(self.entries) == 2

    def test_sorted_by_module_id(self):
        ids = [e.module_id for e in self.entries]
        assert ids == sorted(ids)

    def test_landing_is_first(self):
        assert self.entries[0].module_id == "landing"

    def test_projects_is_second(self):
        assert self.entries[1].module_id == "projects"

    def test_non_module_files_ignored(self):
        all_ids = {e.module_id for e in self.entries}
        assert "app.json" not in all_ids
        assert "ui" not in all_ids
        assert "config" not in all_ids


# ---------------------------------------------------------------------------
# Robustness: invalid YAML inputs
# ---------------------------------------------------------------------------

class TestInvalidYaml:
    def test_invalid_events_yaml_does_not_crash(self):
        file_map = {
            "modules/projects/module.yaml": _PROJECTS_MODULE_YAML,
            "modules/projects/contracts/events.yaml": ":::invalid:::yaml:::",
            "modules/projects/backend/handler.py": "",
        }
        entries = extract_module_inventory(file_map)
        assert len(entries) == 1
        assert entries[0].event_types == []

    def test_events_yaml_wrong_type_does_not_crash(self):
        file_map = {
            "modules/projects/module.yaml": _PROJECTS_MODULE_YAML,
            "modules/projects/contracts/events.yaml": "- just a list",
            "modules/projects/backend/handler.py": "",
        }
        entries = extract_module_inventory(file_map)
        assert len(entries) == 1
        assert entries[0].event_types == []

    def test_invalid_module_yaml_produces_minimal_entry(self):
        """An unparseable module.yaml produces a minimal entry rather than being skipped."""
        file_map = {
            "modules/broken/module.yaml": ":::not valid yaml:::",
        }
        entries = extract_module_inventory(file_map)
        assert len(entries) == 1
        assert entries[0].module_id == "broken"
        assert entries[0].action_ids == []
        assert entries[0].has_persistence is False

    def test_module_yaml_is_scalar_produces_minimal_entry(self):
        file_map = {
            "modules/broken/module.yaml": "just a string",
        }
        entries = extract_module_inventory(file_map)
        assert len(entries) == 1
        assert entries[0].module_id == "broken"
        assert entries[0].action_ids == []

    def test_empty_module_yaml_produces_minimal_entry(self):
        file_map = {
            "modules/empty/module.yaml": "",
        }
        entries = extract_module_inventory(file_map)
        assert len(entries) == 1
        assert entries[0].module_id == "empty"
        assert entries[0].action_ids == []


# ---------------------------------------------------------------------------
# Event type field name variants
# ---------------------------------------------------------------------------

class TestEventTypeVariants:
    def _make_file_map(self, events_yaml: str) -> dict[str, str]:
        return {
            "modules/mod/module.yaml": "schema_version: mozaiks.module.v1\nactions: []",
            "modules/mod/contracts/events.yaml": events_yaml,
        }

    def test_type_field(self):
        yaml = "events:\n  - type: domain.foo.bar\n"
        entries = extract_module_inventory(self._make_file_map(yaml))
        assert entries[0].event_types == ["domain.foo.bar"]

    def test_event_type_field(self):
        yaml = "events:\n  - event_type: domain.foo.bar\n"
        entries = extract_module_inventory(self._make_file_map(yaml))
        assert entries[0].event_types == ["domain.foo.bar"]

    def test_id_field_fallback(self):
        yaml = "events:\n  - id: domain.foo.bar\n"
        entries = extract_module_inventory(self._make_file_map(yaml))
        assert entries[0].event_types == ["domain.foo.bar"]

    def test_type_takes_priority_over_event_type(self):
        yaml = "events:\n  - type: domain.foo.type\n    event_type: domain.foo.event_type\n"
        entries = extract_module_inventory(self._make_file_map(yaml))
        assert entries[0].event_types == ["domain.foo.type"]

    def test_multiple_events(self):
        yaml = "events:\n  - type: domain.a.b\n  - type: domain.c.d\n"
        entries = extract_module_inventory(self._make_file_map(yaml))
        assert entries[0].event_types == ["domain.a.b", "domain.c.d"]


# ---------------------------------------------------------------------------
# Persistence detection
# ---------------------------------------------------------------------------

class TestPersistenceDetection:
    def test_repo_py_triggers_persistence(self):
        file_map = {
            "modules/foo/module.yaml": "schema_version: mozaiks.module.v1\nactions: []",
            "modules/foo/backend/handler.py": "",
            "modules/foo/backend/repo.py": "",
        }
        entries = extract_module_inventory(file_map)
        assert entries[0].has_persistence is True
        assert entries[0].has_repo is True

    def test_emits_on_action_triggers_persistence_without_repo(self):
        module_yaml = """\
schema_version: mozaiks.module.v1
actions:
  - id: create_thing
    handler_method: create_thing
    emits:
      - domain.foo.created
"""
        file_map = {
            "modules/foo/module.yaml": module_yaml,
            "modules/foo/backend/handler.py": "",
        }
        entries = extract_module_inventory(file_map)
        assert entries[0].has_persistence is True
        assert entries[0].has_repo is False  # only yaml signal, not repo.py

    def test_no_persistence_when_no_repo_and_no_emits(self):
        file_map = {
            "modules/foo/module.yaml": "schema_version: mozaiks.module.v1\nactions:\n  - id: read_thing\n    handler_method: read_thing\n",
            "modules/foo/backend/handler.py": "",
        }
        entries = extract_module_inventory(file_map)
        assert entries[0].has_persistence is False


# ---------------------------------------------------------------------------
# runtime_extensions.yaml detection
# ---------------------------------------------------------------------------

def test_runtime_extensions_detected():
    file_map = {
        "modules/hook/module.yaml": "schema_version: mozaiks.module.v1\nactions: []",
        "modules/hook/runtime_extensions.yaml": "api_router: backend.router:router",
        "modules/hook/backend/handler.py": "",
    }
    entries = extract_module_inventory(file_map)
    assert entries[0].has_runtime_extensions is True


def test_runtime_extensions_not_present():
    file_map = {
        "modules/hook/module.yaml": "schema_version: mozaiks.module.v1\nactions: []",
        "modules/hook/backend/handler.py": "",
    }
    entries = extract_module_inventory(file_map)
    assert entries[0].has_runtime_extensions is False


# ---------------------------------------------------------------------------
# profile.yaml detection
# ---------------------------------------------------------------------------

def test_profile_contract_detected():
    file_map = {
        "modules/wallet/module.yaml": "schema_version: mozaiks.module.v1\nactions: []",
        "modules/wallet/contracts/profile.yaml": "schema_version: mozaiks.profile.v1\npanels: []",
        "modules/wallet/backend/handler.py": "",
    }
    entries = extract_module_inventory(file_map)
    assert entries[0].has_profile is True


# ---------------------------------------------------------------------------
# relationships.yaml detection
# ---------------------------------------------------------------------------

def test_relationships_contract_detected():
    file_map = {
        "modules/projects/module.yaml": "schema_version: mozaiks.module.v1\nactions: []",
        "modules/projects/contracts/relationships.yaml": "schema_version: mozaiks.relationships.v1\nproviders: []",
        "modules/projects/backend/handler.py": "",
    }
    entries = extract_module_inventory(file_map)
    assert entries[0].has_relationships is True


# ---------------------------------------------------------------------------
# file_map immutability
# ---------------------------------------------------------------------------

def test_file_map_is_not_mutated():
    original = {
        "modules/foo/module.yaml": "schema_version: mozaiks.module.v1\nactions: []",
        "modules/foo/backend/handler.py": "",
    }
    snapshot = copy.deepcopy(original)
    extract_module_inventory(original)
    assert original == snapshot


# ---------------------------------------------------------------------------
# Empty / edge cases
# ---------------------------------------------------------------------------

def test_empty_file_map_returns_empty_list():
    assert extract_module_inventory({}) == []


def test_no_modules_directory_returns_empty_list():
    file_map = {
        "app.json": "{}",
        "ui/pages/home.yaml": "route: /",
        "config/shell.json": "{}",
    }
    assert extract_module_inventory(file_map) == []


def test_module_yaml_not_at_root_level_is_ignored():
    """Files like modules/foo/backend/module.yaml must not register a module."""
    file_map = {
        "modules/foo/backend/module.yaml": "schema_version: mozaiks.module.v1\nactions: []",
    }
    assert extract_module_inventory(file_map) == []


def test_non_yaml_backend_files_not_included_in_backend_files():
    """Only .py files appear in backend_files."""
    file_map = {
        "modules/foo/module.yaml": "schema_version: mozaiks.module.v1\nactions: []",
        "modules/foo/backend/handler.py": "",
        "modules/foo/backend/notes.txt": "notes",
        "modules/foo/backend/__pycache__/handler.cpython-313.pyc": "",
    }
    entries = extract_module_inventory(file_map)
    assert entries[0].backend_files == ["modules/foo/backend/handler.py"]


def test_result_is_sorted_by_module_id():
    file_map = {
        "modules/zebra/module.yaml": "schema_version: mozaiks.module.v1\nactions: []",
        "modules/apple/module.yaml": "schema_version: mozaiks.module.v1\nactions: []",
        "modules/mango/module.yaml": "schema_version: mozaiks.module.v1\nactions: []",
        "modules/zebra/backend/handler.py": "",
        "modules/apple/backend/handler.py": "",
        "modules/mango/backend/handler.py": "",
    }
    entries = extract_module_inventory(file_map)
    assert [e.module_id for e in entries] == ["apple", "mango", "zebra"]


def test_module_id_comes_from_path_not_yaml():
    """Even if module.yaml declares a different id, module_id is taken from the path."""
    module_yaml = """\
schema_version: mozaiks.module.v1
module:
  id: different_name
  display_name: Different
actions: []
"""
    file_map = {
        "modules/path_name/module.yaml": module_yaml,
        "modules/path_name/backend/handler.py": "",
    }
    entries = extract_module_inventory(file_map)
    assert entries[0].module_id == "path_name"


# ---------------------------------------------------------------------------
# Real first-party module shapes (from factory_app/app/modules/app_registry)
# ---------------------------------------------------------------------------

_APP_REGISTRY_MODULE_YAML = """\
schema_version: mozaiks.module.v1
module:
  id: app_registry
  display_name: App Registry
  version: 1.0.0
  description: Durable app records and lifecycle state for Mozaiks Studio.
  owner: app
  visibility: private
  handler: backend.handler:AppRegistryModule
actions:
  - id: create_app_record
    description: Create or reopen an app lifecycle record.
    handler_method: create_app_record
    emits:
      - domain.app_registry.app_created
  - id: update_build_status
    description: Update the lifecycle state for an existing app record.
    handler_method: update_build_status
    emits:
      - domain.app_registry.status_changed
  - id: list_apps
    description: List app records for the current user scope.
    handler_method: list_apps
  - id: get_app_record
    description: Fetch one app lifecycle record by app id or build registry id.
    handler_method: get_app_record
"""

_APP_REGISTRY_EVENTS_YAML = """\
schema_version: mozaiks.events.v1
events:
  - type: domain.app_registry.app_created
    version: 1
    description: Emitted when an app lifecycle record is created.
    producer: app_registry
  - type: domain.app_registry.status_changed
    version: 1
    description: Emitted when an app lifecycle record changes state.
    producer: app_registry
"""


def test_app_registry_module_shape():
    """Verify extraction against the real app_registry module shape."""
    file_map = {
        "modules/app_registry/module.yaml": _APP_REGISTRY_MODULE_YAML,
        "modules/app_registry/contracts/events.yaml": _APP_REGISTRY_EVENTS_YAML,
        "modules/app_registry/backend/handler.py": "",
        "modules/app_registry/backend/service.py": "",
        "modules/app_registry/backend/repo.py": "",
    }
    entries = extract_module_inventory(file_map)
    assert len(entries) == 1
    e = entries[0]
    assert e.module_id == "app_registry"
    assert e.action_ids == ["create_app_record", "update_build_status", "list_apps", "get_app_record"]
    assert e.event_types == ["domain.app_registry.app_created", "domain.app_registry.status_changed"]
    assert e.has_persistence is True  # both repo.py and emits signal
    assert e.has_repo is True
    assert e.has_service is True


# ---------------------------------------------------------------------------
# Phase 5: carry_forward classification tests
# ---------------------------------------------------------------------------

def _make_entry(**kwargs) -> ModuleInventoryEntry:
    """Build a ModuleInventoryEntry with explicit carry_forward defaults."""
    defaults = dict(
        module_id="custom_module",
        action_ids=[],
        has_persistence=False,
        has_handler=False,
        has_service=False,
        has_repo=False,
        has_policy=False,
        event_types=[],
        has_reactions=False,
        has_notifications=False,
        has_settings=False,
        has_admin=False,
        has_profile=False,
        has_relationships=False,
        has_runtime_extensions=False,
        backend_files=[],
        contract_files=[],
        carry_forward_classification="needs_adaptation",
        carry_forward_reasons=[],
    )
    defaults.update(kwargs)
    return ModuleInventoryEntry(**defaults)


class TestClassifyKnownSafeModules:
    def test_settings_module_is_safe(self) -> None:
        entry = _make_entry(module_id="settings")
        cls, reasons = classify_module_carry_forward(entry)
        assert cls == "safe_carry_forward"
        assert reasons

    def test_notifications_module_is_safe(self) -> None:
        entry = _make_entry(module_id="notifications")
        cls, reasons = classify_module_carry_forward(entry)
        assert cls == "safe_carry_forward"
        assert reasons

    def test_audit_module_is_safe(self) -> None:
        entry = _make_entry(module_id="audit")
        cls, reasons = classify_module_carry_forward(entry)
        assert cls == "safe_carry_forward"
        assert reasons

    def test_activity_module_is_safe(self) -> None:
        entry = _make_entry(module_id="activity")
        cls, reasons = classify_module_carry_forward(entry)
        assert cls == "safe_carry_forward"
        assert reasons

    def test_files_module_is_safe(self) -> None:
        entry = _make_entry(module_id="files")
        cls, reasons = classify_module_carry_forward(entry)
        assert cls == "safe_carry_forward"
        assert reasons

    def test_auth_module_is_safe(self) -> None:
        entry = _make_entry(module_id="auth")
        cls, reasons = classify_module_carry_forward(entry)
        assert cls == "safe_carry_forward"
        assert reasons

    def test_users_module_is_safe(self) -> None:
        entry = _make_entry(module_id="users")
        cls, reasons = classify_module_carry_forward(entry)
        assert cls == "safe_carry_forward"
        assert reasons

    def test_profile_module_is_safe(self) -> None:
        entry = _make_entry(module_id="profile")
        cls, reasons = classify_module_carry_forward(entry)
        assert cls == "safe_carry_forward"
        assert reasons

    def test_integrations_module_is_safe(self) -> None:
        entry = _make_entry(module_id="integrations")
        cls, reasons = classify_module_carry_forward(entry)
        assert cls == "safe_carry_forward"
        assert reasons

    def test_billing_portal_is_safe(self) -> None:
        """billing_portal is classified safe_carry_forward — it is in the known safe list.

        Rationale: billing portal (subscription/payment UI) is generic across
        most app concepts that still involve billing. It may carry persistence
        but the safe-list match takes priority over persistence signals.
        """
        entry = _make_entry(
            module_id="billing_portal",
            has_persistence=True,
            has_repo=True,
            action_ids=["get_subscription", "update_billing_info"],
        )
        cls, reasons = classify_module_carry_forward(entry)
        assert cls == "safe_carry_forward"
        assert any("billing_portal" in r for r in reasons)

    def test_safe_module_with_persistence_still_safe(self) -> None:
        """Known-safe modules remain safe_carry_forward even when has_persistence=True."""
        entry = _make_entry(module_id="notifications", has_persistence=True, has_repo=True)
        cls, reasons = classify_module_carry_forward(entry)
        assert cls == "safe_carry_forward"
        # But persistence is noted in reasons as a review hint
        assert any("has_persistence" in r for r in reasons)

    def test_safe_reasons_include_module_id_match(self) -> None:
        entry = _make_entry(module_id="settings", has_settings=True)
        cls, reasons = classify_module_carry_forward(entry)
        assert cls == "safe_carry_forward"
        assert any("settings" in r for r in reasons)


class TestClassifyDomainSpecificModules:
    def test_projects_module_is_regenerate(self) -> None:
        entry = _make_entry(
            module_id="projects",
            action_ids=["create_project", "list_projects", "update_project", "delete_project"],
            has_persistence=True,
            has_repo=True,
        )
        cls, reasons = classify_module_carry_forward(entry)
        assert cls == "regenerate"
        assert reasons

    def test_tasks_module_is_regenerate(self) -> None:
        entry = _make_entry(module_id="tasks")
        cls, reasons = classify_module_carry_forward(entry)
        assert cls == "regenerate"
        assert any("task" in r for r in reasons)

    def test_orders_module_is_regenerate(self) -> None:
        entry = _make_entry(module_id="orders")
        cls, reasons = classify_module_carry_forward(entry)
        assert cls == "regenerate"

    def test_listings_module_is_regenerate(self) -> None:
        entry = _make_entry(module_id="listings")
        cls, reasons = classify_module_carry_forward(entry)
        assert cls == "regenerate"

    def test_crm_pipeline_module_is_regenerate(self) -> None:
        entry = _make_entry(module_id="crm_pipeline")
        cls, reasons = classify_module_carry_forward(entry)
        assert cls == "regenerate"
        # Should catch both 'crm' and 'pipeline' fragments
        matched = [r for r in reasons if "crm" in r or "pipeline" in r]
        assert matched

    def test_leads_module_is_regenerate(self) -> None:
        entry = _make_entry(module_id="leads")
        cls, reasons = classify_module_carry_forward(entry)
        assert cls == "regenerate"

    def test_invoices_module_is_regenerate(self) -> None:
        entry = _make_entry(module_id="invoices")
        cls, reasons = classify_module_carry_forward(entry)
        assert cls == "regenerate"

    def test_campaigns_module_is_regenerate(self) -> None:
        entry = _make_entry(module_id="campaigns")
        cls, reasons = classify_module_carry_forward(entry)
        assert cls == "regenerate"

    def test_booking_system_module_is_regenerate(self) -> None:
        entry = _make_entry(module_id="booking_system")
        cls, reasons = classify_module_carry_forward(entry)
        assert cls == "regenerate"

    def test_regenerate_reasons_are_nonempty(self) -> None:
        entry = _make_entry(module_id="products", has_persistence=True)
        cls, reasons = classify_module_carry_forward(entry)
        assert cls == "regenerate"
        assert len(reasons) >= 1


class TestClassifyNeedsAdaptation:
    def test_unknown_module_with_persistence_is_needs_adaptation(self) -> None:
        """A custom module with persistence but unknown ID → needs_adaptation, not safe."""
        entry = _make_entry(
            module_id="custom_dashboard",
            has_persistence=True,
            has_repo=True,
            action_ids=["get_stats", "refresh_data"],
        )
        cls, reasons = classify_module_carry_forward(entry)
        assert cls == "needs_adaptation"
        assert any("has_persistence" in r for r in reasons)

    def test_unknown_module_with_admin_is_needs_adaptation(self) -> None:
        entry = _make_entry(module_id="custom_widget", has_admin=True)
        cls, reasons = classify_module_carry_forward(entry)
        assert cls == "needs_adaptation"
        assert any("has_admin" in r for r in reasons)

    def test_unknown_module_with_reactions_is_needs_adaptation(self) -> None:
        entry = _make_entry(module_id="event_hub", has_reactions=True)
        cls, reasons = classify_module_carry_forward(entry)
        assert cls == "needs_adaptation"
        assert any("has_reactions" in r for r in reasons)

    def test_unknown_module_with_runtime_extensions_is_needs_adaptation(self) -> None:
        entry = _make_entry(module_id="api_gateway", has_runtime_extensions=True)
        cls, reasons = classify_module_carry_forward(entry)
        assert cls == "needs_adaptation"
        assert any("has_runtime_extensions" in r for r in reasons)

    def test_unknown_module_with_events_is_needs_adaptation(self) -> None:
        entry = _make_entry(
            module_id="activity_feed",
            event_types=["domain.data.record_created"],
        )
        cls, reasons = classify_module_carry_forward(entry)
        assert cls == "needs_adaptation"
        assert any("event" in r.lower() for r in reasons)

    def test_unknown_module_no_signals_is_needs_adaptation_not_safe(self) -> None:
        """Unknown module with no signals defaults to needs_adaptation, never safe."""
        entry = _make_entry(module_id="mystery_module")
        cls, reasons = classify_module_carry_forward(entry)
        assert cls == "needs_adaptation"
        assert reasons  # conservative fallback reason is included

    def test_crud_heavy_unknown_module_is_needs_adaptation(self) -> None:
        """CRUD-heavy actions alone (without domain fragment) → needs_adaptation."""
        entry = _make_entry(
            module_id="resource_tracker",
            action_ids=["create_resource", "list_resources", "update_resource", "delete_resource"],
            has_persistence=True,
        )
        cls, reasons = classify_module_carry_forward(entry)
        assert cls == "needs_adaptation"

    def test_needs_adaptation_reasons_are_nonempty(self) -> None:
        entry = _make_entry(module_id="custom_reports", has_admin=True, has_persistence=True)
        cls, reasons = classify_module_carry_forward(entry)
        assert cls == "needs_adaptation"
        assert len(reasons) >= 1


class TestClassifyInfrastructureSignals:
    def test_unknown_settings_only_module_is_safe(self) -> None:
        """Module not in safe list but has_settings=True with no persistence/admin → safe."""
        entry = _make_entry(
            module_id="theme_settings",
            has_settings=True,
            has_persistence=False,
            has_admin=False,
            has_reactions=False,
            event_types=[],
        )
        cls, _ = classify_module_carry_forward(entry)
        assert cls == "safe_carry_forward"

    def test_unknown_notifications_only_module_is_safe(self) -> None:
        entry = _make_entry(
            module_id="alert_notifications",
            has_notifications=True,
            has_persistence=False,
            has_admin=False,
            has_reactions=False,
            event_types=[],
        )
        cls, _ = classify_module_carry_forward(entry)
        assert cls == "safe_carry_forward"

    def test_unknown_profile_only_module_is_safe(self) -> None:
        entry = _make_entry(
            module_id="user_profile_display",
            has_profile=True,
            has_persistence=False,
            has_admin=False,
            has_reactions=False,
            event_types=[],
        )
        cls, _ = classify_module_carry_forward(entry)
        assert cls == "safe_carry_forward"

    def test_unknown_relationships_only_module_is_safe(self) -> None:
        entry = _make_entry(
            module_id="resource_relationships",
            has_relationships=True,
            has_persistence=False,
            has_admin=False,
            has_reactions=False,
            event_types=[],
        )
        cls, _ = classify_module_carry_forward(entry)
        assert cls == "safe_carry_forward"

    def test_settings_module_with_admin_is_not_safe(self) -> None:
        """Infrastructure signals + admin → needs_adaptation (admin is concept-adjacent)."""
        entry = _make_entry(
            module_id="theme_settings",
            has_settings=True,
            has_admin=True,
            has_persistence=False,
        )
        cls, _ = classify_module_carry_forward(entry)
        assert cls == "needs_adaptation"

    def test_notifications_with_persistence_not_safe_via_infra_path(self) -> None:
        """has_notifications + has_persistence → needs_adaptation via infra-signal path."""
        entry = _make_entry(
            module_id="alert_notifications",
            has_notifications=True,
            has_persistence=True,
        )
        # Not in known safe list; infra path requires no persistence
        # BUT module_id is not in safe list either
        # → should be needs_adaptation
        cls, _ = classify_module_carry_forward(entry)
        # NOTE: "notifications" IS in _SAFE_MODULE_IDS, so this actually hits
        # the safe list path regardless of persistence.
        # Use an unrecognized module name to test the infra path with persistence.
        entry2 = _make_entry(
            module_id="custom_alert_module",
            has_notifications=True,
            has_persistence=True,
        )
        cls2, _ = classify_module_carry_forward(entry2)
        assert cls2 == "needs_adaptation"


class TestClassificationInExtractedEntries:
    def test_extracted_settings_module_has_classification(self) -> None:
        file_map = {
            "modules/settings/module.yaml": "id: settings\nactions: []\n",
            "modules/settings/contracts/settings.yaml": "schema_version: v1\n",
        }
        entries = extract_module_inventory(file_map)
        assert len(entries) == 1
        e = entries[0]
        assert e.carry_forward_classification == "safe_carry_forward"
        assert e.carry_forward_reasons

    def test_extracted_projects_module_has_classification(self) -> None:
        file_map = {
            "modules/projects/module.yaml": _PROJECTS_MODULE_YAML,
            "modules/projects/backend/repo.py": "",
        }
        entries = extract_module_inventory(file_map)
        assert len(entries) == 1
        e = entries[0]
        assert e.carry_forward_classification == "regenerate"
        assert e.carry_forward_reasons

    def test_extracted_unknown_module_has_classification(self) -> None:
        """Any extracted module has non-empty carry_forward_reasons."""
        file_map = {
            "modules/custom_tracker/module.yaml": "id: custom_tracker\nactions: []\n",
        }
        entries = extract_module_inventory(file_map)
        assert len(entries) == 1
        e = entries[0]
        assert e.carry_forward_classification in {
            "safe_carry_forward", "needs_adaptation", "regenerate"
        }
        assert e.carry_forward_reasons

    def test_model_dump_includes_classification_fields(self) -> None:
        """model_dump() includes carry_forward_classification and carry_forward_reasons."""
        file_map = {
            "modules/notifications/module.yaml": "id: notifications\nactions: []\n",
        }
        entries = extract_module_inventory(file_map)
        dumped = entries[0].model_dump()
        assert "carry_forward_classification" in dumped
        assert "carry_forward_reasons" in dumped
        assert isinstance(dumped["carry_forward_reasons"], list)
        assert isinstance(dumped["carry_forward_classification"], str)

