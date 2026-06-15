from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from mozaiksai.core.artifacts import (
    ArtifactLifecycleStatus,
    ArtifactValidationStatus,
    ArtifactVersionDoc,
)
from tests.import_utils import import_module_directly

_pack_config = import_module_directly("mozaiksai.core.workflow.pack.config")


def _real_factory_lineage_smoke_enabled() -> bool:
    raw = str(os.getenv("RUN_REAL_FACTORY_ARTIFACT_LINEAGE_SMOKE") or "")
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _live_real_factory_lineage_smoke_enabled() -> bool:
    raw = str(os.getenv("RUN_LIVE_REAL_FACTORY_ARTIFACT_LINEAGE_SMOKE") or "")
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _load_subprocess_json_payload(stdout: str) -> dict[str, Any]:
    start = stdout.find("{")
    assert start >= 0, stdout
    return json.loads(stdout[start:])


class _MemoryArtifactStore:
    def __init__(self) -> None:
        self._versions: dict[tuple[str, str], list[ArtifactVersionDoc]] = {}
        self.create_calls: list[dict[str, Any]] = []
        self._counter = 0

    def seed(
        self,
        *,
        app_id: str,
        artifact_kind: str,
        artifact_key: str | None = None,
    ) -> ArtifactVersionDoc:
        artifact_key = artifact_key or artifact_kind
        self._counter += 1
        artifact = ArtifactVersionDoc(
            _id=f"av_{artifact_kind}_{self._counter}",
            app_id=app_id,
            artifact_kind=artifact_kind,
            artifact_key=artifact_key,
            version_number=1,
            lineage_root_id=f"av_{artifact_kind}_{self._counter}",
            lifecycle_status=ArtifactLifecycleStatus.CURRENT,
            validation_status=ArtifactValidationStatus.SKIPPED,
            commit_metadata={"metadata": {"summary_payload": {"seeded": artifact_kind}}},
        )
        self._versions.setdefault((artifact_kind, artifact_key), []).insert(0, artifact)
        return artifact

    async def list_artifact_versions(
        self,
        *,
        app_id: str,
        artifact_kind: str,
        artifact_key: str | None = None,
        lifecycle_status: ArtifactLifecycleStatus | None = None,
        limit: int = 1,
    ) -> list[ArtifactVersionDoc]:
        keys = [(artifact_kind, artifact_key)] if artifact_key else [
            key for key in self._versions if key[0] == artifact_kind
        ]
        versions: list[ArtifactVersionDoc] = []
        for key in keys:
            versions.extend(self._versions.get(key, []))
        versions = [item for item in versions if item.app_id == app_id]
        if lifecycle_status is not None:
            versions = [item for item in versions if item.lifecycle_status == lifecycle_status]
        return versions[:limit]

    async def create_artifact_version(self, **kwargs: Any) -> ArtifactVersionDoc:
        self.create_calls.append(dict(kwargs))
        self._counter += 1
        artifact = ArtifactVersionDoc(
            _id=f"av_{kwargs['artifact_kind']}_{self._counter}",
            app_id=kwargs["app_id"],
            artifact_kind=kwargs["artifact_kind"],
            artifact_key=kwargs["artifact_key"],
            version_number=1,
            parent_version_id=kwargs.get("parent_version_id"),
            lineage_root_id=f"av_{kwargs['artifact_kind']}_{self._counter}",
            canonical_inputs_version=dict(kwargs.get("canonical_inputs_version") or {}),
            lifecycle_status=kwargs["lifecycle_status"],
            validation_status=kwargs["validation_status"],
            commit_metadata=kwargs["commit_metadata"],
        )
        self._versions.setdefault((artifact.artifact_kind, artifact.artifact_key), []).insert(0, artifact)
        return artifact


def _factory_workflows_root() -> Path:
    return Path(__file__).resolve().parents[1] / "factory_app" / "workflows"


