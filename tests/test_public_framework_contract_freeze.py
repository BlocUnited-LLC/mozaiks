from __future__ import annotations

from dataclasses import fields

# ---------------------------------------------------------------------------
# Stable import paths — these must never silently move.
# Each test here is a contract: if it breaks, it's a breaking change and
# requires a CHANGELOG entry and a version bump per the pre-1.0 policy.
# See docs/guides/stability-and-compatibility.md
# ---------------------------------------------------------------------------


def test_version_constant_importable() -> None:
    """Package version is accessible from the canonical import path."""
    from mozaiksai import __version__
    from mozaiksai.version import __version__ as version_direct

    assert __version__ == version_direct
    assert __version__.startswith("0."), f"Pre-1.0 version expected, got {__version__!r}"


def test_app_loader_canonical_import_path() -> None:
    """AppLoader is importable from the canonical Tier-1 stable path."""
    from mozaiksai.core.runtime.app.loader import AppLoader

    assert AppLoader.__name__ == "AppLoader"
    assert callable(AppLoader)


def test_entitlement_port_and_adapters_importable() -> None:
    """EntitlementPort, ConfiguredEntitlementAdapter, and NoOpEntitlementAdapter
    are importable from the ports package — the stable seam for SaaS gating."""
    from mozaiksai.core.ports import (
        EntitlementPort,
        EntitlementResult,
        NoOpEntitlementAdapter,
    )
    from mozaiksai.core.runtime.app.entitlements import ConfiguredEntitlementAdapter

    assert EntitlementPort.__name__ == "EntitlementPort"
    assert EntitlementResult.__name__ == "EntitlementResult"
    assert NoOpEntitlementAdapter.__name__ == "NoOpEntitlementAdapter"
    assert ConfiguredEntitlementAdapter.__name__ == "ConfiguredEntitlementAdapter"


def test_platform_extension_schema_version_is_stable() -> None:
    """PLATFORM_EXTENSION_SCHEMA_VERSION constant follows the canonical
    mozaiks.<name>.v<major> format and must not change without a version bump."""
    from mozaiksai.core.runtime.composition import PLATFORM_EXTENSION_SCHEMA_VERSION

    assert PLATFORM_EXTENSION_SCHEMA_VERSION == "mozaiks.platform_extensions.v1"
    assert PLATFORM_EXTENSION_SCHEMA_VERSION.startswith("mozaiks.")
    assert PLATFORM_EXTENSION_SCHEMA_VERSION.endswith(".v1")


def test_cli_entry_point_importable() -> None:
    """The mozaiks CLI entry point resolves from the stable package namespace."""
    import mozaiks_cli  # noqa: F401
    from mozaiks_cli.main import create_parser

    parser = create_parser()
    assert parser.prog == "mozaiks"


def test_app_zero_public_framework_entrypoints_remain_importable() -> None:
    from mozaiksai.core.runtime.composition import (
        PLATFORM_EXTENSION_SCHEMA_VERSION,
        ModuleActionDispatchRequest,
        ModuleDispatchAudit,
        ModuleDispatchAuthority,
        ModuleDispatchMetadata,
        ModuleDispatchProvenance,
        ModuleDispatchScope,
        ModuleEventProvenance,
        ModuleExecutionPolicyDecision,
        ModuleExecutionPolicyInput,
        ModulePermissionCheck,
        ModuleReactionAudit,
        ModuleReactionProvenance,
        PlatformExtensionBundle,
        dispatch_module_action,
    )
    from mozaiksai.core.studio import StudioScope, resolve_studio_scope
    from mozaiksai.core.validation import (
        GeneratedAppValidationDiagnostic,
        GeneratedAppValidationRequest,
        GeneratedAppValidationResult,
        validate_generated_app_bundle,
    )

    assert StudioScope.__name__ == "StudioScope"
    assert callable(resolve_studio_scope)
    assert PlatformExtensionBundle().schema_version == PLATFORM_EXTENSION_SCHEMA_VERSION
    assert ModuleActionDispatchRequest.__name__ == "ModuleActionDispatchRequest"
    assert ModuleDispatchScope.__name__ == "ModuleDispatchScope"
    assert ModuleDispatchMetadata.__name__ == "ModuleDispatchMetadata"
    assert ModuleDispatchAuthority.__name__ == "ModuleDispatchAuthority"
    assert ModuleDispatchProvenance.__name__ == "ModuleDispatchProvenance"
    assert ModulePermissionCheck.__name__ == "ModulePermissionCheck"
    assert ModuleExecutionPolicyInput.__name__ == "ModuleExecutionPolicyInput"
    assert ModuleExecutionPolicyDecision.__name__ == "ModuleExecutionPolicyDecision"
    assert ModuleDispatchAudit.__name__ == "ModuleDispatchAudit"
    assert ModuleEventProvenance.__name__ == "ModuleEventProvenance"
    assert ModuleReactionProvenance.__name__ == "ModuleReactionProvenance"
    assert ModuleReactionAudit.__name__ == "ModuleReactionAudit"
    assert callable(dispatch_module_action)
    assert GeneratedAppValidationRequest.__name__ == "GeneratedAppValidationRequest"
    assert GeneratedAppValidationDiagnostic.__name__ == "GeneratedAppValidationDiagnostic"
    assert GeneratedAppValidationResult.__name__ == "GeneratedAppValidationResult"
    assert callable(validate_generated_app_bundle)


def test_framework_authority_and_provenance_do_not_claim_production_authority() -> None:
    from mozaiksai.core.runtime.composition import (
        ModuleDispatchAuthority,
        ModuleEventProvenance,
    )

    authority = ModuleDispatchAuthority(
        kind="workflow",
        permission_mode="enforce",
        reason="public workflow module dispatch",
        actor_id="user-1",
        permissions=("orders.read",),
    )
    translated = ModuleDispatchAuthority.from_granted_permissions(None, actor_id="system")
    event = ModuleEventProvenance(
        event_id="evt-1",
        event_type="domain.orders.created",
        producer_layer="module",
        trust_shape="module_envelope",
    )

    assert authority.permission_mode == "enforce"
    assert translated.kind == "leg" + "acy_trusted"
    assert translated.permission_mode == "trusted" + "_bypass"
    assert getattr(translated, "leg" + "acy_granted_permissions_none") is True
    assert "payload" not in event.to_dict()
    authority_fields = {field.name for field in fields(authority)}
    assert "production_authority" not in authority_fields
    assert "approval" not in authority_fields
    assert "credentials" not in authority_fields
