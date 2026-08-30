"""Contract tests for the operator_readiness build context pack."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

WORKSPACE = Path(__file__).resolve().parents[1]
BUILD_CONTEXT = WORKSPACE / "factory_app" / "build_context"
OPERATOR_READINESS = BUILD_CONTEXT / "operator_readiness"
TEMPLATES = OPERATOR_READINESS / "templates"


def _read_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_operator_readiness_context_registers_active_appgenerator_pack() -> None:
    context = _read_yaml(OPERATOR_READINESS / "context.yaml")

    assert context["context_id"] == "operator_readiness"
    assert "AppGenerator" in context["applies_to_workflows"]
    assert context["pack"]["id"] == "operator_readiness"
    assert context["pack"]["status"] == "active"
    assert context["pack"]["capability_source"] == "config_file"


def test_operator_readiness_context_declares_expected_capabilities() -> None:
    context = _read_yaml(OPERATOR_READINESS / "context.yaml")
    capability_ids = {cap["capability_id"] for cap in (context.get("capabilities") or [])}

    assert capability_ids == {
        "operator_readiness.profile.select",
        "operator_readiness.evidence.local",
        "operator_readiness.launch.check",
    }


def test_operator_readiness_context_declares_contract_and_templates_assets() -> None:
    context = _read_yaml(OPERATOR_READINESS / "context.yaml")
    asset_kinds = {asset["kind"] for asset in (context.get("assets") or [])}

    assert "contract" in asset_kinds
    assert "templates" in asset_kinds


def test_operator_readiness_pack_has_templates_directory() -> None:
    assert TEMPLATES.exists(), "operator_readiness pack must ship a templates/ directory"


def test_operator_readiness_templates_ship_expected_files() -> None:
    assert (TEMPLATES / "config" / "operator_readiness.yaml.j2").exists()
    assert (TEMPLATES / "scripts" / "check_operator_readiness_local.ps1.j2").exists()
    assert (TEMPLATES / "docs" / "operations" / "operator-readiness.md.j2").exists()


def test_operator_readiness_contract_required_outputs_are_templates_owned() -> None:
    contract = _read_yaml(OPERATOR_READINESS / "contract.yaml")
    owners = {output.get("owner") for output in contract["required_outputs"]}
    assert owners == {"templates"}


def test_operator_readiness_contract_required_outputs_include_config_script_and_doc() -> None:
    contract = _read_yaml(OPERATOR_READINESS / "contract.yaml")
    output_paths = {output["path"] for output in contract["required_outputs"]}

    assert "config/operator_readiness.yaml" in output_paths
    assert "scripts/check_operator_readiness_local.ps1" in output_paths
    assert "docs/operations/operator-readiness.md" in output_paths


def test_operator_readiness_contract_runtime_boundaries_cover_deterministic_output() -> None:
    contract = _read_yaml(OPERATOR_READINESS / "contract.yaml")
    boundary_ids = {b["id"] for b in (contract.get("runtime_boundaries") or [])}

    assert "structured_output_is_source_of_truth" in boundary_ids
    assert "config_file_is_the_feature_flag" in boundary_ids
    assert "local_evidence_is_not_production_proof" in boundary_ids
    assert "no_app_zero_defaults" in boundary_ids


def test_operator_readiness_contract_forbids_app_zero_specific_launcher_name() -> None:
    contract = _read_yaml(OPERATOR_READINESS / "contract.yaml")
    forbidden_paths = {o.get("path") for o in (contract.get("forbidden_outputs") or [])}

    assert "scripts/check_launch_readiness_local.ps1" in forbidden_paths


def test_operator_readiness_contract_documents_no_spend_behavior() -> None:
    contract_text = _read_text(OPERATOR_READINESS / "contract.yaml")
    assert "no-spend" in contract_text.lower()
    assert "local no-spend" in contract_text.lower()
    assert "AppBuildPlan.readiness_profile" in contract_text


def test_operator_readiness_templates_render_generic_not_app_zero_specific() -> None:
    config_text = _read_text(TEMPLATES / "config" / "operator_readiness.yaml.j2")
    script_text = _read_text(TEMPLATES / "scripts" / "check_operator_readiness_local.ps1.j2")
    doc_text = _read_text(TEMPLATES / "docs" / "operations" / "operator-readiness.md.j2")

    assert "check_launch_readiness_local" not in config_text
    assert "MOZAIKS_IMAGE_GATE_VERIFIED_AT" not in config_text
    assert "MOZAIKS_IMAGE_GATE_VERIFIED_AT" not in script_text
    assert "MOZAIKS_IMAGE_GATE_VERIFIED_AT" not in doc_text


def test_appgenerator_capability_directory_wires_operator_readiness_as_operator_pack() -> None:
    directory = _read_yaml(BUILD_CONTEXT / "AppGenerator" / "capability_directory.yaml")
    entries = {entry["id"]: entry for entry in directory["capabilities"]}

    assert "operator_readiness" in entries
    entry = entries["operator_readiness"]
    assert entry["capability_kind"] == "operator_pack"
    assert "launch" in entry.get("domains", [])
    assert "readiness_profile" in " ".join(entry.get("generator_notes", []))


def test_appgenerator_structured_outputs_include_readiness_profile() -> None:
    structured = _read_yaml(WORKSPACE / "factory_app" / "workflows" / "AppGenerator" / "structured_outputs.yaml")
    app_build_plan = structured["models"]["AppBuildPlan"]["fields"]

    assert "readiness_profile" in app_build_plan


def test_appgenerator_context_variables_define_readiness_profile() -> None:
    context_vars = _read_yaml(WORKSPACE / "factory_app" / "workflows" / "AppGenerator" / "context_variables.yaml")
    assert "readiness_profile" in context_vars.get("definitions", {})


def test_appplanagent_prompt_mentions_readiness_profile_handling() -> None:
    agents_text = _read_text(WORKSPACE / "factory_app" / "workflows" / "AppGenerator" / "agents.yaml")
    assert "readiness_profile" in agents_text
    assert "host_operator_platform" in agents_text
