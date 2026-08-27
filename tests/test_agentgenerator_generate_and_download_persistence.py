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
    context = _Context({"artifact_version_id": "av_parent_1"})
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


@pytest.mark.parametrize(
    "optional_writer",
    ["record_workflow_export", "record_workflow_artifacts"],
)
def test_record_context_and_artifacts_keeps_optional_projections_best_effort(
    monkeypatch,
    optional_writer: str,
) -> None:
    registration = AsyncMock(return_value=type("BuildRecord", (), {"id": "av_1"})())
    monkeypatch.setattr(
        generate_and_download_module,
        "_register_workflow_bundle_artifact_version",
        registration,
    )
    monkeypatch.setattr(generate_and_download_module, "record_workflow_export", AsyncMock())
    monkeypatch.setattr(generate_and_download_module, "record_workflow_artifacts", AsyncMock())
    monkeypatch.setattr(
        generate_and_download_module,
        optional_writer,
        AsyncMock(side_effect=RuntimeError("optional store unavailable")),
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

    registration.assert_awaited_once()

