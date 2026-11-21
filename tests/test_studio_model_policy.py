from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from mozaiksai.core.auth.dependencies import UserPrincipal
from mozaiksai.hosts import studio_model_policy as policy


def _principal(workspace_id: str = "workspace-a") -> UserPrincipal:
    return UserPrincipal(
        user_id="owner",
        email=None,
        name=None,
        roles=["admin"],
        scopes=[],
        raw_claims={},
        tenant_id="tenant-a",
        workspace_id=workspace_id,
        auth_provenance="token_validated",
    )


@pytest.mark.parametrize("configured", [{"gemini", "openai"}, {"gemini"}, {"openai"}])
@pytest.mark.asyncio
async def test_picker_lists_only_configured_providers(monkeypatch, configured):
    monkeypatch.setattr(policy, "_load_policy", AsyncMock(return_value=(policy.StudioModelPolicy(), False)))
    monkeypatch.setattr(policy, "_configured_models", lambda: configured)
    result = await policy.studio_model_options(_principal())
    assert {model["id"] for model in result["models"]} == configured
    expected = "gemini" if "gemini" in configured else "openai"
    assert result["default"] == expected
    assert await policy.select_studio_model(_principal(), None) == expected


@pytest.mark.asyncio
async def test_disabled_model_cannot_be_selected(monkeypatch):
    configured = {"gemini", "openai"}
    monkeypatch.setattr(policy, "_configured_models", lambda: configured)
    monkeypatch.setattr(
        policy, "_load_policy", AsyncMock(return_value=(policy.StudioModelPolicy(allowed=["gemini"]), True)),
    )
    with pytest.raises(HTTPException) as error:
        await policy.select_studio_model(_principal(), "openai")
    assert error.value.status_code == 422


@pytest.mark.asyncio
async def test_admin_save_is_scoped_and_contains_no_provider_keys(monkeypatch):
    monkeypatch.setattr(policy, "_configured_models", lambda: {"openai"})
    collection = AsyncMock()
    monkeypatch.setattr(policy, "_collection", lambda: collection)
    selection = policy.StudioModelPolicy(default="openai", allowed=["openai"])
    await policy.save_studio_model_policy(_principal(), selection)
    scope = {"tenant_id": "tenant-a", "workspace_id": "workspace-a"}
    collection.replace_one.assert_awaited_once_with(
        {"_id": scope}, {"_id": scope, "default": "openai", "allowed": ["openai"]}, upsert=True,
    )
    collection.find_one.return_value = selection.model_dump()
    assert await policy._load_policy(_principal("workspace-b")) == (selection, True)
    collection.find_one.assert_awaited_once_with(
        {"_id": {"tenant_id": "tenant-a", "workspace_id": "workspace-b"}}, {"_id": 0},
    )


@pytest.mark.parametrize("allowed", [[], ["openai"], ["gemini", "gemini"]])
def test_policy_rejects_disabled_default_or_duplicates(allowed):
    with pytest.raises(ValidationError):
        policy.StudioModelPolicy(allowed=allowed)


@pytest.mark.asyncio
async def test_admin_surface_rejects_dev_override_and_non_admin():
    from mozaiksai.hosts.studio import require_studio_model_admin

    principal = _principal()
    assert await require_studio_model_admin(principal) is principal
    principal.auth_provenance = "dev_override"
    with pytest.raises(HTTPException) as error:
        await require_studio_model_admin(principal)
    assert error.value.status_code == 403
    principal.auth_provenance = "token_validated"
    principal.roles = ["member"]
    with pytest.raises(HTTPException) as error:
        await require_studio_model_admin(principal)
    assert error.value.status_code == 403


@pytest.mark.asyncio
async def test_saved_policy_fails_closed_after_provider_key_removal(monkeypatch):
    monkeypatch.setattr(policy, "_configured_models", lambda: {"openai"})
    saved = policy.StudioModelPolicy(allowed=["gemini"])
    monkeypatch.setattr(policy, "_load_policy", AsyncMock(return_value=(saved, True)))
    with pytest.raises(HTTPException) as error:
        await policy.select_studio_model(_principal(), None, require_available=False)
    assert error.value.status_code == 422


def test_unbound_owner_policies_are_isolated():
    first = _principal()
    first.workspace_id = None
    second = _principal()
    second.workspace_id = None
    second.user_id = "other-owner"
    assert policy._scope_id(first) != policy._scope_id(second)


def test_generic_key_is_available_only_for_the_configured_primary_provider(monkeypatch):
    from mozaiksai.core.adapters import llm_fallback

    monkeypatch.setenv("LLM_PRIMARY_API_TYPE", "openai")
    monkeypatch.setattr(
        llm_fallback, "_optional_secret",
        lambda name: "test-primary-key" if name == "LLM_PRIMARY_API_KEY" else "",
    )
    assert llm_fallback.resolve_configured_provider_key("openai") == "test-primary-key"
    assert not llm_fallback.resolve_configured_provider_key("google")
