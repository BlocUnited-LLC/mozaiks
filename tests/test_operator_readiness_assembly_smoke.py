from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

WORKSPACE = Path(__file__).resolve().parents[1]
OPERATOR_READINESS_PACK_ROOT = WORKSPACE / "factory_app" / "build_context" / "operator_readiness"


class _Ctx:
    def __init__(self, data: dict[str, Any]) -> None:
        self.data = dict(data)

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
                "build_tasks": [],
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

