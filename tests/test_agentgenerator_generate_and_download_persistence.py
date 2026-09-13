from __future__ import annotations

import asyncio
import importlib
from pathlib import Path
from unittest.mock import AsyncMock

import pytest


def _load_generate_and_download_module():
    return importlib.import_module(
        "factory_app.workflows.AgentGenerator.tools.generate_and_download"
    )


generate_and_download_module = _load_generate_and_download_module()


class _Context:
    def __init__(self, initial=None) -> None:
        self.data = dict(initial or {})

    def set(self, key, value) -> None:
        self.data[key] = value

    def get(self, key, default=None):
        return self.data.get(key, default)


class _FakeArtifactStore:
    def __init__(self) -> None:
        self.calls = []

    async def create_build_record(self, **kwargs):
        self.calls.append(dict(kwargs))
        return type("BuildRecord", (), {"id": "av_workflow_bundle_1"})()


def test_register_workflow_bundle_requires_context_before_artifact_reads(monkeypatch) -> None:
    artifacts_mod = importlib.import_module("mozaiksai.core.artifacts")
    resolve_inputs = AsyncMock()
    monkeypatch.setattr(artifacts_mod, "resolve_latest_artifact_version_refs", resolve_inputs)
    store = _FakeArtifactStore()
    with pytest.raises(ValueError, match="requires runtime context"):
        asyncio.run(generate_and_download_module._register_workflow_bundle_artifact_version(
            app_id="app_123", user_id="user_123", workflow_name="AgentGenerator",
            chat_id="chat_123", bundle_name="LeadWorkflow", zip_path=None,
            context_variables=None, artifact_store=store,
        ))
    resolve_inputs.assert_not_awaited()
    assert store.calls == []


def test_register_workflow_bundle_artifact_version_sets_canonical_inputs(monkeypatch, tmp_path: Path) -> None:
    fake_artifact_store = _FakeArtifactStore()
    artifacts_mod = importlib.import_module("mozaiksai.core.artifacts")
    monkeypatch.setattr(artifacts_mod, "get_artifact_store", lambda: fake_artifact_store)
    monkeypatch.setattr(
        artifacts_mod,
        "resolve_latest_artifact_version_refs",
        lambda **kwargs: asyncio.sleep(0, result={
            "concept": "av_concept_1",
            "design_docs": "av_design_docs_1",
        }),
    )

    zip_path = tmp_path / "GeneratedWorkflow.zip"
    zip_path.write_bytes(b"fake workflow bytes")
    context = _Context({
        "artifact_version_id": "av_parent_1",
        "run_build_binding": {
            "build_registry_id": "registry_123", "target_app_id": "app_123",
            "build_id": "build_123", "phase": "refinement",
        },
    })
    workflow_integration_metadata = {
        "contract_version": "1.0",
        "workflows": [
            {
                "workflow_name": "LeadWorkflow",
                "capability_id": "lead-workflow",
                "startup_mode": "BackendOnly",
                "trigger_events": [
                    {
                        "event_type": "domain.leads.batch_requested",
                        "source": "domain",
                        "capability_id": "lead-workflow",
                    }
                ],
            }
        ],
    }

    artifact_version = asyncio.run(
        generate_and_download_module._register_workflow_bundle_artifact_version(
            app_id="app_123",
            user_id="user_123",
            workflow_name="AgentGenerator",
            chat_id="chat_123",
            bundle_name="LeadWorkflow",
            zip_path=zip_path,
            context_variables=context,
            workflow_integration_metadata=workflow_integration_metadata,
        )
    )

    assert artifact_version.id == "av_workflow_bundle_1"
    assert fake_artifact_store.calls[0]["build_family"] == "workflow_bundle"
    assert fake_artifact_store.calls[0]["build_key"] == "LeadWorkflow"
    assert fake_artifact_store.calls[0]["parent_build_record_id"] == "av_parent_1"
    assert fake_artifact_store.calls[0]["canonical_inputs_version"] == {
        "concept": "av_concept_1",
        "design_docs": "av_design_docs_1",
    }
    assert fake_artifact_store.calls[0]["lifecycle_status"].value == "draft"
    assert fake_artifact_store.calls[0]["validation_status"].value == "pending"
    assert (
        fake_artifact_store.calls[0]["commit_metadata"]["metadata"]["workflow_integration_metadata"]
        == workflow_integration_metadata
    )
    assert context.data["artifact_version_id"] == "av_workflow_bundle_1"


def test_record_context_and_artifacts_propagates_artifact_registration_failure(
    monkeypatch,
) -> None:
    export_write = AsyncMock()
    artifact_projection = AsyncMock()
    monkeypatch.setattr(generate_and_download_module, "record_workflow_export", export_write)
    monkeypatch.setattr(
        generate_and_download_module,
        "record_workflow_artifacts",
        artifact_projection,
    )
    monkeypatch.setattr(
        generate_and_download_module,
        "_register_workflow_bundle_artifact_version",
        AsyncMock(side_effect=RuntimeError("artifact store unavailable")),
    )
    monkeypatch.setattr(
        generate_and_download_module,
        "resolve_agent_api_url",
        lambda app_id: f"https://api.test/{app_id}",
    )
    monkeypatch.setattr(
        generate_and_download_module,
        "resolve_agent_websocket_url",
        lambda app_id: f"wss://ws.test/{app_id}",
    )

    with pytest.raises(RuntimeError, match="artifact store unavailable"):
        asyncio.run(
            generate_and_download_module._record_context_and_artifacts(
                app_id="app_123",
                user_id="user_123",
                chat_id="chat_123",
                pack_name="LeadWorkflow",
                bundle_entries=[],
                zip_path=None,
                context_variables=_Context(),
            )
        )

    export_write.assert_not_awaited()
    artifact_projection.assert_not_awaited()


