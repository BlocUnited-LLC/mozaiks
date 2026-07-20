"""
Pure Pydantic validator and static helper tests for:
  mozaiksai/core/runtime/app/module_loader.py

Covers helpers not tested in test_module_loader_helpers.py:

  ModuleEvent._canonical_event_namespace:
    - "domain.*" prefix → accepted
    - "platform.*" prefix → accepted
    - "hosted.*" prefix → accepted
    - "mozaikspay.*" prefix -> accepted
    - other prefix → ValidationError

  ModuleEventsManifest._validate_unique_events:
    - unique event types → valid
    - duplicate event types → ValidationError

  ModuleReactionTarget._validate_target_contract:
    - kind="handler" with handler_method → valid
    - kind="handler" missing handler_method → ValidationError
    - kind="handler" with capability_id → ValidationError
    - kind="capability" with capability_id → valid
    - kind="capability" missing capability_id → ValidationError
    - kind="notification" with notification_id → valid
    - kind="notification" missing notification_id → ValidationError
    - kind="service_adapter" with adapter + adapter_method -> valid
    - kind="service_adapter" missing adapter or adapter_method -> ValidationError

  ModuleRuntimeExtension._entrypoint (validator):
    - valid "backend.router:get_router" → accepted
    - no colon → ValidationError
    - empty module path → ValidationError
    - empty attr → ValidationError
    - starts with "modules." → ValidationError
    - starts with "app.modules." → ValidationError
    - starts with "mozaiksai." → ValidationError
    - does not start with "backend." → ValidationError
    - non-identifier attr → ValidationError

  ModuleRuntimeExtension._validate_extension_contract:
    - kind="startup_service" with prefix declared → ValidationError
    - kind="api_router" with prefix "/api" → valid
    - kind="api_router" with prefix "api" (no leading slash) → ValidationError
    - kind="api_router" with prefix None → valid

  ModuleLoader._is_known_or_canonical_event (static):
    - event in declared_events set → True
    - "domain.*" not in set → True
    - "platform.*" not in set → True
    - "hosted.*" not in set → True
    - "mozaikspay.*" not in set -> True
    - unknown event not in set → False

  ModuleLoader._display_path (static):
    - path with at least 3 parents → relative from parents[2]
    - path with fewer parents → full string returned
"""
from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from mozaiksai.core.runtime.app.module_loader import (
    ModuleEvent,
    ModuleEventsManifest,
    ModuleLoader,
    ModuleReaction,
    ModuleReactionTarget,
    ModuleRuntimeExtension,
)

# ---------------------------------------------------------------------------
# 1. ModuleEvent._canonical_event_namespace
# ---------------------------------------------------------------------------

def _event(event_type: str) -> ModuleEvent:
    return ModuleEvent(
        type=event_type,
        version=1,
        producer="my_module",
    )


class TestModuleEventCanonicalNamespace:
    def test_domain_prefix_accepted(self):
        e = _event("domain.my_module.created")
        assert e.type == "domain.my_module.created"

    def test_platform_prefix_accepted(self):
        e = _event("platform.auth.login")
        assert e.type == "platform.auth.login"

    def test_hosted_prefix_accepted(self):
        e = _event("hosted.billing.payment_received")
        assert e.type == "hosted.billing.payment_received"

    def test_mozaikspay_prefix_accepted(self):
        e = _event("mozaikspay.self_hosted_app_registered")
        assert e.type == "mozaikspay.self_hosted_app_registered"

    def test_other_prefix_raises(self):
        with pytest.raises(ValidationError, match="domain"):
            _event("app.my_module.created")

    def test_no_prefix_raises(self):
        with pytest.raises(ValidationError):
            _event("my_module.created")

    def test_empty_string_raises(self):
        with pytest.raises(ValidationError):
            _event("")

    def test_partial_prefix_raises(self):
        # "domains." is not one of the allowed prefixes
        with pytest.raises(ValidationError):
            _event("domains.my_module.created")


# ---------------------------------------------------------------------------
# 2. ModuleEventsManifest._validate_unique_events
# ---------------------------------------------------------------------------

class TestModuleEventsManifestUniqueEvents:
    def test_unique_events_valid(self):
        manifest = ModuleEventsManifest(
            schema_version="mozaiks.events.v1",
            events=[
                _event("domain.mod.created"),
                _event("domain.mod.updated"),
            ],
        )
        assert len(manifest.events) == 2

    def test_duplicate_event_types_raises(self):
        with pytest.raises(ValidationError, match="unique"):
            ModuleEventsManifest(
                schema_version="mozaiks.events.v1",
                events=[
                    _event("domain.mod.created"),
                    _event("domain.mod.created"),
                ],
            )


