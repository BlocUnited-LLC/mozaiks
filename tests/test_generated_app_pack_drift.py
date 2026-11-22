"""Fresh source drift is a public validation diagnostic, never approval."""
import hashlib
from unittest.mock import AsyncMock

import pytest

from factory_app.workflows._shared.pack_selection import pin_pack_choices, rows_for_pack_choices
from mozaiksai.core.artifacts.summary_artifacts import _summary_bytes
from mozaiksai.core.validation import (
    GeneratedAppValidationRequest,
    validate_generated_app_bundle,
    validate_generated_app_candidate,
)
from mozaiksai.core.validation.compilation_inputs import capture_app_compilation_inputs
from tests.test_app_compilation_inputs import resolve, saved  # noqa: F401 - scoped store fixture


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["registry", "digest"])
async def test_public_facade_reports_pack_drift_as_invalid_approved_compilation(saved, tmp_path, monkeypatch, change):  # noqa: F811 - imported pytest fixture
    from tests.reviewed_pack_fixture import reviewed_pack_fixture
    from tests.test_designdocs_capability_ownership import _search_pack

    _search_pack(tmp_path, monkeypatch)
    store, context, bundle = saved
    packs = rows_for_pack_choices({}, pin_pack_choices({}, ["managed_search"]))
    context["capability_packs"] = packs
    context["app_build_plan"]["capability_packs"] = packs
    reviewed = reviewed_pack_fixture(context, ["managed_search"])
    record = reviewed["design_docs_artifact"]
    owner = record["commit_metadata"]["author_user_id"]
    payload = record["commit_metadata"]["metadata"]["summary_payload"]
    assert payload["access_policy_review"]["reviewed_by"] == owner
    design = store.records["design"]
    design.commit_metadata.author_user_id = owner
    design.commit_metadata.metadata["summary_payload"] = payload
    raw = _summary_bytes(payload)
    design.files_manifest[0].sha256 = hashlib.sha256(raw).hexdigest()
    design.files_manifest[0].size_bytes = len(raw)
    context["design_docs_artifact"] = design.model_dump(mode="json", by_alias=True)
    bundle.commit_metadata.author_user_id = owner
    bundle.commit_metadata.metadata["capability_packs"] = packs
    bundle.commit_metadata.metadata["compilation_inputs"] = await capture_app_compilation_inputs(
        context, owner_user_id=owner, archive_sha256=bundle.files_manifest[0].sha256, artifact_store=store,
    )
    approval = await resolve(saved, owner_user_id=owner)
    assert approval is not None
    if change == "registry":
        empty = tmp_path / "empty-registry"
        empty.mkdir()
        monkeypatch.setenv("MOZAIKS_BUILD_CONTEXT_PATH", str(empty))
    else:
        contract = tmp_path / "managed_search/contract.yaml"
        contract.write_text(contract.read_text() + "\n# modified after approved resolver\n")

    result = validate_generated_app_bundle(GeneratedAppValidationRequest(
        files={"app.json": '{"appId":"target"}'}, capability_packs=packs,
        require_deployment_artifacts=False, approved_compilation=approval,
    ))
    assert result.passed is False
    assert result.resolved_capability_packs == []
    assert [diagnostic.code for diagnostic in result.diagnostics] == ["approved_compilation_invalid"]
    assert ("no longer available" if change == "registry" else "changed") in result.diagnostics[0].message
    candidate = await validate_generated_app_candidate(
        files={"app.json": '{"appId":"target"}'}, app_id="target", capability_packs=packs,
        approved_compilation=approval, validation_strategy="docker",
    )
    assert candidate["validation_status"] == "failed"
    assert candidate["app_bundle_acceptance_result"]["passed"] is False
    assert candidate["app_bundle_acceptance_result"]["status"] == "failed"
    assert ("no longer available" if change == "registry" else "changed") in candidate["errors"][0]
    assert candidate["app_validation_result"]["validation_status"] == "pending"


@pytest.mark.asyncio
@pytest.mark.parametrize("unexpected", [False, True])
async def test_candidate_does_not_mislabel_native_errors_as_pack_drift(saved, monkeypatch, unexpected):  # noqa: F811 - imported pytest fixture
    from factory_app.workflows.AppGenerator.tools import app_validation

    approval = await resolve(saved)
    error = RuntimeError("unexpected validator failure") if unexpected else ValueError("invalid binary asset input")
    monkeypatch.setattr(app_validation, "run_app_bundle_acceptance_gate", AsyncMock(side_effect=error))
    kwargs = {
        "files": {"app.json": '{"appId":"target"}'}, "app_id": "target",
        "capability_packs": saved[2].commit_metadata.metadata["capability_packs"],
        "approved_compilation": approval, "validation_strategy": "docker",
    }
    if unexpected:
        with pytest.raises(RuntimeError, match="unexpected validator failure"):
            await validate_generated_app_candidate(**kwargs)
    else:
        result = await validate_generated_app_candidate(**kwargs)
        assert result["validation_status"] == "failed"
        assert result["app_bundle_acceptance_result"]["failed_tests"] == [{
            "gate": "acceptance", "test": "deterministic_inputs", "error": str(error),
        }]
