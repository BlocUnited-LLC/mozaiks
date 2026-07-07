from __future__ import annotations

from pathlib import Path

import pytest


def test_relationship_provider_discovery_returns_empty_for_no_modules(tmp_path: Path) -> None:
    from mozaiksai.core.relationships.discovery import load_relationship_providers

    assert load_relationship_providers(tmp_path) == []
    (tmp_path / "modules").mkdir()
    assert load_relationship_providers(tmp_path) == []


def test_relationship_provider_discovery_loads_valid_provider(tmp_path: Path) -> None:
    from mozaiksai.core.relationships.discovery import load_relationship_providers

    module_dir = tmp_path / "modules" / "projects"
    (module_dir / "contracts").mkdir(parents=True)
    (module_dir / "contracts" / "relationships.yaml").write_text(
        """
schema_version: mozaiks.relationships.v1
providers:
  - id: owned-projects
    label: Owned Projects
    description: Projects owned by the current user.
    order: 20
    action: list_user_project_relationships
    resource_types: [project]
    relationship_types: [owner, maintainer]
""".lstrip(),
        encoding="utf-8",
    )

    providers = load_relationship_providers(tmp_path)

    assert len(providers) == 1
    assert providers[0]["id"] == "owned-projects"
    assert providers[0]["module_id"] == "projects"
    assert providers[0]["action"] == "list_user_project_relationships"
    assert providers[0]["resource_types"] == ["project"]


def test_relationship_provider_discovery_sorts_by_order(tmp_path: Path) -> None:
    from mozaiksai.core.relationships.discovery import load_relationship_providers

    for module_id, order in [("zeta", 50), ("alpha", 20)]:
        module_dir = tmp_path / "modules" / module_id
        (module_dir / "contracts").mkdir(parents=True)
        (module_dir / "contracts" / "relationships.yaml").write_text(
            f"""
schema_version: mozaiks.relationships.v1
providers:
  - id: {module_id}-relationships
    label: {module_id.title()} Relationships
    order: {order}
    action: list_relationships
    resource_types: [thing]
""".lstrip(),
            encoding="utf-8",
        )

    providers = load_relationship_providers(tmp_path)

    assert [provider["module_id"] for provider in providers] == ["alpha", "zeta"]


def test_relationship_manifest_rejects_duplicate_provider_ids() -> None:
    from pydantic import ValidationError

    from mozaiksai.core.runtime.app.module_loader import ModuleRelationshipsManifest

    with pytest.raises(ValidationError, match="unique id"):
        ModuleRelationshipsManifest.model_validate(
            {
                "schema_version": "mozaiks.relationships.v1",
                "providers": [
                    {"id": "dup", "label": "One", "action": "list_one", "resource_types": ["app"]},
                    {"id": "dup", "label": "Two", "action": "list_two", "resource_types": ["app"]},
                ],
            }
        )


def test_relationship_manifest_requires_resource_types() -> None:
    from pydantic import ValidationError

    from mozaiksai.core.runtime.app.module_loader import ModuleRelationshipsManifest

    with pytest.raises(ValidationError, match="resource_type"):
        ModuleRelationshipsManifest.model_validate(
            {
                "schema_version": "mozaiks.relationships.v1",
                "providers": [
                    {"id": "owned", "label": "Owned", "action": "list_owned", "resource_types": []},
                ],
            }
        )


def test_relationship_manifest_defaults_null_order_to_100() -> None:
    from mozaiksai.core.runtime.app.module_loader import ModuleRelationshipsManifest

    manifest = ModuleRelationshipsManifest.model_validate(
        {
            "schema_version": "mozaiks.relationships.v1",
            "providers": [
                {
                    "id": "owned",
                    "label": "Owned",
                    "action": "list_owned",
                    "order": None,
                    "resource_types": ["app"],
                },
            ],
        }
    )

    assert manifest.providers[0].order == 100


def test_module_loader_exposes_relationships_manifest(tmp_path: Path) -> None:
    from mozaiksai.core.runtime.app.module_loader import ModuleLoader
    from tests.test_module_loader_contracts import _write_canonical_module

    module_dir = _write_canonical_module(tmp_path)
    module_dir.joinpath("contracts", "relationships.yaml").write_text(
        """
schema_version: mozaiks.relationships.v1
providers:
  - id: owned-tasks
    label: Owned Tasks
    action: create
    resource_types: [task]
    relationship_types: [owner]
""".lstrip(),
        encoding="utf-8",
    )

    loaded = ModuleLoader(str(tmp_path)).load("tasks")

    assert loaded.manifests.relationships is not None
    assert loaded.manifests.relationships.providers[0].id == "owned-tasks"