@pytest.mark.asyncio
async def test_offline_build_sequence_smoke_persists_agent_and_app_artifact_chain() -> None:
    """Offline production smoke for the build journey.

    This uses the real factory registry and real summary artifact persistence,
    but replaces storage with an in-memory artifact store. It proves the
    cross-workflow contract without OpenAI, AG2 model calls, MongoDB, or HTTP.
    """

    graph = _pack_config.load_global_pack_graph(workflows_root=_factory_workflows_root())
    assert graph is not None

    build = next(sequence for sequence in graph.journeys if sequence.id == "build")
    workflow_steps = [step.workflows for step in build.steps if step.workflows]
    transition_steps = [step.transition for step in build.steps if step.transition]

    assert workflow_steps == [
        ["ValueEngine"],
        ["ThemeCapture"],
        ["DesignDocs"],
        ["SubscriptionContractDesigner"],
        ["AgentGenerator"],
        ["AppGenerator"],
    ]
    assert transition_steps == [
        "app_type_selector",
        "coding_journey_selector",
        "database_setup_selector",
        "app_review",
        "build_satisfaction_rating",
    ]
    assert graph.artifact_dependency_graph["workflow_bundle"] == [
        "design_docs",
        "subscription_contract",
    ]
    assert "workflow_bundle" in graph.artifact_dependency_graph["app_bundle"]

    store = _MemoryArtifactStore()
    app_id = "offline-build-sequence-smoke"
    design_docs = store.seed(app_id=app_id, artifact_kind="design_docs")
    subscription_contract = store.seed(app_id=app_id, artifact_kind="subscription_contract")
    theme_capture = store.seed(app_id=app_id, artifact_kind="theme_capture")

    from factory_app.workflows.AgentGenerator.tools.platform.build_lifecycle import (
        _persist_workflow_bundle_artifact,
    )
    from factory_app.workflows.AppGenerator.tools.platform.build_lifecycle import (
        _persist_app_bundle_artifact,
    )

    await _persist_workflow_bundle_artifact(
        app_id=app_id,
        chat_id="chat_agentgenerator",
        user_id="user_1",
        workflow_name="AgentGenerator",
        build_mode=None,
        artifact_store=store,
    )
    await _persist_app_bundle_artifact(
        app_id=app_id,
        chat_id="chat_appgenerator",
        user_id="user_1",
        workflow_name="AppGenerator",
        build_mode=None,
        artifact_store=store,
    )

    workflow_call = next(call for call in store.create_calls if call["artifact_kind"] == "workflow_bundle")
    app_call = next(call for call in store.create_calls if call["artifact_kind"] == "app_bundle")
    workflow_bundle = store._versions[("workflow_bundle", "workflow_bundle")][0]

    assert workflow_call["source_workflow"] == "AgentGenerator"
    assert workflow_call["canonical_inputs_version"] == {
        "design_docs": design_docs.id,
        "subscription_contract": subscription_contract.id,
    }

    app_inputs = app_call["canonical_inputs_version"]
    assert app_call["source_workflow"] == "AppGenerator"
    assert app_inputs["design_docs"] == design_docs.id
    assert app_inputs["subscription_contract"] == subscription_contract.id
    assert app_inputs["theme_capture"] == theme_capture.id
    assert app_inputs["workflow_bundle"] == workflow_bundle.id
    assert "brand" not in app_inputs
    assert app_call["lifecycle_status"] == ArtifactLifecycleStatus.CURRENT
    assert app_call["validation_status"] == ArtifactValidationStatus.SKIPPED


@pytest.mark.asyncio
async def test_offline_factory_artifact_lineage_smoke_hydrates_workflow_metadata() -> None:
    from scripts.smoke_factory_artifact_lineage import (
        run_offline_factory_artifact_lineage_smoke,
    )

    result = await run_offline_factory_artifact_lineage_smoke()

    assert result["success"] is True, result["validation_errors"]
    assert result["sequence"]["workflow_steps"] == [
        ["ValueEngine"],
        ["ThemeCapture"],
        ["DesignDocs"],
        ["SubscriptionContractDesigner"],
        ["AgentGenerator"],
        ["AppGenerator"],
    ]
    assert result["artifact_lineage"]["workflow_bundle_inputs"] == {
        "design_docs": result["artifact_lineage"]["design_docs_id"],
        "subscription_contract": result["artifact_lineage"]["subscription_contract_id"],
    }
    assert result["artifact_lineage"]["app_bundle_inputs"]["design_docs"] == result["artifact_lineage"]["design_docs_id"]
    assert (
        result["artifact_lineage"]["app_bundle_inputs"]["subscription_contract"]
        == result["artifact_lineage"]["subscription_contract_id"]
    )
    assert result["artifact_lineage"]["app_bundle_inputs"]["theme_capture"] == result["artifact_lineage"]["theme_capture_id"]
    assert result["artifact_lineage"]["app_bundle_inputs"]["workflow_bundle"] == result["artifact_lineage"]["workflow_bundle_id"]
    assert result["hydration"]["status"] == "hydrated"
    assert result["hydration"]["source"] == "workflow_bundle_artifact"
    assert result["workflow_integration_metadata"]["source_artifact_version_id"] == result["artifact_lineage"]["workflow_bundle_id"]
    assert "workflow_integration" in result["appgenerator_acceptance"]["validation_evidence"]["completed"]
    assert result["export_gate"]["allow_export"] is True
    assert result["runtime_loader"]["workflow_reaction_loaded"] is True