# ---------------------------------------------------------------------------
# 3. ModuleReactionTarget._validate_target_contract
# ---------------------------------------------------------------------------

class TestModuleReactionTargetContract:
    def test_handler_with_method_valid(self):
        target = ModuleReactionTarget(kind="handler", handler_method="on_payment_received")
        assert target.kind == "handler"
        assert target.handler_method == "on_payment_received"

    def test_handler_missing_method_raises(self):
        with pytest.raises(ValidationError, match="handler_method"):
            ModuleReactionTarget(kind="handler")

    def test_handler_with_capability_id_raises(self):
        with pytest.raises(ValidationError):
            ModuleReactionTarget(kind="handler", handler_method="on_event", capability_id="cap1")

    def test_handler_with_notification_id_raises(self):
        with pytest.raises(ValidationError):
            ModuleReactionTarget(kind="handler", handler_method="on_event", notification_id="notif1")

    def test_capability_with_id_valid(self):
        target = ModuleReactionTarget(kind="capability", capability_id="my_capability")
        assert target.kind == "capability"

    def test_capability_missing_id_raises(self):
        with pytest.raises(ValidationError, match="capability_id"):
            ModuleReactionTarget(kind="capability")

    def test_capability_with_handler_method_raises(self):
        with pytest.raises(ValidationError):
            ModuleReactionTarget(kind="capability", capability_id="cap1", handler_method="on_event")

    def test_notification_with_id_valid(self):
        target = ModuleReactionTarget(kind="notification", notification_id="my_notification")
        assert target.kind == "notification"

    def test_notification_missing_id_raises(self):
        with pytest.raises(ValidationError, match="notification_id"):
            ModuleReactionTarget(kind="notification")

    def test_service_adapter_with_adapter_and_method_valid(self):
        target = ModuleReactionTarget(
            kind="service_adapter",
            adapter="app.services.adapters.ci.managed_ci_provisioner:ManagedCIProvisioner",
            adapter_method="handle",
        )
        assert target.kind == "service_adapter"
        assert target.adapter_method == "handle"

    def test_service_adapter_missing_adapter_raises(self):
        with pytest.raises(ValidationError, match="adapter"):
            ModuleReactionTarget(kind="service_adapter", adapter_method="handle")

    def test_service_adapter_missing_method_raises(self):
        with pytest.raises(ValidationError, match="adapter_method"):
            ModuleReactionTarget(
                kind="service_adapter",
                adapter="app.services.adapters.ci.managed_ci_provisioner:ManagedCIProvisioner",
            )

    def test_service_adapter_with_handler_method_raises(self):
        with pytest.raises(ValidationError):
            ModuleReactionTarget(
                kind="service_adapter",
                adapter="app.services.adapters.ci.managed_ci_provisioner:ManagedCIProvisioner",
                adapter_method="handle",
                handler_method="on_event",
            )

    def test_mozaikspay_reaction_event_type_accepted(self):
        reaction = ModuleReaction(
            id="provision_self_hosted_registration",
            event_type="mozaikspay.self_hosted_app_registered",
            target=ModuleReactionTarget(kind="handler", handler_method="on_self_hosted_app_registered"),
        )
        assert reaction.event_type == "mozaikspay.self_hosted_app_registered"


# ---------------------------------------------------------------------------
# 4. ModuleRuntimeExtension._entrypoint validator
# ---------------------------------------------------------------------------

class TestModuleRuntimeExtensionEntrypoint:
    def test_valid_api_router_entrypoint(self):
        ext = ModuleRuntimeExtension(kind="api_router", entrypoint="backend.router:get_router")
        assert ext.entrypoint == "backend.router:get_router"

    def test_valid_startup_service_entrypoint(self):
        ext = ModuleRuntimeExtension(kind="startup_service", entrypoint="backend.worker:ServiceClass")
        assert ext.entrypoint == "backend.worker:ServiceClass"

    def test_no_colon_raises(self):
        with pytest.raises(ValidationError, match="syntax"):
            ModuleRuntimeExtension(kind="api_router", entrypoint="backend.router")

    def test_empty_module_path_raises(self):
        with pytest.raises(ValidationError):
            ModuleRuntimeExtension(kind="api_router", entrypoint=":get_router")

    def test_empty_attr_raises(self):
        with pytest.raises(ValidationError):
            ModuleRuntimeExtension(kind="api_router", entrypoint="backend.router:")

    def test_modules_prefix_raises(self):
        with pytest.raises(ValidationError, match="module-local"):
            ModuleRuntimeExtension(kind="api_router", entrypoint="modules.my_mod.backend.router:get_router")

    def test_app_modules_prefix_raises(self):
        with pytest.raises(ValidationError, match="module-local"):
            ModuleRuntimeExtension(kind="api_router", entrypoint="app.modules.billing.router:get_router")

    def test_mozaiksai_prefix_raises(self):
        with pytest.raises(ValidationError, match="module-local"):
            ModuleRuntimeExtension(kind="api_router", entrypoint="mozaiksai.core.router:get_router")

    def test_non_backend_prefix_raises(self):
        with pytest.raises(ValidationError, match="backend"):
            ModuleRuntimeExtension(kind="api_router", entrypoint="services.router:get_router")

    def test_non_identifier_attr_raises(self):
        with pytest.raises(ValidationError, match="identifier"):
            ModuleRuntimeExtension(kind="api_router", entrypoint="backend.router:get-router")


