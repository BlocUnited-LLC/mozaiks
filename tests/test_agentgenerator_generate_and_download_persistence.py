from __future__ import annotations

import asyncio
import importlib
from pathlib import Path


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

    async def create_artifact_version(self, **kwargs):
        self.calls.append(dict(kwargs))
        return type("ArtifactVersion", (), {"id": "av_workflow_bundle_1"})()


def test_register_workflow_bundle_artifact_version_sets_canonical_inputs(monkeypatch, tmp_path: Path) -> None:
    fake_artifact_store = _FakeArtifactStore()
    artifacts_mod = importlib.import_module("mozaiksai.core.artifacts")
    monkeypatch.setattr(artifacts_mod, "get_artifact_store", lambda: fake_artifact_store)
    monkeypatch.setattr(
        artifacts_mod,
        "resolve_latest_artifact_version_refs",
        lambda **kwargs: asyncio.sleep(0, result={
            "concept": "av_concept_1",
            "build_plan": "av_build_plan_1",
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
    assert fake_artifact_store.calls[0]["artifact_kind"] == "workflow_bundle"
    assert fake_artifact_store.calls[0]["artifact_key"] == "LeadWorkflow"
    assert fake_artifact_store.calls[0]["parent_version_id"] == "av_parent_1"
    assert fake_artifact_store.calls[0]["canonical_inputs_version"] == {
        "concept": "av_concept_1",
        "build_plan": "av_build_plan_1",
        "design_docs": "av_design_docs_1",
    }
    assert fake_artifact_store.calls[0]["lifecycle_status"].value == "draft"
    assert fake_artifact_store.calls[0]["validation_status"].value == "pending"
    assert (
        fake_artifact_store.calls[0]["commit_metadata"]["metadata"]["workflow_integration_metadata"]
        == workflow_integration_metadata
    )
    assert context.data["artifact_version_id"] == "av_workflow_bundle_1"