@pytest.mark.asyncio
async def test_offline_factory_artifact_lineage_smoke_uses_hydrated_primary_workflow_metadata() -> None:
    from scripts.smoke_factory_artifact_lineage import (
        run_offline_factory_artifact_lineage_smoke,
    )

    result = await run_offline_factory_artifact_lineage_smoke(
        workflow_integration_metadata={
            "contract_version": "1.0",
            "bundle_name": "EscalationAutomation",
            "source": "test_custom_workflow_metadata",
            "workflows": [
                {
                    "workflow_name": "EscalationWorkflow",
                    "capability_id": "support-escalation-workflow",
                    "startup_mode": "BackendOnly",
                    "trigger_events": [
                        {
                            "event_type": "domain.support_ticket.escalation_requested",
                            "capability_id": "support-escalation-workflow",
                            "description": "Routes escalated support tickets.",
                        }
                    ],
                }
            ],
        }
    )

    assert result["success"] is True, result["validation_errors"]
    assert result["workflow_integration_metadata"]["source"] == "test_custom_workflow_metadata"
    assert (
        result["appgenerator_acceptance"]["fixture_workflow_integration"]["workflow_name"]
        == "EscalationWorkflow"
    )
    assert (
        result["appgenerator_acceptance"]["fixture_workflow_integration"]["capability_id"]
        == "support-escalation-workflow"
    )
    assert "support-escalation-workflow" in result["runtime_loader"]["reaction_capability_ids"]


@pytest.mark.skipif(
    not _real_factory_lineage_smoke_enabled(),
    reason="Set RUN_REAL_FACTORY_ARTIFACT_LINEAGE_SMOKE=1 to run the Mongo-backed lineage smoke.",
)
def test_real_store_factory_artifact_lineage_smoke_hydrates_workflow_metadata() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    completed = subprocess.run(
        [sys.executable, "scripts/smoke_factory_artifact_lineage.py", "--real-store"],
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    result = _load_subprocess_json_payload(completed.stdout)

    assert result["success"] is True, result["validation_errors"]
    assert result["mode"] == "real_store"
    assert result["artifact_lineage"]["workflow_bundle_inputs"] == {
        "design_docs": result["artifact_lineage"]["design_docs_id"],
        "subscription_contract": result["artifact_lineage"]["subscription_contract_id"],
    }
    assert result["artifact_lineage"]["app_bundle_inputs"]["workflow_bundle"] == result["artifact_lineage"]["workflow_bundle_id"]
    assert result["hydration"]["status"] == "hydrated"
    assert result["hydration"]["source"] == "workflow_bundle_artifact"
    assert result["workflow_integration_metadata"]["source_artifact_version_id"] == result["artifact_lineage"]["workflow_bundle_id"]
    assert "workflow_integration" in result["appgenerator_acceptance"]["validation_evidence"]["completed"]
    assert result["export_gate"]["allow_export"] is True
    assert result["runtime_loader"]["workflow_reaction_loaded"] is True


@pytest.mark.skipif(
    not _live_real_factory_lineage_smoke_enabled(),
    reason=(
        "Set RUN_LIVE_REAL_FACTORY_ARTIFACT_LINEAGE_SMOKE=1 to run the live "
        "AgentGenerator plus Mongo-backed lineage smoke."
    ),
)
def test_live_real_store_factory_artifact_lineage_smoke_hydrates_live_workflow_metadata() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    completed = subprocess.run(
        [
            sys.executable,
            "scripts/smoke_factory_artifact_lineage.py",
            "--real-store",
            "--live-agentgenerator",
            "--timeout-seconds",
            "600",
        ],
        cwd=repo_root,
        text=True,
        capture_output=True,
        check=False,
        timeout=900,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    result = _load_subprocess_json_payload(completed.stdout)

    assert result["success"] is True, result["validation_errors"]
    assert result["mode"] == "live_real_store"
    assert result["live_agentgenerator"]["success"] is True
    assert result["live_agentgenerator"]["semantic_drift"]["valid"] is True
    assert int((result["live_agentgenerator"]["task_run_trace"] or {}).get("max_overlap") or 0) >= 2
    assert result["artifact_lineage"]["app_bundle_inputs"]["workflow_bundle"] == result["artifact_lineage"]["workflow_bundle_id"]
    assert result["hydration"]["status"] == "hydrated"
    assert result["hydration"]["source"] == "workflow_bundle_artifact"
    assert result["workflow_integration_metadata"]["source"] == "live_agentgenerator_pack_smoke"
    assert "workflow_integration" in result["appgenerator_acceptance"]["validation_evidence"]["completed"]
    assert result["export_gate"]["allow_export"] is True
    assert result["runtime_loader"]["workflow_reaction_loaded"] is True