def test_empty_workflow_partition_is_recorded_without_fake_endpoints(monkeypatch):
    import yaml

    from mozaiksai.core.workflow.agents.factory import (
        ContextVariablesBridge,
        _wrap_tool_with_context,
    )
    from mozaiksai.core.workflow.context.authority import build_context_authority_policy

    store = _FakeArtifactStore()
    artifacts_mod = importlib.import_module("mozaiksai.core.artifacts")
    monkeypatch.setattr(artifacts_mod, "get_artifact_store", lambda: store)
    monkeypatch.setattr(artifacts_mod, "resolve_latest_artifact_version_refs", AsyncMock(return_value={}))
    export_write = AsyncMock()
    monkeypatch.setattr(generate_and_download_module, "record_workflow_export", export_write)
    monkeypatch.setattr(generate_and_download_module, "record_workflow_artifacts", AsyncMock())
    data = {
        "run_build_binding": {"build_registry_id": "reg-1", "target_app_id": "app_123",
                              "build_id": "build-1", "phase": "genesis"},
        "workflow_plan_review": {"review_id": "review-1", "selection_hash": "hash", "status": "approved"},
        "generated_workflow_name": "StaleWorkflow",
    }
    config_path = Path(__file__).resolve().parents[1] / "factory_app/workflows/AgentGenerator/context_variables.yaml"
    definitions = yaml.safe_load(config_path.read_text(encoding="utf-8"))["definitions"]
    policy = build_context_authority_policy(workflow_name="AgentGenerator", definitions=definitions)
    context = ContextVariablesBridge(data, authority_policy=policy)
    context._bind_run(("AgentGenerator", "factory-test", "chat_123"), policy)
    record = _wrap_tool_with_context(generate_and_download_module._record_context_and_artifacts, context)
    asyncio.run(record(
        app_id="app_123", user_id="user_123", chat_id="chat_123", pack_name="Records",
        bundle_entries=[], zip_path=None,
    ))
    saved = store.calls[0]
    assert saved["files_manifest"] == []
    assert saved["app_id"] == "app_123"
    metadata = saved["commit_metadata"]["metadata"]
    assert metadata["workflow_plan_review"]["status"] == "approved"
    assert metadata["workflow_integration_metadata"]["workflows"] == []
    assert metadata["workflow_integration_metadata"]["primary_workflow"] is None
    assert metadata["artifact_path"] is None
    assert context.get("generated_workflow_name") is None
    assert context.get("artifact_version_id") == "av_workflow_bundle_1"
    extra = export_write.call_args.kwargs["extra_fields"]
    assert extra["agent_websocket_url"] is None
    assert extra["agent_api_url"] is None


@pytest.mark.parametrize(
    "optional_writer",
    ["record_workflow_export", "record_workflow_artifacts"],
)
def test_record_context_and_artifacts_keeps_optional_projections_best_effort(
    monkeypatch,
    optional_writer: str,
) -> None:
    events: list[str] = []

    async def register_bundle(*args, **kwargs):
        events.append("canonical_registration")
        return type("BuildRecord", (), {"id": "av_1"})()

    async def write_export(*args, **kwargs):
        events.append("workflow_export")
        if optional_writer == "record_workflow_export":
            raise RuntimeError("optional store unavailable")

    async def write_artifacts(*args, **kwargs):
        events.append("workflow_artifacts")
        if optional_writer == "record_workflow_artifacts":
            raise RuntimeError("optional store unavailable")

    registration = AsyncMock(side_effect=register_bundle)
    export_write = AsyncMock(side_effect=write_export)
    artifact_projection = AsyncMock(side_effect=write_artifacts)
    monkeypatch.setattr(
        generate_and_download_module,
        "_register_workflow_bundle_artifact_version",
        registration,
    )
    monkeypatch.setattr(
        generate_and_download_module,
        "record_workflow_export",
        export_write,
    )
    monkeypatch.setattr(
        generate_and_download_module,
        "record_workflow_artifacts",
        artifact_projection,
    )
    monkeypatch.setattr(
        generate_and_download_module,
        "resolve_agent_api_url",
        lambda app_id: f"https://api.test/{app_id}",
    )
    monkeypatch.setattr(
        generate_and_download_module,
        "resolve_agent_websocket_url",
        lambda app_id: f"wss://ws.test/{app_id}",
    )

    asyncio.run(
        generate_and_download_module._record_context_and_artifacts(
            app_id="app_123",
            user_id="user_123",
            chat_id="chat_123",
            pack_name="LeadWorkflow",
            bundle_entries=[],
            zip_path=None,
            context_variables=_Context(),
        )
    )

    assert events == [
        "canonical_registration",
        "workflow_export",
        "workflow_artifacts",
    ]
    registration.assert_awaited_once()
    export_write.assert_awaited_once()
    artifact_projection.assert_awaited_once()

