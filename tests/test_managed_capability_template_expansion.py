from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest
import yaml

WORKSPACE = Path(__file__).resolve().parents[1]


def _load_module(relative_path: str, module_name: str):
    file_path = WORKSPACE / relative_path
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_resolver():
    return _load_module(
        "factory_app/workflows/AppGenerator/tools/resolve_managed_capability_templates.py",
        "tests.resolve_managed_capability_templates",
    )


class _Ctx:
    def __init__(self, data: dict[str, Any]) -> None:
        self.data = dict(data)

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value


@pytest.fixture()
def resolver():
    return _load_resolver()


def _write_pack(root: Path, pack_id: str, files: dict[str, str], *, status: str = "active") -> Path:
    pack_root = root / pack_id
    pack_root.mkdir(parents=True)
    (pack_root / "context.yaml").write_text(
        yaml.safe_dump(
            {
                "context_id": pack_id,
                "applies_to_workflows": ["AppGenerator"],
                "assets": [
                    {"path": "templates/", "kind": "templates"},
                ],
                "pack": {
                    "id": pack_id,
                    "version": "0.1.0",
                    "status": status,
                    "capability_source": "managed_capability",
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    for relative, content in files.items():
        path = pack_root / "templates" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return pack_root


def test_null_capability_packs_return_empty(resolver) -> None:
    assert resolver.resolve_managed_capability_templates(None) == []
    assert resolver.resolve_managed_capability_templates([]) == []


def test_selected_pack_materializes_all_templates(resolver, tmp_path: Path) -> None:
    pack_root = _write_pack(
        tmp_path,
        "wallet",
        {
            "services/integrations/wallet_client.py": "# wallet client\n",
            "modules/billing/module.yaml": "module:\n  id: billing\n",
        },
    )

    result = resolver.resolve_managed_capability_templates(
        [{"id": "wallet", "capability_source": "managed_capability", "pack_source_path": str(pack_root)}]
    )

    files = {item["filename"]: item["content"] for item in result
             if item["filename"] != ".mozaiks/pack_provenance.json"}
    assert files == {
        "modules/billing/module.yaml": "module:\n  id: billing\n",
        "services/integrations/wallet_client.py": "# wallet client\n",
    }


def test_selected_pack_renders_jinja_templates_and_strips_suffix(resolver, tmp_path: Path) -> None:
    pack_root = _write_pack(
        tmp_path,
        "operator_readiness",
        {
            "config/operator_readiness.yaml.j2": "profile: {{ readiness_profile }}\nledger: {{ evidence_ledger_path }}\n",
        },
    )

    result = resolver.resolve_managed_capability_templates(
        [
            {
                "id": "operator_readiness",
                "capability_source": "config_file",
                "pack_source_path": str(pack_root),
            }
        ],
        context_variables={
            "readiness_profile": "host_operator_platform",
            "evidence_ledger_path": "docs/operations/evidence-log.json",
        },
    )

    files = {item["filename"]: item["content"] for item in result
             if item["filename"] != ".mozaiks/pack_provenance.json"}
    assert files == {
        "config/operator_readiness.yaml": (
            "profile: host_operator_platform\n"
            "ledger: docs/operations/evidence-log.json"
        )
    }


def test_selected_pack_ignores_cache_and_bytecode_files(resolver, tmp_path: Path) -> None:
    pack_root = _write_pack(
        tmp_path,
        "mozaikspay",
        {"services/integrations/mozaikspay_client.py": "# client\n"},
    )
    cache_file = (
        pack_root
        / "templates"
        / "services"
        / "integrations"
        / "__pycache__"
        / "mozaikspay_client.cpython-313.pyc"
    )
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    cache_file.write_bytes(b"\xf3\r\r\n\x00\x00\x00\x00compiled")
    hidden_file = pack_root / "templates" / ".DS_Store"
    hidden_file.write_text("not a template", encoding="utf-8")

    result = resolver.resolve_managed_capability_templates(
        [{"id": "mozaikspay", "capability_source": "managed_capability", "pack_source_path": str(pack_root)}]
    )

    template_files = [f for f in result if f["filename"] != ".mozaiks/pack_provenance.json"]
    assert template_files == [
        {"filename": "services/integrations/mozaikspay_client.py", "content": "# client\n"}
    ]


@pytest.mark.asyncio
async def test_assemble_app_tasks_overlays_selected_pack_template(tmp_path: Path) -> None:
    pack_root = _write_pack(
        tmp_path,
        "wallet",
        {"services/integrations/wallet_client.py": "# deterministic wallet client\n"},
    )
    from factory_app.workflows.AppGenerator.tools.assemble_app_tasks import assemble_app_tasks

    ctx = _Ctx(
        {
            "app_id": "app_template_overlay",
            "app_build_plan": {
                "capability_packs": [
                    {"capability_pack_id": "wallet", "capability_source": "managed_capability"}
                ],
                "build_tasks": [],
            },
            "capability_packs": [
                {
                    "id": "wallet",
                    "capability_source": "managed_capability",
                    "pack_source_path": str(pack_root),
                }
            ],
            "app_task_batch_results": {
                "wallet_adapter": {
                    "code_files": [
                        {
                            "filename": "services/integrations/wallet_client.py",
                            "content": "# bad llm stub\n",
                        }
                    ]
                }
            },
        }
    )

    result = await assemble_app_tasks(context_variables=ctx)
    files = {item["filename"]: item["content"] for item in result["code_files"]}
    assert files["services/integrations/wallet_client.py"] == "# deterministic wallet client\n"


def test_inactive_pack_is_not_materialized(resolver, tmp_path: Path) -> None:
    pack_root = _write_pack(
        tmp_path,
        "wallet",
        {"services/integrations/wallet_client.py": "# inactive\n"},
        status="inactive",
    )

    result = resolver.resolve_managed_capability_templates(
        [{"id": "wallet", "pack_source_path": str(pack_root)}]
    )
    assert result == []


def test_missing_pack_context_raises(resolver, tmp_path: Path) -> None:
    with pytest.raises(resolver.ManagedCapabilityTemplateError, match="pack context"):
        resolver.resolve_managed_capability_templates(
            [{"id": "wallet", "pack_source_path": str(tmp_path)}]
        )


def test_unsafe_pack_id_raises(resolver, tmp_path: Path) -> None:
    pack_root = _write_pack(
        tmp_path,
        "wallet",
        {"services/integrations/wallet_client.py": "# wallet\n"},
    )

    with pytest.raises(resolver.ManagedCapabilityTemplateError, match="Unsafe pack_id"):
        resolver.resolve_managed_capability_templates(
            [{"id": "../wallet", "pack_source_path": str(pack_root)}]
        )


def test_conflicting_selected_pack_templates_raise(resolver, tmp_path: Path) -> None:
    wallet_root = _write_pack(
        tmp_path,
        "wallet",
        {"services/integrations/shared_client.py": "# wallet\n"},
    )
    pay_root = _write_pack(
        tmp_path,
        "mozaikspay",
        {"services/integrations/shared_client.py": "# pay\n"},
    )

    with pytest.raises(resolver.ManagedCapabilityTemplateError, match="Multiple selected"):
        resolver.resolve_managed_capability_templates(
            [
                {"id": "wallet", "pack_source_path": str(wallet_root)},
                {"id": "mozaikspay", "pack_source_path": str(pay_root)},
            ]
        )


def test_resolver_and_assembly_do_not_import_mozaiks_app() -> None:
    for relative in (
        "factory_app/workflows/AppGenerator/tools/resolve_managed_capability_templates.py",
        "factory_app/workflows/AppGenerator/tools/assemble_app_tasks.py",
    ):
        src = (WORKSPACE / relative).read_text(encoding="utf-8")
        import_lines = [
            line.strip()
            for line in src.splitlines()
            if line.strip().startswith(("import ", "from "))
        ]
        assert not any(
            line.startswith("from mozaiks_app") or line.startswith("import mozaiks_app")
            for line in import_lines
        )
