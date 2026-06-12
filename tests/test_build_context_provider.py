from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from mozaiksai.core.session.build_context import (
    BuildContextError,
    load_build_context,
    merge_build_context,
)
from mozaiksai.core.session.launcher import apply_launch_context_provider


def _write_yaml(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def _build_context_root(tmp_path: Path) -> Path:
    root = tmp_path / "build_context"
    context_root = root / "AcmeEnterprise"
    _write_yaml(
        context_root / "context.yaml",
            {
                "context_id": "acme_operator",
                "applies_to_workflows": ["AppGenerator"],
                "assets": [
                    {"path": "contract.yaml", "kind": "contract"},
                ],
                "operator_capabilities": ["enterprise_sso", "audit_export"],
            "capability_registry": {
                "sso": {
                    "capability_pack_id": "enterprise_sso",
                    "implementation_mode": "external_integration",
                }
            },
            "pack": {
                "id": "enterprise_sso",
                "display_name": "Enterprise SSO",
                "description": "SAML/OIDC login and group sync.",
                "status": "active",
            },
            "capabilities": [{"capability_id": "sso.login"}],
                "projections": {
                "context_variables": {
                    "operator_capabilities": {"from": "operator_capabilities"},
                    "capability_packs": {"from": "capability_packs"},
                    "operator_contracts": {"from": "operator_contracts"},
                    "capability_registry": {"from": "capability_registry"},
                    "provider_backed_capabilities": {
                        "from_trigger": "builder_options.provider_backed_capabilities",
                        "default": [],
                    },
                },
            },
        },
    )
    _write_yaml(
        context_root / "contract.yaml",
        {
            "contract_id": "enterprise_sso",
            "instructions": ["Generate only app-owned SSO facade code."],
        },
    )
    return root


def test_build_context_projects_only_declared_workflow_values(tmp_path: Path) -> None:
    root = _build_context_root(tmp_path)

    context = merge_build_context(
        build_context_root=root,
        workflow_id="AppGenerator",
        context_variables={"screen": "studio-create"},
        trigger_payload={
            "builder_options": {
                "provider_backed_capabilities": [{"capability_id": "sso.login"}]
            }
        },
    )

    assert context["screen"] == "studio-create"
    assert context["operator_capabilities"] == ["enterprise_sso", "audit_export"]
    assert context["capability_registry"]["sso"]["capability_pack_id"] == "enterprise_sso"
    assert context["capability_packs"][0]["id"] == "enterprise_sso"
    assert context["capability_packs"][0]["capability_source"] == "operator_pack"
    assert context["capability_packs"][0]["pack_source_path"] == str((root / "AcmeEnterprise").resolve())
    assert "context_id" not in context["capability_packs"][0]
    assert "assets" not in context["capability_packs"][0]
    assert context["operator_contracts"][0]["contract_id"] == "enterprise_sso"
    assert context["provider_backed_capabilities"] == [{"capability_id": "sso.login"}]
    assert "packs" not in context


def test_build_context_does_not_project_to_unmapped_workflow(tmp_path: Path) -> None:
    root = _build_context_root(tmp_path)

    context = merge_build_context(
        build_context_root=root,
        workflow_id="AgentGenerator",
        context_variables={},
    )

    assert context == {}


def test_existing_context_variables_take_precedence_over_build_context(tmp_path: Path) -> None:
    root = _build_context_root(tmp_path)

    context = merge_build_context(
        build_context_root=root,
        workflow_id="AppGenerator",
        context_variables={"operator_capabilities": ["already_set"]},
    )

    assert context["operator_capabilities"] == ["already_set"]


def test_merge_build_context_noop_when_no_build_context_file(tmp_path: Path) -> None:
    context = merge_build_context(
        build_context_root=tmp_path / "missing_build_context",
        workflow_id="AppGenerator",
        context_variables={"screen": "studio-create"},
    )
    assert context == {"screen": "studio-create"}


def test_merge_build_context_noop_when_no_context_path_provided(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MOZAIKS_BUILD_CONTEXT_PATH", raising=False)
    monkeypatch.delenv("MOZAIKS_APP_WORKSPACE_PATH", raising=False)

    context = merge_build_context(
        workflow_id="AppGenerator",
        context_variables={"screen": "studio-create"},
    )
    assert context == {"screen": "studio-create"}


def test_load_build_context_rejects_missing_context_id(tmp_path: Path) -> None:
    context_file = tmp_path / "build_context" / "AcmeEnterprise" / "context.yaml"
    context_file.parent.mkdir(parents=True)
    context_file.write_text("operator_capabilities: [x]\n", encoding="utf-8")

    with pytest.raises(BuildContextError, match="context_id"):
        load_build_context(context_file)


def test_load_build_context_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(BuildContextError, match="not found"):
        load_build_context(tmp_path / "build_context" / "AcmeEnterprise" / "context.yaml")


def test_inactive_pack_is_not_projected(tmp_path: Path) -> None:
    root = tmp_path / "build_context"
    context_root = root / "Payments"
    _write_yaml(
        context_root / "context.yaml",
        {
            "context_id": "operator",
            "applies_to_workflows": ["AppGenerator"],
            "assets": [],
            "pack": {"id": "mozaikspay", "status": "inactive"},
            "projections": {
                "context_variables": {"capability_packs": {"from": "capability_packs"}},
            },
        },
    )

    context = merge_build_context(build_context_root=root, workflow_id="AppGenerator", context_variables={})
    assert context["capability_packs"] == []


@pytest.mark.asyncio
async def test_launch_context_provider_reads_build_context_via_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root = _build_context_root(tmp_path)
    monkeypatch.setenv("MOZAIKS_BUILD_CONTEXT_PATH", str(root))
    monkeypatch.setenv(
        "MOZAIKS_LAUNCH_CONTEXT_PROVIDER",
        "mozaiksai.core.session.build_context:merge_build_context",
    )

    context = await apply_launch_context_provider(
        workflow_id="AppGenerator",
        requested_workflow_id="AppGenerator",
        context_variables={"screen": "studio-create"},
        trigger_payload={},
        app_id="app_123",
        user_id="user_123",
        trigger_source="chat",
    )

    assert context["screen"] == "studio-create"
    assert context["operator_capabilities"] == ["enterprise_sso", "audit_export"]


def test_factory_workflow_catalog_yamls_exist() -> None:
    """All factory prompt catalog YAMLs must live under factory_app/build_context."""

    factory_build_context = Path(__file__).resolve().parents[1] / "factory_app" / "build_context"

    expected = {
        factory_build_context / "AppGenerator" / "domain_catalogs.yaml",
        factory_build_context / "AppGenerator" / "capability_routing.yaml",
        factory_build_context / "AppGenerator" / "file_contracts.yaml",
        factory_build_context / "AppGenerator" / "module_archetypes.yaml",
        factory_build_context / "AppGenerator" / "shell_presets.yaml",
        factory_build_context / "AppGenerator" / "workflow_archetypes.yaml",
        factory_build_context / "AgentGenerator" / "ag2_network_patterns.yaml",
    }
    for path in expected:
        assert path.exists(), f"Missing factory workflow catalog: {path}"

