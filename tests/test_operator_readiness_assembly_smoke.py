from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from factory_app.workflows.AppGenerator.tools.resolve_managed_capability_templates import (
    ManagedCapabilityTemplateError,
    resolve_templates_for_pack,
)
from tests.factory_context import factory_context

WORKSPACE = Path(__file__).resolve().parents[1]
OPERATOR_READINESS_PACK_ROOT = WORKSPACE / "factory_app" / "build_context" / "operator_readiness"


class _Ctx:
    def __init__(self, data: dict[str, Any]) -> None:
        self.data = factory_context(data)

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value


def _file_map(result: dict[str, Any]) -> dict[str, str]:
    return {
        str(item["filename"]): str(item["content"])
        for item in result.get("code_files") or []
        if isinstance(item, dict) and item.get("filename")
    }


@pytest.mark.asyncio
async def test_operator_readiness_pack_materializes_rendered_outputs_end_to_end() -> None:
    from factory_app.workflows.AppGenerator.tools.assemble_app_tasks import assemble_app_tasks

    ctx = _Ctx(
        {
            "app_id": "operator-readiness-smoke",
            "readiness_profile": "host_operator_platform",
            "evidence_ledger_path": "docs/operations/evidence-log.json",
            "launch_check_command": "python scripts/check_launch_readiness.py --json",
            "monetization_check_command": "python scripts/check_monetization_readiness.py --json",
            "app_build_plan": {
                "capability_packs": [
                    {
                        "capability_pack_id": "operator_readiness",
                        "id": "operator_readiness",
                        "capability_source": "config_file",
                        "pack_source_path": str(OPERATOR_READINESS_PACK_ROOT),
                    }
                ],
                "build_tasks": [{"task_id": "seed"}],
            },
            "capability_packs": [
                {
                    "capability_pack_id": "operator_readiness",
                    "id": "operator_readiness",
                    "capability_source": "config_file",
                    "pack_source_path": str(OPERATOR_READINESS_PACK_ROOT),
                }
            ],
            "app_task_batch_results": {
                "seed": {
                    "code_files": [
                        {
                            "filename": "app.json",
                            "content": "{\"appName\":\"Operator Readiness Smoke\"}",
                        }
                    ]
                }
            },
        }
    )

    result = await assemble_app_tasks(context_variables=ctx)
    file_map = _file_map(result)

    assert file_map["config/operator_readiness.yaml"].startswith("version: 1")
    assert "host_operator_platform" in file_map["config/operator_readiness.yaml"]
    assert "docs/operations/evidence-log.json" in file_map["config/operator_readiness.yaml"]
    assert file_map["scripts/check_operator_readiness_local.ps1"].startswith("$ErrorActionPreference")
    assert "host_operator_platform" in file_map["scripts/check_operator_readiness_local.ps1"]
    assert file_map["docs/operations/operator-readiness.md"].startswith("# Operator Readiness")
    assert "host_operator_platform" in file_map["docs/operations/operator-readiness.md"]
    assert "app.json" in file_map


def test_readiness_template_requires_profile() -> None:
    with pytest.raises(ManagedCapabilityTemplateError, match="readiness_profile"):
        resolve_templates_for_pack(OPERATOR_READINESS_PACK_ROOT, "operator_readiness")


def test_scanner_verifies_selected_pack_pins_without_rendering_context() -> None:
    """The public validation facade has files and selected packs, not build context.

    Declared-output layout checks need only the verified source, so they must
    not require template variables; a stale selected pin must still fail.
    """
    from factory_app.workflows.AppGenerator.tools.generated_bundle_scanner import (
        scan_generated_bundle,
    )
    from mozaiksai.core.validation.generated_app import (
        GeneratedAppValidationRequest,
        validate_generated_app_bundle,
    )

    pack = {"id": "operator_readiness", "pack_source_path": str(OPERATOR_READINESS_PACK_ROOT)}
    files = {"docs/operations/undeclared.md": ""}
    errors = scan_generated_bundle(files, capability_packs=[pack])
    assert not any("could not render" in error for error in errors)
    assert any("undeclared repository-support pack outputs" in error for error in errors)
    result = validate_generated_app_bundle(GeneratedAppValidationRequest(files=files, capability_packs=[pack]))
    assert not any("could not render" in item.message for item in result.diagnostics)

    stale = {**pack, "digest": "sha256:" + "0" * 64}
    assert any(
        "selected digest does not match" in error
        for error in scan_generated_bundle(files, capability_packs=[stale])
    )


def test_readiness_yaml_preserves_string_values() -> None:
    command = 'python check.py --label "Launch: local" # keep this argument'
    files = resolve_templates_for_pack(
        OPERATOR_READINESS_PACK_ROOT,
        "operator_readiness",
        context_variables={"readiness_profile": "host_operator_platform", "launch_check_command": command},
    )
    file_map = {item["filename"]: item["content"] for item in files}

    assert not any(path.endswith(".j2") for path in file_map)
    assert yaml.safe_load(file_map["config/operator_readiness.yaml"])["launch_check_command"] == command

