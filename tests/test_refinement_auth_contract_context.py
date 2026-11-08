"""Selected auth edits receive the runtime model without granting authority."""

from __future__ import annotations

import copy
import hashlib
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml
from jsonschema import Draft202012Validator

from factory_app.refinement_harness.tools.get_artifact_workspace_scope import (
    get_artifact_workspace_scope,
)
from mozaiksai.control_plane import (
    CodingWorkerPlan,
    CodingWorkerRequest,
    ControlPlaneCodingCapabilityConfig,
    ControlPlaneConfig,
    ControlPlaneToolContext,
    ControlPlaneToolExecutor,
    ControlPlaneToolResult,
    FileUpdate,
    StructuredOutputCodingProvider,
)
from mozaiksai.control_plane.loader import load_refinement_harness
from mozaiksai.core.artifacts.models import ArtifactVersionDoc
from mozaiksai.core.runtime.app.auth_contract import (
    AppAuthContract,
    AppAuthContractError,
    validate_app_auth_contract,
)
from mozaiksai.core.runtime.app.paths import APP_AUTH_CONFIG_PATH

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def auth_document():
    template = ROOT / "factory_app/build_context/webapp_builder/templates/config/auth.yaml"
    document = yaml.safe_load(
        template.read_text(encoding="utf-8").replace("{{AUTH_DEFAULT_ROUTE}}", "/")
    )
    document["authorization"] = {"roles": []}
    validate_app_auth_contract(document)
    return document


def workspace_store(tmp_path, auth_document, *, include_auth=True, family="app_bundle"):
    archive = tmp_path / "artifact.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        if include_auth:
            bundle.writestr("GeneratedApp/config/auth.yaml", yaml.safe_dump(auth_document))
        bundle.writestr("GeneratedApp/modules/notes/module.yaml", "actions: []\n")
        bundle.writestr("GeneratedApp/brand/theme_config.json", "{}\n")
    record = ArtifactVersionDoc(
        _id="av_auth",
        app_id="app_1",
        build_family=family,
        build_key=family,
        version_number=1,
        lineage_root_id="av_root",
        lifecycle_status="current",
        validation_status="passed",
        source_workflow="AppGenerator",
        files_manifest=[
            {
                "path": archive.name,
                "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                "size_bytes": archive.stat().st_size,
            }
        ],
        commit_metadata={"metadata": {"artifact_path": str(archive)}},
    )
    store = SimpleNamespace(get_build_record=AsyncMock(return_value=record))
    return store


def context(paths, *, family="app_bundle"):
    return ControlPlaneToolContext(
        checkpoint="coding_requested",
        app_id="app_1",
        build_family=family,
        build_key=family,
        build_record_id="av_auth",
        extra={"selected_file_paths": paths},
    )


@pytest.mark.asyncio
async def test_auth_scope_projects_runtime_schema_and_keeps_invalid_grants_rejected(
    tmp_path, auth_document
):
    before = copy.deepcopy(auth_document)
    scope = await get_artifact_workspace_scope(
        context=context([APP_AUTH_CONFIG_PATH]),
        artifact_store=workspace_store(tmp_path, auth_document),
    )
    schema = scope["selected_file_contracts"][APP_AUTH_CONFIG_PATH]
    assert schema == AppAuthContract.model_json_schema()
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    validator.validate(auth_document)
    valid = copy.deepcopy(auth_document)
    valid["authorization"]["roles"] = [{"id": "member", "permissions": ["notes.read"]}]
    validator.validate(valid)
    validate_app_auth_contract(valid)
    invalid = copy.deepcopy(valid)
    invalid["authorization"]["roles"][0] = {"id": "member", "grants": ["notes.read"]}
    errors = list(validator.iter_errors(invalid))
    assert {error.validator for error in errors} == {"required", "additionalProperties"}
    with pytest.raises(AppAuthContractError, match="permissions: Field required"):
        validate_app_auth_contract(invalid)
    assert auth_document == before
    assert scope["selected_file_paths"] == [APP_AUTH_CONFIG_PATH]
    assert set(scope["selected_file_contracts"]) == {APP_AUTH_CONFIG_PATH}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "paths",
    [[], ["modules/notes/module.yaml"], ["brand/theme_config.json"], ["app/config/auth.yaml"]],
)
async def test_unrelated_selection_does_not_receive_auth_contract(tmp_path, auth_document, paths):
    scope = await get_artifact_workspace_scope(
        context=context(paths),
        artifact_store=workspace_store(tmp_path, auth_document),
    )
    assert scope["selected_file_contracts"] == {}
    assert scope["selected_file_paths"] == paths


