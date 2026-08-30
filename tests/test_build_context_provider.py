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
from mozaiksai.core.workflow.context.projection import inject_build_context_projections


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
                "values": {
                    "operator_capabilities": ["enterprise_sso", "audit_export"],
                    "capability_registry": {
                        "sso": {
                            "capability_pack_id": "enterprise_sso",
                            "implementation_mode": "external_integration",
                        }
                    },
                },
                "pack": {
                    "id": "enterprise_sso",
                    "version": "0.1.0",
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


def test_load_build_context_rejects_unknown_root_key(tmp_path: Path) -> None:
    context_file = tmp_path / "build_context" / "AcmeEnterprise" / "context.yaml"
    _write_yaml(
        context_file,
        {
            "context_id": "acme_operator",
            "applies_to_workflows": ["AppGenerator"],
            "assets": [],
            "secret_override": "do not project",
        },
    )

    with pytest.raises(BuildContextError, match="secret_override"):
        load_build_context(context_file)


def test_load_build_context_rejects_unknown_pack_key(tmp_path: Path) -> None:
    context_file = tmp_path / "build_context" / "AcmeEnterprise" / "context.yaml"
    _write_yaml(
        context_file,
        {
            "context_id": "acme_operator",
            "applies_to_workflows": ["AppGenerator"],
            "assets": [],
            "pack": {
                "id": "acme_operator",
                "version": "0.1.0",
                "hidden_injection": "do not project",
            },
        },
    )

    with pytest.raises(BuildContextError, match="pack.hidden_injection"):
        load_build_context(context_file)


def test_unknown_asset_kind_rejected_before_workflow_projection(tmp_path: Path) -> None:
    root = tmp_path / "build_context"
    context_file = root / "AcmeEnterprise" / "context.yaml"
    _write_yaml(
        context_file,
        {
            "context_id": "acme_operator",
            "applies_to_workflows": ["AppGenerator"],
            "assets": [{"path": "unknown.yaml", "kind": "agent_payload"}],
            "projections": {
                "context_variables": {
                    "operator_payload": {"value": "must not project"},
                },
            },
        },
    )

    with pytest.raises(BuildContextError, match="asset.kind"):
        merge_build_context(build_context_root=root, workflow_id="AppGenerator", context_variables={})


def test_malformed_assets_do_not_reach_agent_projection(tmp_path: Path) -> None:
    root = tmp_path / "build_context"
    context_root = root / "AcmeEnterprise"
    _write_yaml(
        context_root / "context.yaml",
        {
            "context_id": "acme_operator",
            "applies_to_workflows": ["AppGenerator"],
            "assets": [{"path": "catalog.yaml", "kind": "catalog", "unexpected": True}],
        },
    )
    _write_yaml(context_root / "catalog.yaml", {"items": [{"id": "should_not_render"}]})

    class _Agent:
        name = "AppPlanAgent"
        _system_message = "base prompt"
        context_variables = {"build_context_root": str(root)}

    agent = _Agent()
    inject_build_context_projections(agent, [])

    assert agent._system_message == "base prompt"


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
            "pack": {"id": "mozaikspay", "version": "0.1.0", "status": "inactive"},
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


def test_all_first_party_build_contexts_load() -> None:
    factory_build_context = Path(__file__).resolve().parents[1] / "factory_app" / "build_context"

    for context_path in sorted(factory_build_context.glob("*/context.yaml")):
        loaded = load_build_context(context_path)
        assert loaded["context_id"]


def test_load_and_materialization_accept_same_representative_pack(tmp_path: Path) -> None:
    from factory_app.workflows.AppGenerator.tools.resolve_managed_capability_templates import (
        resolve_templates_for_pack,
    )

    pack_root = tmp_path / "build_context" / "valid_pack"
    _write_yaml(
        pack_root / "context.yaml",
        {
            "context_id": "valid_pack",
            "applies_to_workflows": ["AppGenerator"],
            "assets": [{"path": "templates", "kind": "templates"}],
            "pack": {
                "id": "valid_pack",
                "version": "0.1.0",
                "status": "active",
                "capability_source": "generated_module",
            },
        },
    )
    template = pack_root / "templates" / "modules" / "valid_pack" / "module.yaml"
    template.parent.mkdir(parents=True)
    template.write_text("module_id: valid_pack\n", encoding="utf-8")

    assert load_build_context(pack_root / "context.yaml")["context_id"] == "valid_pack"
    files = resolve_templates_for_pack(pack_root, "valid_pack")
    assert files == [{"filename": "modules/valid_pack/module.yaml", "content": "module_id: valid_pack\n"}]


def test_load_and_materialization_reject_same_representative_pack(tmp_path: Path) -> None:
    from factory_app.workflows.AppGenerator.tools.resolve_managed_capability_templates import (
        ManagedCapabilityTemplateError,
        resolve_templates_for_pack,
    )

    pack_root = tmp_path / "build_context" / "invalid_pack"
    _write_yaml(
        pack_root / "context.yaml",
        {
            "context_id": "invalid_pack",
            "applies_to_workflows": ["AppGenerator"],
            "assets": [{"path": "templates", "kind": "templates"}],
            "pack": {
                "id": "invalid_pack",
                "version": "0.1.0",
                "status": "active",
                "capability_source": "generated_module",
                "hidden_injection": "reject",
            },
        },
    )
    (pack_root / "templates").mkdir()

    with pytest.raises(BuildContextError, match="pack.hidden_injection"):
        load_build_context(pack_root / "context.yaml")
    with pytest.raises(ManagedCapabilityTemplateError, match="schema validation"):
        resolve_templates_for_pack(pack_root, "invalid_pack")

