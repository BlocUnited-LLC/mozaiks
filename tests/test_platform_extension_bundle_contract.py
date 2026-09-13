from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from mozaiksai.core.runtime.composition import (
    PLATFORM_EXTENSION_SCHEMA_VERSION,
    ModuleExecutionPolicyDecision,
    PlatformExtensionBundle,
    PlatformHookRegistry,
)


def _fresh() -> PlatformHookRegistry:
    return PlatformHookRegistry()


def test_typed_platform_extension_bundle_registers_hooks() -> None:
    startup = MagicMock()
    prereqs = MagicMock(return_value=(True, None))

    reg = _fresh()
    reg._register_bundle(  # public normalization contract; registry API remains internal
        PlatformExtensionBundle(
            on_startup=startup,
            chat_prereqs=prereqs,
        )
    )

    assert reg.has_startup is True
    assert reg.has_prereqs is True


def test_legacy_dict_platform_extension_bundle_remains_supported() -> None:
    reg = _fresh()
    reg._register_bundle({"module_permission_resolver": MagicMock(return_value=[])})

    assert reg.has_module_permission_resolver is True


def test_before_module_execution_hook_registers_from_typed_bundle() -> None:
    hook = MagicMock(return_value=True)

    reg = _fresh()
    reg._register_bundle(PlatformExtensionBundle(before_module_execution=hook))

    assert reg.has_before_module_execution is True


def test_module_dispatch_audit_hook_registers_from_dict_bundle() -> None:
    hook = MagicMock()

    reg = _fresh()
    reg._register_bundle({"module_dispatch_audit": hook})

    assert reg.has_module_dispatch_audit is True


def test_module_reaction_audit_hook_registers_from_typed_bundle() -> None:
    hook = MagicMock()

    reg = _fresh()
    reg._register_bundle(PlatformExtensionBundle(module_reaction_audit=hook))

    assert reg.has_module_reaction_audit is True


@pytest.mark.asyncio
async def test_empty_registry_allows_module_execution_policy_by_default() -> None:
    reg = _fresh()

    decision = await reg.call_before_module_execution(object())

    assert isinstance(decision, ModuleExecutionPolicyDecision)
    assert decision.allowed is True


def test_schema_version_is_public_and_versioned() -> None:
    assert PLATFORM_EXTENSION_SCHEMA_VERSION == "mozaiks.platform_extensions.v1"
    assert PlatformExtensionBundle().schema_version == PLATFORM_EXTENSION_SCHEMA_VERSION


def test_invalid_platform_extension_schema_version_is_rejected() -> None:
    reg = _fresh()

    with pytest.raises(ValueError, match="Unsupported platform extension schema_version"):
        reg._register_bundle({"schema_version": "mozaiks.platform_extensions.v999"})


def test_scalar_platform_extension_bundle_is_rejected() -> None:
    reg = _fresh()

    with pytest.raises(TypeError, match="Platform extension bundle"):
        reg._register_bundle("not-a-bundle")


def test_ask_context_hook_registers_from_dict_and_typed_bundle() -> None:
    hook = MagicMock(return_value={"Workspace apps": "2 total"})

    reg = _fresh()
    reg._register_bundle({"ask_context": hook})
    assert reg.has_ask_context is True

    typed = _fresh()
    typed._register_bundle(PlatformExtensionBundle(ask_context=hook))
    assert typed.has_ask_context is True


@pytest.mark.asyncio
async def test_call_ask_context_merges_first_wins_and_fails_open() -> None:
    async def first_hook(*, app_id: str, user_id: str):
        _ = app_id, user_id
        return {"Workspace apps": "2 total", "Shared": "from-first"}

    def failing_hook(*, app_id: str, user_id: str):
        raise RuntimeError("registry offline")

    def second_hook(*, app_id: str, user_id: str):
        _ = app_id, user_id
        return {"Shared": "from-second", "Extra": "value"}

    reg = _fresh()
    reg._register_bundle({"ask_context": first_hook})
    reg._register_bundle({"ask_context": failing_hook})
    reg._register_bundle({"ask_context": second_hook})

    merged = await reg.call_ask_context(app_id="app_1", user_id="user_1")

    assert merged == {
        "Workspace apps": "2 total",
        "Shared": "from-first",
        "Extra": "value",
    }


@pytest.mark.asyncio
async def test_call_ask_context_without_hooks_returns_empty_dict() -> None:
    reg = _fresh()
    assert await reg.call_ask_context(app_id="app_1", user_id="user_1") == {}