@pytest.mark.asyncio
@pytest.mark.parametrize("include_auth,family", [(False, "app_bundle"), (True, "workflow_bundle")])
async def test_auth_contract_requires_selected_existing_app_file(
    tmp_path, auth_document, include_auth, family
):
    scope = await get_artifact_workspace_scope(
        context=context([APP_AUTH_CONFIG_PATH], family=family),
        artifact_store=workspace_store(
            tmp_path, auth_document, include_auth=include_auth, family=family
        ),
    )
    assert scope["selected_file_contracts"] == {}


@pytest.mark.asyncio
async def test_coding_provider_receives_auth_contract_from_real_selected_file_tool(
    tmp_path, auth_document, monkeypatch
):
    store = workspace_store(tmp_path, auth_document)
    monkeypatch.setattr(
        "factory_app.refinement_harness.tools.get_artifact_workspace_scope.get_artifact_store",
        lambda: store,
    )
    pack = load_refinement_harness(
        app_root=tmp_path / "app", factory_root=ROOT / "factory_app/refinement_harness"
    )
    real_executor = ControlPlaneToolExecutor(pack_loader=lambda: pack)

    class ScopedContextExecutor:
        async def execute_tool(self, call, *, context):
            if call.tool_id == "get_artifact_workspace_scope":
                return await real_executor.execute_tool(call, context=context)
            return ControlPlaneToolResult(success=True, output={"present": False})

    contents = yaml.safe_dump(auth_document)
    plan = CodingWorkerPlan(
        summary="Keep scoped auth unchanged.",
        owned_paths=[APP_AUTH_CONFIG_PATH],
        updated_files=[FileUpdate(path=APP_AUTH_CONFIG_PATH, content=contents)],
        validation_strategy="docker",
        validation_commands=[],
        start_preview=False,
        needs_human_review=False,
        rationale="Test supplied context only.",
    )
    runner = SimpleNamespace(run=AsyncMock(return_value=plan))
    config = ControlPlaneConfig(
        enabled=True,
        llm_profiles={"codegen": {"llm_config": {"model": "test-model"}}},
        coding=ControlPlaneCodingCapabilityConfig(enabled=True, llm_profile="codegen"),
    )
    provider = StructuredOutputCodingProvider(
        agent_runner=runner,
        config_loader=lambda: config,
        pack_loader=lambda: pack,
        tool_executor=ScopedContextExecutor(),
    )
    result = await provider.execute(
        CodingWorkerRequest(
            app_id="app_1",
            user_id="owner",
            build_family="app_bundle",
            build_key="app_bundle",
            build_record_id="av_auth",
            change_class="patch",
            raw_user_request="Review the selected auth declaration.",
            files={APP_AUTH_CONFIG_PATH: contents},
            metadata={"selected_file_paths": [APP_AUTH_CONFIG_PATH]},
        )
    )
    assert result.status == "completed"
    call = runner.run.await_args.kwargs
    payload = json.loads(
        call["user_prompt"].split("payload_json:\n", 1)[1].split("\n\nReturn a JSON object", 1)[0]
    )
    scope = payload["refinement_context"]["get_artifact_workspace_scope"]
    assert scope["selected_file_contracts"] == {
        APP_AUTH_CONFIG_PATH: AppAuthContract.model_json_schema()
    }
    assert "selected_file_contracts" in call["system_prompt"]
    assert payload["input_files"] == {APP_AUTH_CONFIG_PATH: contents}
    assert result.owned_paths == [APP_AUTH_CONFIG_PATH]
    assert result.changed_files[0].content == contents
