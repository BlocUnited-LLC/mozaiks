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
