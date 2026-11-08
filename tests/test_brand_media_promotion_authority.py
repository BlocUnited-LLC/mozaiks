"""The model-invoked brand tool uses immutable runtime scope before publication."""
from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import yaml

from factory_app.workflows.BrandAssetGeneratorWorkflow.tools.promote_brand_asset import (
    promote_brand_asset,
)
from mozaiksai.core.media.types import AssetVisibility
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge, _wrap_tool_with_context
from mozaiksai.core.workflow.context.authority import build_context_authority_policy
from mozaiksai.core.workflow.context.schema import load_context_variables_config
from tests.test_media_publication_authority import publication_case as publication_case


@pytest.fixture
def brand_call(monkeypatch, tmp_path, publication_case):
    monkeypatch.setitem(promote_brand_asset.__globals__, "get_media_asset_store", lambda: publication_case.store)
    workspace = tmp_path / "brand-workspace"
    monkeypatch.setenv("MOZAIKS_APP_WORKSPACE_PATH", str(workspace))
    workflow = "BrandAssetGeneratorWorkflow"
    path = Path(__file__).resolve().parents[1] / "factory_app/workflows" / workflow / "context_variables.yaml"
    definitions = load_context_variables_config(yaml.safe_load(path.read_text(encoding="utf-8"))).definitions
    policy = build_context_authority_policy(workflow_name=workflow, definitions=definitions)
    # Neither mutable context identifiers nor cached promotion entries are authority.
    context = ContextVariablesBridge({
        "app_id": "forged-app", "chat_id": "forged-chat", "user_id": "owner",
        "promoted_brand_assets": [],
    }, authority_policy=policy)
    context._bind_run((workflow, "app-1", "source-chat"), policy)
    return _wrap_tool_with_context(promote_brand_asset, context), context, workspace


@pytest.mark.asyncio
async def test_brand_tool_requires_active_runtime_invocation(publication_case, brand_call):
    case = publication_case
    _, _, workspace = brand_call
    result = await promote_brand_asset(case.asset.asset_id, "logo", {
        "app_id": "app-1", "chat_id": "source-chat", "user_id": "owner",
        "promoted_brand_assets": [{"asset_id": case.asset.asset_id, "role": "logo"}],
    })
    assert result["ok"] is False
    assert not workspace.exists()
    assert case.collection.docs[0]["asset_visibility"] == AssetVisibility.PRIVATE


@pytest.mark.asyncio
async def test_brand_tool_promotes_its_stored_source_with_runtime_actor(monkeypatch, publication_case, brand_call):
    case = publication_case
    invoke, context, workspace = brand_call
    promote = AsyncMock(wraps=case.store.mark_asset_promoted)
    monkeypatch.setattr(case.store, "mark_asset_promoted", promote)
    result = await invoke(case.asset.asset_id, "logo", alt_text="Our logo")
    assert result["ok"] is True, result
    promotion_arguments = dict(promote.await_args.kwargs)
    assert callable(promotion_arguments.pop("scope_guard"))
    assert promotion_arguments == {
        "app_id": "app-1", "asset_id": case.asset.asset_id, "promotion_target": "brand_asset",
        "promoted_by": "owner", "expected_source_chat_id": "source-chat",
    }
    manifest = json.loads((workspace / "config/asset_manifest.json").read_text(encoding="utf-8"))
    assert manifest["assets"]["logo"]["asset_id"] == case.asset.asset_id
    assert manifest["assets"]["logo"]["alt_text"] == "Our logo"
    assert len(context.get("promoted_brand_assets")) == 1
    assert case.collection.docs[0]["asset_visibility"] == AssetVisibility.PUBLIC
    assert (await invoke(case.asset.asset_id, "logo", alt_text="Our logo"))["ok"] is True
    assert len(context.get("promoted_brand_assets")) == 1


@pytest.mark.parametrize("fault", ["foreign-app", "foreign-chat", "missing-chat", "missing-asset"])
@pytest.mark.asyncio
async def test_brand_tool_refuses_unowned_assets_before_any_manifest_write(monkeypatch, publication_case, brand_call, fault):
    case = publication_case
    invoke, _, workspace = brand_call
    if fault == "missing-asset":
        case.collection.docs.clear()
    else:
        field = "app_id" if fault == "foreign-app" else "source_chat_id"
        case.collection.docs[0][field] = None if fault == "missing-chat" else "another-owner"
    before = deepcopy(case.collection.docs)
    promote = AsyncMock(wraps=case.store.mark_asset_promoted)
    monkeypatch.setattr(case.store, "mark_asset_promoted", promote)
    result = await invoke(case.asset.asset_id, "logo")
    assert result["ok"] is False
    assert not workspace.exists()
    assert case.collection.docs == before
    promote.assert_not_awaited()