@pytest.mark.asyncio
async def test_relationship_endpoint_hydrates_provider_rows(monkeypatch, tmp_path: Path) -> None:
    from mozaiksai.core.auth.dependencies import UserPrincipal
    from mozaiksai.core.runtime.composition.module_executor import ModuleResult
    from mozaiksai.hosts import platform as platform_app

    module_dir = tmp_path / "modules" / "projects"
    (module_dir / "contracts").mkdir(parents=True)
    (tmp_path / "app.json").write_text('{"appName": "Test"}', encoding="utf-8")
    (module_dir / "contracts" / "relationships.yaml").write_text(
        """
schema_version: mozaiks.relationships.v1
providers:
  - id: owned-projects
    label: Owned Projects
    action: list_user_project_relationships
    resource_types: [project]
    relationship_types: [owner]
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("PLATFORM_PATH", str(tmp_path))

    class _FakeExecutor:
        async def execute(self, req, context):
            assert req.module == "projects"
            assert req.action == "list_user_project_relationships"
            return ModuleResult(
                success=True,
                data={
                    "relationships": [
                        {
                            "resource_type": "project",
                            "resource_id": "project_1",
                            "resource_label": "Project One",
                            "relationship_type": "owner",
                            "primary_route": "/projects/project_1",
                            "capabilities": ["project.view", "project.admin"],
                        }
                    ]
                },
            )

    class _FakeRegistry:
        @property
        def module_executor(self):
            return _FakeExecutor()

    monkeypatch.setattr(platform_app, "executor_registry", _FakeRegistry())

    principal = UserPrincipal(
        user_id="u1",
        email="u@example.com",
        name="User",
        roles=[],
        scopes=[],
        raw_claims={},
    )

    result = await platform_app.get_current_user_relationships(app_id=None, principal=principal)

    assert result["providers"][0]["count"] == 1
    assert result["providers"][0]["error"] is None
    assert result["relationships"] == [
        {
            "relationship_id": "projects:owned-projects:project:project_1:owner",
            "resource_type": "project",
            "resource_id": "project_1",
            "resource_label": "Project One",
            "relationship_type": "owner",
            "status": "active",
            "capabilities": ["project.view", "project.admin"],
            "primary_route": "/projects/project_1",
            "secondary_routes": [],
            "source_module": "projects",
            "source_provider": "owned-projects",
            "updated_at": None,
            "metadata": {},
        }
    ]


@pytest.mark.asyncio
async def test_relationship_endpoint_filters_resource_and_relationship_types(monkeypatch, tmp_path: Path) -> None:
    from mozaiksai.core.auth.dependencies import UserPrincipal
    from mozaiksai.core.runtime.composition.module_executor import ModuleResult
    from mozaiksai.hosts import platform as platform_app

    module_dir = tmp_path / "modules" / "projects"
    (module_dir / "contracts").mkdir(parents=True)
    (tmp_path / "app.json").write_text('{"appName": "Test"}', encoding="utf-8")
    (module_dir / "contracts" / "relationships.yaml").write_text(
        """
schema_version: mozaiks.relationships.v1
providers:
  - id: owned-projects
    label: Owned Projects
    action: list_user_project_relationships
    resource_types: [project]
    relationship_types: [owner]
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("PLATFORM_PATH", str(tmp_path))

    class _FakeExecutor:
        async def execute(self, req, context):
            return ModuleResult(
                success=True,
                data={
                    "relationships": [
                        {
                            "resource_type": "project",
                            "resource_id": "project_1",
                            "relationship_type": "owner",
                        },
                        {
                            "resource_type": "project",
                            "resource_id": "project_2",
                            "relationship_type": "viewer",
                        },
                        {
                            "resource_type": "team",
                            "resource_id": "team_1",
                            "relationship_type": "owner",
                        },
                    ]
                },
            )

    class _FakeRegistry:
        @property
        def module_executor(self):
            return _FakeExecutor()

    monkeypatch.setattr(platform_app, "executor_registry", _FakeRegistry())

    principal = UserPrincipal(
        user_id="u1",
        email="u@example.com",
        name="User",
        roles=[],
        scopes=[],
        raw_claims={},
    )

    result = await platform_app.get_current_user_relationships(app_id=None, principal=principal)

    assert [row["resource_id"] for row in result["relationships"]] == ["project_1"]


@pytest.mark.asyncio
async def test_relationship_endpoint_isolates_provider_failure(monkeypatch, tmp_path: Path) -> None:
    from mozaiksai.core.auth.dependencies import UserPrincipal
    from mozaiksai.core.runtime.composition.module_executor import ModuleResult
    from mozaiksai.hosts import platform as platform_app

    module_dir = tmp_path / "modules" / "projects"
    (module_dir / "contracts").mkdir(parents=True)
    (tmp_path / "app.json").write_text('{"appName": "Test"}', encoding="utf-8")
    (module_dir / "contracts" / "relationships.yaml").write_text(
        """
schema_version: mozaiks.relationships.v1
providers:
  - id: broken-provider
    label: Broken
    action: list_broken
    resource_types: [project]
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("PLATFORM_PATH", str(tmp_path))

    class _FailingExecutor:
        async def execute(self, req, context):
            return ModuleResult(success=False, error="service unavailable")

    class _FakeRegistry:
        @property
        def module_executor(self):
            return _FailingExecutor()

    monkeypatch.setattr(platform_app, "executor_registry", _FakeRegistry())

    principal = UserPrincipal(
        user_id="u1",
        email="u@example.com",
        name="User",
        roles=[],
        scopes=[],
        raw_claims={},
    )

    result = await platform_app.get_current_user_relationships(app_id=None, principal=principal)

    assert result["relationships"] == []
    assert result["providers"][0]["count"] == 0
    assert "service unavailable" in result["providers"][0]["error"]
