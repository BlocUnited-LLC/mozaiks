"""Studio workspace model policy and launch-time model selection."""

from __future__ import annotations

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pymongo.errors import PyMongoError

from mozaiksai.control_plane.config import load_control_plane_config
from mozaiksai.core.adapters.llm_fallback import resolve_configured_provider_key
from mozaiksai.core.auth.dependencies import UserPrincipal
from mozaiksai.core.core_config import get_mongo_client
from mozaiksai.core.data.persistence.namespaces import SYSTEM_DATABASE, PlatformCollections
from mozaiksai.core.secrets import SecretResolutionError
from mozaiksai.core.session.build_binding import RefinementModel, refinement_model_config

_MODEL_LABELS: dict[RefinementModel, str] = {
    "gemini": "Gemini 3.8 Flash",
    "openai": "GPT-5 Nano",
}


class StudioModelPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default: RefinementModel = "gemini"
    allowed: list[RefinementModel] = Field(default_factory=lambda: ["gemini", "openai"])

    @model_validator(mode="after")
    def _validate_default(self) -> StudioModelPolicy:
        if len(set(self.allowed)) != len(self.allowed) or self.default not in self.allowed:
            raise ValueError("Enable the default model and list each allowed model once.")
        return self


def _scope_id(principal: UserPrincipal) -> dict[str, str]:
    scope = {"tenant_id": principal.tenant_id or ""}
    if principal.is_authenticated and principal.tenant_id and principal.workspace_id:
        scope["workspace_id"] = principal.workspace_id
    else:
        # OSS Studio's unbound workspaces are portfolios owned by one user.
        scope["owner_user_id"] = principal.user_id
    return scope


def _collection():
    return get_mongo_client()[SYSTEM_DATABASE][PlatformCollections.STUDIO_MODEL_POLICIES]


def _configured_models() -> set[RefinementModel]:
    configured = set()
    for model in _MODEL_LABELS:
        provider = refinement_model_config(model)["api_type"]
        try:
            if resolve_configured_provider_key(provider):
                configured.add(model)
        except SecretResolutionError:
            continue
    return configured


async def _load_policy(principal: UserPrincipal) -> tuple[StudioModelPolicy, bool]:
    try:
        document = await _collection().find_one({"_id": _scope_id(principal)}, {"_id": 0})
    except SecretResolutionError as exc:
        if exc.source != "missing":
            raise HTTPException(503, "Workspace model settings are unavailable.") from exc
        document = None
    except PyMongoError as exc:
        raise HTTPException(503, "Workspace model settings are unavailable.") from exc
    if document is not None:
        return StudioModelPolicy.model_validate(document), True
    return StudioModelPolicy(default=load_control_plane_config().model_override or "gemini"), False


def _available_models(policy: StudioModelPolicy, configured: set[RefinementModel]) -> list[RefinementModel]:
    return [model for model in policy.allowed if model in configured]


def _effective_default(policy: StudioModelPolicy, available: list[RefinementModel]) -> RefinementModel | None:
    return policy.default if policy.default in available else next(iter(available), None)


async def studio_model_options(principal: UserPrincipal, *, admin: bool = False) -> dict:
    policy, _ = await _load_policy(principal)
    configured = _configured_models()
    available = _available_models(policy, configured)
    default = _effective_default(policy, available)
    if admin:
        return {
            "default": policy.default,
            "allowed": policy.allowed,
            "models": [
                {"id": model, "label": label, "configured": model in configured}
                for model, label in _MODEL_LABELS.items()
            ],
        }
    return {"default": default, "models": [{"id": model, "label": _MODEL_LABELS[model]} for model in available]}


async def select_studio_model(
    principal: UserPrincipal, requested: RefinementModel | None, *, require_available: bool = True,
) -> RefinementModel | None:
    policy, saved = await _load_policy(principal)
    available = _available_models(policy, _configured_models())
    if requested is not None:
        if requested not in available:
            raise HTTPException(422, "The selected model is disabled or lacks a configured provider key.")
        return requested
    default = _effective_default(policy, available)
    if default is None and (require_available or saved):
        raise HTTPException(422, "Configure an allowed model provider key before starting a build.")
    return default


async def save_studio_model_policy(principal: UserPrincipal, policy: StudioModelPolicy) -> None:
    if policy.default not in _configured_models():
        raise HTTPException(422, "Configure the default model provider key before saving.")
    scope = _scope_id(principal)
    try:
        await _collection().replace_one(
            {"_id": scope}, {"_id": scope, **policy.model_dump()}, upsert=True,
        )
    except (SecretResolutionError, PyMongoError) as exc:
        raise HTTPException(503, "Workspace model settings are unavailable.") from exc