@pytest.mark.parametrize("failure", [None, RuntimeError("storage unavailable")])
@pytest.mark.asyncio
async def test_brand_tool_does_not_write_manifest_or_report_success_if_promotion_fails(monkeypatch, publication_case, brand_call, failure):
    case = publication_case
    invoke, context, workspace = brand_call
    promote = AsyncMock(return_value=None) if failure is None else AsyncMock(side_effect=failure)
    monkeypatch.setattr(case.store, "mark_asset_promoted", promote)
    result = await invoke(case.asset.asset_id, "logo")
    assert result["ok"] is False
    assert not workspace.exists()
    assert list(context.get("promoted_brand_assets")) == []
    assert case.collection.docs[0]["asset_visibility"] == AssetVisibility.PRIVATE


@pytest.mark.asyncio
async def test_brand_tool_source_check_is_atomic_with_publication(monkeypatch, publication_case, brand_call):
    case = publication_case
    invoke, _, workspace = brand_call
    read = case.store.get_asset

    async def change_source_after_read(**kwargs):
        asset = await read(**kwargs)
        case.collection.docs[0]["source_chat_id"] = "another-chat"
        return asset

    monkeypatch.setattr(case.store, "get_asset", change_source_after_read)
    result = await invoke(case.asset.asset_id, "logo")
    assert result["ok"] is False
    assert not workspace.exists()
    assert case.collection.docs[0]["asset_visibility"] == AssetVisibility.PRIVATE


@pytest.mark.asyncio
async def test_manifest_failure_reports_the_completed_publication(monkeypatch, publication_case, brand_call):
    case = publication_case
    invoke, context, workspace = brand_call
    write = Path.write_text

    def fail_manifest(path, *args, **kwargs):
        if path.name == "asset_manifest.json":
            raise OSError("read-only workspace")
        return write(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_manifest)
    result = await invoke(case.asset.asset_id, "logo")
    assert result["ok"] is False
    assert "published" in result["error"]
    assert not (workspace / "config/asset_manifest.json").exists()
    assert list(context.get("promoted_brand_assets")) == []
    assert case.collection.docs[0]["asset_visibility"] == AssetVisibility.PUBLIC


@pytest.mark.parametrize("boundary", ["collection", "write", "readback"])
@pytest.mark.asyncio
async def test_revoked_child_cannot_confirm_publication(monkeypatch, publication_case, brand_call, boundary):
    case = publication_case
    _, context, workspace = brand_call
    entered, resume = asyncio.Event(), asyncio.Event()
    collection = case.store._coll
    calls = 0

    async def paused_collection():
        nonlocal calls
        calls += 1
        if (boundary == "collection" and calls == 2) or (boundary == "readback" and calls == 3):
            entered.set()
            await resume.wait()
        return await collection()

    monkeypatch.setattr(case.store, "_coll", paused_collection)
    original_update = case.collection.update_one

    async def paused_update(*args, **kwargs):
        result = await original_update(*args, **kwargs)
        if boundary == "write":
            entered.set()
            await resume.wait()
        return result

    update = AsyncMock(wraps=paused_update)
    monkeypatch.setattr(case.collection, "update_one", update)

    async def launch(context_variables=None):
        child = asyncio.create_task(promote_brand_asset(case.asset.asset_id, "logo", context_variables))
        await asyncio.wait_for(entered.wait(), timeout=2)
        return child

    child = await _wrap_tool_with_context(launch, context)()
    resume.set()
    result = await asyncio.wait_for(child, timeout=2)
    assert result["ok"] is False
    if boundary == "collection":
        update.assert_not_awaited()
        assert case.collection.docs[0]["asset_visibility"] == AssetVisibility.PRIVATE
    else:
        # An already-issued write cannot be recalled; the expired caller cannot confirm it.
        update.assert_awaited_once()
        assert case.collection.docs[0]["asset_visibility"] == AssetVisibility.PUBLIC
    assert not workspace.exists()
    assert list(context.get("promoted_brand_assets")) == []