# ---------------------------------------------------------------------------
# 5. ModuleRuntimeExtension._validate_extension_contract
# ---------------------------------------------------------------------------

class TestModuleRuntimeExtensionContract:
    def test_startup_service_without_prefix_valid(self):
        ext = ModuleRuntimeExtension(kind="startup_service", entrypoint="backend.worker:Worker")
        assert ext.prefix is None

    def test_startup_service_with_prefix_raises(self):
        with pytest.raises(ValidationError, match="prefix"):
            ModuleRuntimeExtension(
                kind="startup_service",
                entrypoint="backend.worker:Worker",
                prefix="/api",
            )

    def test_api_router_with_valid_prefix(self):
        ext = ModuleRuntimeExtension(
            kind="api_router", entrypoint="backend.router:get_router", prefix="/api/v1"
        )
        assert ext.prefix == "/api/v1"

    def test_api_router_with_none_prefix_valid(self):
        ext = ModuleRuntimeExtension(
            kind="api_router", entrypoint="backend.router:get_router", prefix=None
        )
        assert ext.prefix is None

    def test_api_router_prefix_without_slash_raises(self):
        with pytest.raises(ValidationError, match="start with /"):
            ModuleRuntimeExtension(
                kind="api_router",
                entrypoint="backend.router:get_router",
                prefix="api/v1",
            )


# ---------------------------------------------------------------------------
# 6. ModuleLoader._is_known_or_canonical_event (static method)
# ---------------------------------------------------------------------------

class TestIsKnownOrCanonicalEvent:
    def test_event_in_declared_events_true(self):
        declared = {"domain.mod.created", "platform.auth.login"}
        assert ModuleLoader._is_known_or_canonical_event("domain.mod.created", declared) is True

    def test_domain_prefix_not_in_set_true(self):
        assert ModuleLoader._is_known_or_canonical_event("domain.other.event", set()) is True

    def test_platform_prefix_not_in_set_true(self):
        assert ModuleLoader._is_known_or_canonical_event("platform.session.start", set()) is True

    def test_hosted_prefix_not_in_set_true(self):
        assert ModuleLoader._is_known_or_canonical_event("hosted.billing.paid", set()) is True

    def test_mozaikspay_prefix_not_in_set_true(self):
        assert ModuleLoader._is_known_or_canonical_event(
            "mozaikspay.self_hosted_app_registered", set()
        ) is True

    def test_unknown_event_not_in_declared_false(self):
        assert ModuleLoader._is_known_or_canonical_event("app.custom.event", set()) is False

    def test_unknown_event_added_to_declared_true(self):
        declared = {"app.custom.event"}
        assert ModuleLoader._is_known_or_canonical_event("app.custom.event", declared) is True


# ---------------------------------------------------------------------------
# 7. ModuleLoader._display_path (static method)
# ---------------------------------------------------------------------------

class TestDisplayPath:
    def test_deep_path_relative_from_parents_2(self):
        path = Path("/a/b/c/d/e.py")
        result = ModuleLoader._display_path(path)
        # parents[2] of /a/b/c/d/e.py is /a/b
        # relative_to(/a/b) → c/d/e.py (or c\d\e.py on Windows)
        # Normalize separators for cross-platform comparison
        assert result.replace("\\", "/") == "c/d/e.py"

    def test_shallow_path_returns_full_string(self):
        # Path with fewer than 3 parents returns full path string
        path = Path("a.py")
        result = ModuleLoader._display_path(path)
        assert result == str(path)

    def test_result_is_string(self):
        path = Path("/a/b/c/module.py")
        result = ModuleLoader._display_path(path)
        assert isinstance(result, str)
