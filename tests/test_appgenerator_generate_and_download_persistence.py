from __future__ import annotations

import asyncio
import importlib
import importlib.util
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import yaml

from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge, _wrap_tool_with_context
from mozaiksai.core.workflow.context.authority import (
    ContextAuthorityError,
    build_context_authority_policy,
)
from tests.factory_context import factory_context


def _load_generate_and_download_module():
    workspace = Path(__file__).resolve().parents[1]
    file_path = (
        workspace
        / "factory_app"
        / "workflows"
        / "AppGenerator"
        / "tools"
        / "generate_and_download.py"
    )
    module_name = "tests.appgenerator_generate_and_download_persistence_direct"
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load module spec for {file_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


generate_and_download_module = _load_generate_and_download_module()


class _Context:
    def __init__(self, initial=None) -> None:
        self.data = factory_context(initial)

    def set(self, key, value) -> None:
        self.data[key] = value

    def get(self, key, default=None):
        return self.data.get(key, default)


class _FakeStore:
    def __init__(self) -> None:
        self.calls = []

    async def save_database_migration(self, **kwargs):
        self.calls.append(dict(kwargs))
        return {
            "migration_id": kwargs["migration"]["migration_id"],
            "app_id": kwargs["app_id"],
            "build_id": kwargs["build_id"],
            "status": kwargs["status"],
        }


class _FakeArtifactStore:
    def __init__(self) -> None:
        self.calls = []

    async def create_build_record(self, **kwargs):
        self.calls.append(dict(kwargs))
        from mozaiksai.core.artifacts.models import BuildRecord

        return BuildRecord(
            _id="av_bundle_1",
            app_id=kwargs["app_id"],
            build_family=kwargs["build_family"],
            build_key=kwargs["build_key"],
            version_number=1,
            lineage_root_id="av_bundle_1",
            files_manifest=kwargs.get("files_manifest") or [],
            commit_metadata=kwargs.get("commit_metadata") or {},
        )


def test_download_projects_admitted_bundle_overlays_and_deletions() -> None:
    forbidden_path = "modules/billing/backend/token_wallet_ledger.py"
    context = ContextVariablesBridge(
        {
            "generated_files": {
                "app.json": '{"id":"billing-app"}',
                "modules/billing/backend/service.py": "class BillingService:\n    pass\n",
                forbidden_path: "class TokenWalletLedger:\n    pass\n",
            },
            "code_files": [
                {
                    "filename": "modules/billing/backend/service.py",
                    "content": "class BillingService:\n    async def list_products(self, ctx, **params):\n        return []\n",
                }
            ],
            "deleted_files": [forbidden_path],
        }
    )
    files_map = generate_and_download_module.admitted_app_file_map(context)

    assert files_map["app.json"] == '{"id":"billing-app"}'
    assert "async def list_products" in files_map["modules/billing/backend/service.py"]
    assert forbidden_path not in files_map


def test_persist_pending_schema_migration_records_staged_history(monkeypatch, tmp_path: Path) -> None:
    fake_store = _FakeStore()
    monkeypatch.setattr(generate_and_download_module, "BuilderArtifactStore", lambda: fake_store)

    context = _Context({"artifact_version_id": "artifact_123", "revision_scope": "feature"})
    migration_file = tmp_path / "data/migrations/m_1.json"
    migration_file.parent.mkdir(parents=True)
    accepted_bytes = b'{"migration_id":"m_1","changes":{"new_collections":["users"]}}\r\n'
    migration_file.write_bytes(accepted_bytes)
    record = asyncio.run(
        generate_and_download_module._persist_pending_schema_migration(
            pending_migration={"migration_id": "m_1", "changes": {"new_collections": ["users"]}},
            app_id="app_123",
            build_id="build_123",
            workflow_name="AppGenerator",
            chat_id="chat_123",
            context_variables=context,
            generated_app_dir=str(tmp_path),
        )
    )

    assert record["migration_id"] == "m_1"
    assert fake_store.calls[0]["artifact_version_id"] == "artifact_123"
    assert fake_store.calls[0]["change_class"] == "feature"
    assert context.data["persisted_database_migration"]["status"] == "staged"
    assert context.data["staged_database_migration_path"] == "data/migrations/m_1.json"
    assert migration_file.read_bytes() == accepted_bytes


def test_register_app_bundle_artifact_version_sets_context_and_parent(monkeypatch, tmp_path: Path) -> None:
    fake_artifact_store = _FakeArtifactStore()
    artifacts_mod = importlib.import_module("mozaiksai.core.artifacts")
    monkeypatch.setattr(artifacts_mod, "get_artifact_store", lambda: fake_artifact_store)
    monkeypatch.setattr(
        artifacts_mod,
        "resolve_latest_artifact_version_refs",
        lambda **kwargs: asyncio.sleep(0, result={
            "concept": "av_concept_1",
            "design_docs": "av_design_docs_1",
            "workflow_bundle": "av_workflow_bundle_1",
            "theme_capture": "av_theme_capture_1",
        }),
    )

    zip_path = tmp_path / "GeneratedApp.zip"
    zip_path.write_bytes(b"fake bundle bytes")
    context = _Context(
        {
            "artifact_version_id": "av_parent_1",
            "build_id": "build_1",
            "build_registry_id": "appreg_1",
            "app_bundle_acceptance_status": "passed",
            "app_bundle_acceptance_result": {
                "status": "passed",
                "validation_evidence": {"completed": ["bundle_scan"], "failed": []},
            },
            "app_bundle_validation_evidence": {"completed": ["bundle_scan"], "failed": []},
        }
    )

    artifact_version = asyncio.run(
        generate_and_download_module._register_app_bundle_artifact_version(
            app_id="app_123",
            user_id="user_123",
            workflow_name="AppGenerator",
            chat_id="chat_123",
            bundle_name="GeneratedApp",
            zip_path=zip_path,
            context_variables=context,
        )
    )

    assert artifact_version.id == "av_bundle_1"
    assert fake_artifact_store.calls[0]["build_family"] == "app_bundle"
    assert fake_artifact_store.calls[0]["build_key"] == "app_bundle"
    assert fake_artifact_store.calls[0]["parent_build_record_id"] == "av_parent_1"
    assert fake_artifact_store.calls[0]["canonical_inputs_version"] == {
        "concept": "av_concept_1",
        "design_docs": "av_design_docs_1",
        "workflow_bundle": "av_workflow_bundle_1",
        "theme_capture": "av_theme_capture_1",
    }
    assert fake_artifact_store.calls[0]["lifecycle_status"].value == "draft"
    assert fake_artifact_store.calls[0]["validation_status"].value == "passed"
    metadata = fake_artifact_store.calls[0]["commit_metadata"]["metadata"]
    assert metadata["build_id"] == "build_1"
    assert metadata["build_registry_id"] == "appreg_1"
    assert metadata["app_bundle_acceptance"]["status"] == "passed"
    assert metadata["validation_evidence"]["failed"] == []
    assert context.data["artifact_version_id"] == "av_bundle_1"


def test_register_app_bundle_artifact_version_marks_failed_acceptance(monkeypatch, tmp_path: Path) -> None:
    fake_artifact_store = _FakeArtifactStore()
    artifacts_mod = importlib.import_module("mozaiksai.core.artifacts")
    monkeypatch.setattr(artifacts_mod, "get_artifact_store", lambda: fake_artifact_store)
    monkeypatch.setattr(
        artifacts_mod,
        "resolve_latest_artifact_version_refs",
        lambda **kwargs: asyncio.sleep(0, result={}),
    )

    zip_path = tmp_path / "GeneratedApp.zip"
    zip_path.write_bytes(b"fake bundle bytes")
    context = _Context(
        {
            "app_bundle_acceptance_status": "failed",
            "app_bundle_acceptance_result": {
                "status": "failed",
                "validation_evidence": {"completed": [], "failed": ["module_wiring"]},
            },
            "app_bundle_validation_evidence": {"completed": [], "failed": ["module_wiring"]},
        }
    )

    asyncio.run(
        generate_and_download_module._register_app_bundle_artifact_version(
            app_id="app_123",
            user_id="user_123",
            workflow_name="AppGenerator",
            chat_id="chat_123",
            bundle_name="GeneratedApp",
            zip_path=zip_path,
            context_variables=context,
        )
    )

    assert fake_artifact_store.calls[0]["validation_status"].value == "failed"
    metadata = fake_artifact_store.calls[0]["commit_metadata"]["metadata"]
    assert metadata["validation_evidence"]["failed"] == ["module_wiring"]


def test_generate_and_download_blocks_failed_acceptance_before_writing(monkeypatch) -> None:
    async def fail_if_called(*_args, **_kwargs):
        raise AssertionError("finalization continued after failed acceptance")

    async def noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(generate_and_download_module, "_inject_agent_context_env", noop)
    from factory_app.app.modules.app_registry.backend.service import AppRegistryService

    monkeypatch.setattr(AppRegistryService, "update_build_status", fail_if_called)
    monkeypatch.setattr(generate_and_download_module, "use_ui_tool", fail_if_called)

    context = _Context(
        {
            "chat_id": "chat_123",
            "app_id": "app_123",
            "generated_files": {
                "config/secrets.yaml": "secrets:\n  - name: BAD\n    value: raw-secret\n",
            },
        }
    )

    result = asyncio.run(
        generate_and_download_module.generate_and_download(
            {},
            "Bundle ready.",
            context_variables=context,
        )
    )

    assert result["status"] == "error"
    assert result["app_bundle_acceptance_status"] == "failed"
    assert context.data["app_bundle_acceptance_status"] == "failed"
    assert result["bundle_errors"]


@pytest.mark.parametrize("line_ending", ["\n", "\r\n"])
def test_generate_and_download_uses_canonical_build_root_and_propagates_registration_failure(
    monkeypatch,
    tmp_path: Path,
    line_ending: str,
) -> None:
    async def noop(*_args, **_kwargs):
        return None

    async def passed_acceptance(**_kwargs):
        return {
            "passed": True,
            "status": "passed",
            "bundle_scan": {"errors": []},
            "validation_evidence": {"completed": ["bundle_scan"], "failed": []},
        }

    async def registration_failure(**_kwargs):
        raise RuntimeError("artifact registration failed")

    monkeypatch.setenv("MOZAIKS_GENERATED_ARTIFACTS_PATH", str(tmp_path / "generated"))
    monkeypatch.setattr(generate_and_download_module, "_inject_agent_context_env", noop)
    monkeypatch.setattr(generate_and_download_module, "run_app_bundle_acceptance_gate", passed_acceptance)
    monkeypatch.setattr(
        generate_and_download_module,
        "_register_app_bundle_artifact_version",
        registration_failure,
    )

    context = _Context(
        {
            "chat_id": "chat_123",
            "app_id": "app-123",
            "build_id": "build-123",
            "generated_files": {
                "app.json": '{"app_id":"app-123"}',
                "README.md": line_ending.join(["First line", "Second line", ""]),
            },
        }
    )

    with pytest.raises(RuntimeError, match="artifact registration failed"):
        asyncio.run(
            generate_and_download_module.generate_and_download(
                {},
                "Bundle ready.",
                context_variables=context,
            )
        )

    expected_app_dir = tmp_path / "generated" / "apps" / "app-123" / "build-123" / "app"
    assert (expected_app_dir / "app.json").exists()
    expected_bytes = line_ending.join(["First line", "Second line", ""]).encode("utf-8")
    assert (expected_app_dir / "README.md").read_bytes() == expected_bytes
    assert not (tmp_path / "generated_apps").exists()


@pytest.mark.parametrize("export_result", [{"success": True}, {"success": False}, None, "exception"])
def test_requested_github_export_failure_does_not_report_ready(monkeypatch, tmp_path, export_result):
    module = generate_and_download_module
    monkeypatch.setenv("MOZAIKS_GENERATED_ARTIFACTS_PATH", str(tmp_path / "generated"))
    for name in ("_inject_agent_context_env", "_register_app_bundle_artifact_version"):
        monkeypatch.setattr(module, name, AsyncMock(return_value=None))
    from factory_app.app.modules.app_registry.backend.service import AppRegistryService

    monkeypatch.setattr(AppRegistryService, "update_build_status", AsyncMock(return_value={"success": True}))
    async def accepted_snapshot(*, files, context_variables, **_kwargs):
        from factory_app.workflows.AppGenerator.tools.task_integrity import artifact_snapshot_digest

        context_variables.set("generated_files", files)
        context_variables.set("app_build_plan", {"build_tasks": []})
        accepted = {
            "passed": True, "status": "passed", "bundle_scan": {"errors": []},
            "validation_evidence": {"completed": ["bundle_scan"], "failed": []},
            "snapshot_digest": artifact_snapshot_digest(context_variables, files),
        }
        context_variables.set("app_bundle_acceptance_result", accepted)
        context_variables.set("app_bundle_acceptance_status", "passed")
        context_variables.set("app_validation_status", "skipped")
        context_variables.set("integration_tests_passed", True)
        return accepted

    monkeypatch.setattr(module, "run_app_bundle_acceptance_gate", accepted_snapshot)
    monkeypatch.setattr(module, "use_ui_tool", AsyncMock(return_value={
        "status": "completed", "action": "export_to_github",
    }))
    monkeypatch.setattr(module, "export_app_code_to_github", AsyncMock(
        return_value=export_result,
        side_effect=TimeoutError("export timed out") if export_result == "exception" else None,
    ))
    context = _Context({
        "chat_id": "chat", "app_id": "app", "build_id": "build",
        "generated_files": {"app.json": '{"app_id":"app"}'},
    })
    result = asyncio.run(module.generate_and_download({}, "Bundle ready.", context_variables=context))
    succeeded = export_result == {"success": True}
    assert result["status"] == ("success" if succeeded else "error")
    assert result["outcome"] == ("ready" if succeeded else "blocked")
    assert Path(result["bundle_zip"]).exists()


@pytest.mark.parametrize("agent,outcome", [
    ("AppSchemaAgent", "repair_schema"), ("ConfigMiddlewareAgent", "repair_integration"),
    ("ModelAgent", "repair_models"), ("ServiceAgent", "repair_service"),
    ("ControllerAgent", "repair_controller"), ("FrontendStubAgent", "repair_frontend"),
    ("DatabaseAgent", "repair_database"), ("UnapprovedAgent", "blocked"),
])
def test_export_failure_uses_the_selected_canonical_repair_lane(agent, outcome):
    result = generate_and_download_module._export_repair_outcome({
        "bundle_repair": {"status": "needs_revision", "target_agent": agent},
    })

    assert result == outcome


def test_export_failure_routes_evidenced_task_recovery_before_artifact_repair():
    result = generate_and_download_module._export_repair_outcome({
        "task_recovery_request": {"root_task_ids": ["failed_service"]},
        "bundle_repair": {"status": "needs_revision", "target_agent": "ServiceAgent"},
    })

    assert result == "repair_tasks"


@pytest.mark.asyncio
@pytest.mark.parametrize("reject_handoff", [False, True])
async def test_packaging_publishes_registered_review_path_under_real_context_authority(monkeypatch, tmp_path, reject_handoff):
    from factory_app.app.modules.app_registry.backend.service import AppRegistryService

    module = generate_and_download_module
    root = Path(__file__).resolve().parents[1]
    definitions = yaml.safe_load((root / "factory_app/workflows/AppGenerator/context_variables.yaml").read_text(encoding="utf-8"))["definitions"]
    if reject_handoff:
        definitions.pop("bundle_path")
    policy = build_context_authority_policy(workflow_name="AppGenerator", definitions=definitions)
    context = ContextVariablesBridge(factory_context({
        "chat_id": "chat", "app_id": "host", "build_id": "build", "user_id": "owner",
        "generated_files": {"app.json": '{"app_id":"target"}'},
    }), authority_policy=policy)
    context._bind_run(("AppGenerator", "host", "chat"), policy)
    monkeypatch.setenv("MOZAIKS_GENERATED_ARTIFACTS_PATH", str(tmp_path / "generated"))
    for name in ("_inject_agent_context_env", "_register_app_bundle_artifact_version"):
        monkeypatch.setattr(module, name, AsyncMock(return_value=None))
    update = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(AppRegistryService, "update_build_status", update)
    monkeypatch.setattr(module, "run_app_bundle_acceptance_gate", AsyncMock(return_value={
        "passed": True, "status": "passed", "bundle_scan": {"errors": []},
        "validation_evidence": {"completed": ["bundle_scan"], "failed": []},
    }))
    ui = AsyncMock(return_value={"status": "completed", "action": "continue"})
    monkeypatch.setattr(module, "use_ui_tool", ui)
    tool = _wrap_tool_with_context(module.generate_and_download, context)
    if reject_handoff:
        with pytest.raises(ContextAuthorityError, match="key=bundle_path"):
            await tool({}, "Bundle ready.")
        ui.assert_not_awaited()
        return
    result = await tool({}, "Bundle ready.")
    assert result["status"] == "success"
    staged = update.call_args.kwargs["current_build_run"]["bundle_path"]
    assert context.get("bundle_path") == staged == result["bundle_dir"]
    assert context.get("lifecycle_state") == "review"
    assert context.consume_authorized_context_updates(
        policy=policy, run_identity=("AppGenerator", "host", "chat"),
    )["set"]["bundle_path"] == staged
