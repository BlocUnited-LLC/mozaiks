from __future__ import annotations

from dataclasses import fields


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
    legacy = ModuleDispatchAuthority.from_granted_permissions(None, actor_id="system")
    event = ModuleEventProvenance(
        event_id="evt-1",
        event_type="domain.orders.created",
        producer_layer="module",
        trust_shape="module_envelope",
    )

    assert authority.permission_mode == "enforce"
    assert legacy.kind == "legacy_trusted"
    assert legacy.permission_mode == "trusted_bypass"
    assert legacy.legacy_granted_permissions_none is True
    assert "payload" not in event.to_dict()
    authority_fields = {field.name for field in fields(authority)}
    assert "production_authority" not in authority_fields
    assert "approval" not in authority_fields
    assert "credentials" not in authority_fields
