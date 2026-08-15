"""
AppGenerator app_build_plan.py pack/facade/context helper unit tests.

Covers helpers not in test_app_build_plan_helpers.py or test_app_build_plan_helpers_extended.py:

  _context_get (app_build_plan version):
    - None context_variables → default
    - object without .get() → default
    - dict with key → value
    - dict without key → default
    - .get() raises → default

  _context_available_pack_map:
    - None → {}
    - capability_packs list → mapped by pack_id
    - available_managed_capabilities list → merged into map
    - duplicate pack_ids → merged (later values win for non-existing keys)
    - packs with id / pack_id / capability_pack_id fields → all accepted
    - non-dict packs → skipped

  _context_managed_capability_ids:
    - None → empty frozenset
    - packs without capability_source → not included
    - packs with capability_source="managed_capability" → included
    - mixed packs → only hosted included

  _pack_id_from_descriptor:
    - capability_pack_id → returned
    - fallback to id → returned
    - fallback to pack_id → returned
    - all empty → ""

  _pack_facades:
    - top-level "facades" key → returned as normalized list
    - no "facades" key, nested "pack.facades" → returned
    - neither key → []
    - non-dict pack section → []

  _facade_pack_descriptor:
    - missing module_id and facade_id → None
    - valid facade with pages → descriptor built
    - pages with primary_actions → operations populated
    - pages with primary_entities → primary_entities populated
    - label/summary from facade → used
    - label/summary missing → auto-generated from facade_id
    - provider_module → included in required_integrations

  _iter_page_api_endpoints:
    - flat dict with api_endpoint → yields it
    - nested dict → yields from nested
    - list of dicts → yields from all
    - non-string api_endpoint → skipped
    - empty endpoint → skipped
    - no api_endpoint keys → yields nothing

  _validate_page_bindings:
    - no forbidden ids → no error
    - page binds to allowed module → no error
    - page binds to managed capability module → raises ValueError
    - page binds to backing module → raises ValueError
    - nested endpoint binding → raises ValueError

  _managed_capability_backing_module_ids:
    - no managed capabilities → empty frozenset
    - managed capability with backing_module → included
    - managed capability with requires_modules → included
    - managed capability with capabilities[].module → included
    - managed capability's own id excluded from result
    - pack not in context available_packs → nothing added

  _selected_managed_capability_descriptors:
    - non-managed capabilities excluded
    - managed capability in available_packs → descriptor from available_packs
    - managed capability not in available_packs → descriptor from pack itself

  _managed_facade_route_rules:
    - no managed capabilities → empty dict
    - pack with facade + provider_actions → rules built
    - facade missing provider_module → pack_id used as fallback
    - facade missing facade_id → rule skipped
    - empty provider_actions → no rules

  _validate_user_facing_managed_capability_tasks:
    - user-facing managed capability without page_bundle → raises ValueError
    - user-facing managed capability without facade module_contract → raises ValueError
    - backend-only managed capability → no error

"""
from __future__ import annotations

import pytest

from factory_app.workflows.AppGenerator.tools.app_build_plan import (
    _apply_selected_pack_files,
    _context_available_pack_map,
    _context_get,
    _context_managed_capability_ids,
    _facade_pack_descriptor,
    _iter_page_api_endpoints,
    _managed_capability_backing_module_ids,
    _managed_facade_route_rules,
    _merge_available_pack_defaults,
    _pack_facades,
    _pack_id_from_descriptor,
    _selected_managed_capability_descriptors,
    _validate_monetization_provider_selection,
    _validate_page_bindings,
    _validate_user_facing_managed_capability_tasks,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ctx(data: dict):
    """Return a simple dict-based context_variables."""
    return data


# ---------------------------------------------------------------------------
# 1. _context_get (app_build_plan version)
# ---------------------------------------------------------------------------

class TestContextGetAppBuildPlan:
    def test_none_returns_default(self):
        assert _context_get(None, "key", "fallback") == "fallback"

    def test_none_default_is_none(self):
        assert _context_get(None, "key") is None

    def test_object_without_get_returns_default(self):
        class NoGet:
            pass
        assert _context_get(NoGet(), "key", "def") == "def"

    def test_dict_key_found(self):
        assert _context_get({"key": "value"}, "key") == "value"

    def test_dict_key_missing_returns_default(self):
        assert _context_get({"other": "val"}, "key", "def") == "def"

    def test_get_raises_returns_default(self):
        class BadGet:
            def get(self, k, d=None):
                raise RuntimeError("boom")
        assert _context_get(BadGet(), "key", "safe") == "safe"


# ---------------------------------------------------------------------------
# 2. _context_available_pack_map
# ---------------------------------------------------------------------------

class TestContextAvailablePackMap:
    def test_none_context_returns_empty(self):
        assert _context_available_pack_map(None) == {}

    def test_capability_packs_mapped_by_pack_id(self):
        ctx = _ctx({"capability_packs": [{"capability_pack_id": "wallet", "label": "Wallet"}]})
        result = _context_available_pack_map(ctx)
        assert "wallet" in result
        assert result["wallet"]["label"] == "Wallet"

    def test_available_managed_capabilities_merged(self):
        ctx = _ctx({
            "capability_packs": [],
            "available_managed_capabilities": [{"id": "billing", "label": "Billing"}],
        })
        result = _context_available_pack_map(ctx)
        assert "billing" in result

    def test_id_field_accepted(self):
        ctx = _ctx({"capability_packs": [{"id": "payment_provider", "label": "payment provider"}]})
        result = _context_available_pack_map(ctx)
        assert "payment_provider" in result

    def test_pack_id_field_accepted(self):
        ctx = _ctx({"capability_packs": [{"pack_id": "auth0"}]})
        result = _context_available_pack_map(ctx)
        assert "auth0" in result

    def test_duplicate_pack_ids_merged(self):
        ctx = _ctx({
            "capability_packs": [{"id": "wallet", "from_caps": True}],
            "available_managed_capabilities": [{"id": "wallet", "from_hosted": True}],
        })
        result = _context_available_pack_map(ctx)
        # Both keys merged — both present
        assert result["wallet"]["from_caps"] is True
        assert result["wallet"]["from_hosted"] is True

    def test_non_dict_packs_skipped(self):
        ctx = _ctx({"capability_packs": ["not_a_dict", None, {"id": "valid"}]})
        result = _context_available_pack_map(ctx)
        assert list(result.keys()) == ["valid"]

    def test_packs_without_id_fields_skipped(self):
        ctx = _ctx({"capability_packs": [{"no_id_field": "x"}]})
        result = _context_available_pack_map(ctx)
        assert result == {}

    def test_non_list_pack_groups_skipped(self):
        ctx = _ctx({"capability_packs": "not_a_list"})
        result = _context_available_pack_map(ctx)
        assert result == {}


# ---------------------------------------------------------------------------
# 3. _context_managed_capability_ids
# ---------------------------------------------------------------------------

class TestContextManagedCapabilityIds:
    def test_none_returns_empty_frozenset(self):
        assert _context_managed_capability_ids(None) == frozenset()

    def test_packs_without_capability_source_excluded(self):
        ctx = _ctx({"capability_packs": [{"id": "wallet"}]})
        assert _context_managed_capability_ids(ctx) == frozenset()

    def test_managed_capability_source_included(self):
        ctx = _ctx({"capability_packs": [{"id": "billing", "capability_source": "managed_capability"}]})
        result = _context_managed_capability_ids(ctx)
        assert "billing" in result

    def test_non_hosted_source_excluded(self):
        ctx = _ctx({"capability_packs": [{"id": "tasks", "capability_source": "generated_module"}]})
        assert _context_managed_capability_ids(ctx) == frozenset()

    def test_mixed_packs_only_hosted_included(self):
        ctx = _ctx({"capability_packs": [
            {"id": "billing", "capability_source": "managed_capability"},
            {"id": "tasks"},
        ]})
        result = _context_managed_capability_ids(ctx)
        assert result == frozenset({"billing"})

    def test_available_managed_capabilities_key_also_searched(self):
        ctx = _ctx({
            "capability_packs": [],
            "available_managed_capabilities": [{"pack_id": "payment_provider", "capability_source": "managed_capability"}],
        })
        result = _context_managed_capability_ids(ctx)
        assert "payment_provider" in result


# ---------------------------------------------------------------------------
# 3a. _merge_available_pack_defaults
# ---------------------------------------------------------------------------

class TestMergeAvailablePackDefaults:
    def test_preserves_structured_required_integrations_from_available_pack(self):
        item = {"capability_pack_id": "mozaikspay", "capability_source": "managed_capability"}
        available = {
            "id": "mozaikspay",
            "required_integrations": [
                {
                    "service": "mozaikspay",
                    "provider": "mozaikspay",
                    "display_name": "MozaiksPay",
                    "kind": "api_key",
                    "required_fields": [
                        {"name": "api_base", "type": "url", "frontend_safe": True},
                        {"name": "client_secret", "type": "secret", "frontend_safe": False},
                    ],
                }
            ],
        }

        result = _merge_available_pack_defaults(item, available)

        assert result["required_integrations"] == available["required_integrations"]
        assert result["required_integrations"][0]["service"] == "mozaikspay"
        assert result["required_integrations"][0]["required_fields"][1]["name"] == "client_secret"


# ---------------------------------------------------------------------------
# 4. _pack_id_from_descriptor
# ---------------------------------------------------------------------------

class TestPackIdFromDescriptor:
    def test_capability_pack_id_field(self):
        assert _pack_id_from_descriptor({"capability_pack_id": "wallet"}) == "wallet"

    def test_id_field_fallback(self):
        assert _pack_id_from_descriptor({"id": "billing"}) == "billing"

    def test_pack_id_field_fallback(self):
        assert _pack_id_from_descriptor({"pack_id": "payment_provider"}) == "payment_provider"

    def test_all_empty_returns_empty_string(self):
        assert _pack_id_from_descriptor({}) == ""

    def test_whitespace_stripped(self):
        assert _pack_id_from_descriptor({"capability_pack_id": "  wallet  "}) == "wallet"

    def test_capability_pack_id_takes_priority(self):
        result = _pack_id_from_descriptor({
            "capability_pack_id": "primary",
            "id": "secondary",
        })
        assert result == "primary"


# ---------------------------------------------------------------------------
# 5. _pack_facades
# ---------------------------------------------------------------------------

class TestPackFacades:
    def test_top_level_facades_key(self):
        descriptor = {"facades": [{"module_id": "billing_portal"}]}
        result = _pack_facades(descriptor)
        assert len(result) == 1
        assert result[0]["module_id"] == "billing_portal"

    def test_nested_pack_facades_key(self):
        descriptor = {"pack": {"facades": [{"module_id": "checkout"}]}}
        result = _pack_facades(descriptor)
        assert len(result) == 1
        assert result[0]["module_id"] == "checkout"

    def test_no_facades_key_returns_empty(self):
        assert _pack_facades({}) == []

    def test_non_dict_pack_section_returns_empty(self):
        assert _pack_facades({"pack": "not_a_dict"}) == []

    def test_top_level_facades_takes_priority_over_nested(self):
        descriptor = {
            "facades": [{"module_id": "top_level"}],
            "pack": {"facades": [{"module_id": "nested"}]},
        }
        result = _pack_facades(descriptor)
        assert result[0]["module_id"] == "top_level"

    def test_non_dict_items_in_facades_list_skipped(self):
        descriptor = {"facades": ["not_a_dict", {"module_id": "valid"}]}
        result = _pack_facades(descriptor)
        assert len(result) == 1
        assert result[0]["module_id"] == "valid"


# ---------------------------------------------------------------------------
# 6. _facade_pack_descriptor
# ---------------------------------------------------------------------------

class TestFacadePackDescriptor:
    def _base_facade(self):
        return {
            "module_id": "billing_portal",
            "provider_module": "mozaikspay",
            "label": "Billing Portal",
            "summary": "App-owned billing facade.",
            "pages": [],
        }

    def test_missing_ids_returns_none(self):
        assert _facade_pack_descriptor({}) is None
        assert _facade_pack_descriptor({"module_id": "", "facade_id": ""}) is None

    def test_facade_id_field_used(self):
        result = _facade_pack_descriptor({"facade_id": "checkout"})
        assert result is not None
        assert result["capability_pack_id"] == "checkout"

    def test_module_id_takes_priority_over_facade_id(self):
        result = _facade_pack_descriptor({"module_id": "billing_portal", "facade_id": "other"})
        assert result["capability_pack_id"] == "billing_portal"

    def test_surface_kind_is_module(self):
        result = _facade_pack_descriptor(self._base_facade())
        assert result["surface_kind"] == "module"

    def test_pack_type_is_managed_facade(self):
        result = _facade_pack_descriptor(self._base_facade())
        assert result["pack_type"] == "managed_facade"

    def test_label_from_facade(self):
        result = _facade_pack_descriptor(self._base_facade())
        assert result["label"] == "Billing Portal"

    def test_label_auto_generated_from_id(self):
        facade = {"module_id": "billing_portal"}
        result = _facade_pack_descriptor(facade)
        assert "Billing Portal" in result["label"] or "billing_portal" in result["label"]

    def test_pages_primary_actions_in_operations(self):
        facade = {
            "module_id": "checkout",
            "pages": [{"name": "checkout", "primary_actions": ["create_checkout", "cancel_checkout"]}],
        }
        result = _facade_pack_descriptor(facade)
        assert "create_checkout" in result["operations"]
        assert "cancel_checkout" in result["operations"]

    def test_pages_primary_entities_collected(self):
        facade = {
            "module_id": "checkout",
            "pages": [{"name": "checkout", "primary_entities": ["Subscription"]}],
        }
        result = _facade_pack_descriptor(facade)
        assert "Subscription" in result["primary_entities"]

    def test_primary_pages_from_page_names(self):
        facade = {
            "module_id": "billing_portal",
            "pages": [{"name": "Billing Dashboard"}, {"name": "Invoice List"}],
        }
        result = _facade_pack_descriptor(facade)
        assert "billing_dashboard" in result["primary_pages"]
        assert "invoice_list" in result["primary_pages"]

    def test_provider_module_in_required_integrations(self):
        result = _facade_pack_descriptor(self._base_facade())
        assert "mozaikspay" in result["required_integrations"]

    def test_implementation_mode_is_declarative_module(self):
        result = _facade_pack_descriptor(self._base_facade())
        assert result["implementation_mode"] == "declarative_module"

    def test_capability_source_is_generated_module(self):
        result = _facade_pack_descriptor(self._base_facade())
        assert result["capability_source"] == "generated_module"


# ---------------------------------------------------------------------------
# 7. _iter_page_api_endpoints
# ---------------------------------------------------------------------------

class TestIterPageApiEndpoints:
    def test_flat_dict_yields_endpoint(self):
        page = {"api_endpoint": "/api/modules/tasks/list_tasks"}
        result = list(_iter_page_api_endpoints(page))
        assert result == ["/api/modules/tasks/list_tasks"]

    def test_nested_dict_yields_from_nested(self):
        page = {"section": {"api_endpoint": "/api/modules/tasks/create_task"}}
        result = list(_iter_page_api_endpoints(page))
        assert "/api/modules/tasks/create_task" in result

    def test_list_of_dicts_yields_all(self):
        data = [
            {"api_endpoint": "/api/modules/a/action"},
            {"api_endpoint": "/api/modules/b/action"},
        ]
        result = list(_iter_page_api_endpoints(data))
        assert len(result) == 2

    def test_non_string_api_endpoint_skipped(self):
        page = {"api_endpoint": 42}
        result = list(_iter_page_api_endpoints(page))
        assert result == []

    def test_empty_endpoint_skipped(self):
        page = {"api_endpoint": "   "}
        result = list(_iter_page_api_endpoints(page))
        assert result == []

    def test_no_api_endpoint_yields_nothing(self):
        page = {"name": "Page", "title": "My Page"}
        result = list(_iter_page_api_endpoints(page))
        assert result == []

    def test_deeply_nested(self):
        page = {"content": {"form": {"api_endpoint": "/api/modules/x/action"}}}
        result = list(_iter_page_api_endpoints(page))
        assert result == ["/api/modules/x/action"]

    def test_scalar_yields_nothing(self):
        assert list(_iter_page_api_endpoints("scalar")) == []
        assert list(_iter_page_api_endpoints(42)) == []
        assert list(_iter_page_api_endpoints(None)) == []


# ---------------------------------------------------------------------------
# 8. _validate_page_bindings
# ---------------------------------------------------------------------------

class TestValidatePageBindings:
    def test_no_forbidden_ids_no_error(self):
        pages = [{"name": "Tasks", "api_endpoint": "/api/modules/managed_capability/action"}]
        _validate_page_bindings(pages, managed_capability_ids=frozenset(), managed_capability_backing_module_ids=frozenset())

    def test_page_binds_to_allowed_module_no_error(self):
        pages = [{"name": "Tasks", "api_endpoint": "/api/modules/tasks/list_tasks"}]
        _validate_page_bindings(
            pages,
            managed_capability_ids=frozenset({"billing"}),
            managed_capability_backing_module_ids=frozenset(),
        )

    def test_page_binds_to_managed_capability_raises(self):
        pages = [{"name": "Billing", "api_endpoint": "/api/modules/billing/create_checkout"}]
        try:
            _validate_page_bindings(
                pages,
                managed_capability_ids=frozenset({"billing"}),
                managed_capability_backing_module_ids=frozenset(),
            )
            raise AssertionError("Expected ValueError")
        except ValueError as exc:
            assert "billing" in str(exc)

    def test_page_binds_to_backing_module_raises(self):
        pages = [{"name": "payment provider", "api_endpoint": "/api/modules/payment_provider_backend/charge"}]
        try:
            _validate_page_bindings(
                pages,
                managed_capability_ids=frozenset({"billing"}),
                managed_capability_backing_module_ids=frozenset({"payment_provider_backend"}),
            )
            raise AssertionError("Expected ValueError")
        except ValueError as exc:
            assert "payment_provider_backend" in str(exc)

    def test_nested_endpoint_binding_raises(self):
        pages = [{"name": "P", "section": {"api_endpoint": "/api/modules/billing/pay"}}]
        import pytest
        with pytest.raises(ValueError, match="billing"):
            _validate_page_bindings(
                pages,
                managed_capability_ids=frozenset({"billing"}),
                managed_capability_backing_module_ids=frozenset(),
            )


# ---------------------------------------------------------------------------
# 9. _managed_capability_backing_module_ids
# ---------------------------------------------------------------------------

class TestHostedBackingModuleIds:
    def _ctx_with_hosted(self, pack_id: str, pack_data: dict):
        return _ctx({"available_managed_capabilities": [{**pack_data, "id": pack_id, "capability_source": "managed_capability"}]})

    def test_no_managed_capabilities_returns_empty(self):
        result = _managed_capability_backing_module_ids([], context_variables=None)
        assert result == frozenset()

    def test_backing_module_field_included(self):
        ctx = self._ctx_with_hosted("billing", {"backing_module": "payment_provider_internal"})
        packs = [{"capability_pack_id": "billing", "capability_source": "managed_capability"}]
        result = _managed_capability_backing_module_ids(packs, context_variables=ctx)
        assert "payment_provider_internal" in result

    def test_requires_modules_included(self):
        ctx = self._ctx_with_hosted("billing", {"requires_modules": ["payment_processor"]})
        packs = [{"capability_pack_id": "billing", "capability_source": "managed_capability"}]
        result = _managed_capability_backing_module_ids(packs, context_variables=ctx)
        assert "payment_processor" in result

    def test_capabilities_module_field_included(self):
        ctx = self._ctx_with_hosted("billing", {
            "capabilities": [{"capability_id": "pay", "module": "payment_gateway"}],
        })
        packs = [{"capability_pack_id": "billing", "capability_source": "managed_capability"}]
        result = _managed_capability_backing_module_ids(packs, context_variables=ctx)
        assert "payment_gateway" in result

    def test_managed_capability_own_id_excluded(self):
        ctx = self._ctx_with_hosted("billing", {"backing_module": "billing"})
        packs = [{"capability_pack_id": "billing", "capability_source": "managed_capability"}]
        result = _managed_capability_backing_module_ids(packs, context_variables=ctx)
        assert "billing" not in result

    def test_non_managed_capabilities_excluded(self):
        ctx = _ctx({"available_managed_capabilities": [
            {"id": "billing", "capability_source": "generated_module", "backing_module": "billing_backend"},
        ]})
        packs = [{"capability_pack_id": "billing", "capability_source": "generated_module"}]
        result = _managed_capability_backing_module_ids(packs, context_variables=ctx)
        assert "billing_backend" not in result

    def test_pack_not_in_context_returns_empty(self):
        packs = [{"capability_pack_id": "billing", "capability_source": "managed_capability"}]
        result = _managed_capability_backing_module_ids(packs, context_variables=_ctx({}))
        assert result == frozenset()


# ---------------------------------------------------------------------------
# 10. _selected_managed_capability_descriptors
# ---------------------------------------------------------------------------

class TestSelectedManagedCapabilityDescriptors:
    def test_non_managed_capabilities_excluded(self):
        packs = [{"capability_pack_id": "tasks", "capability_source": "generated_module"}]
        result = _selected_managed_capability_descriptors(packs, context_variables=None)
        assert result == {}


# ---------------------------------------------------------------------------
# 10b. _apply_selected_pack_files
# ---------------------------------------------------------------------------

class TestApplySelectedPackFiles:
    def test_pack_declared_facade_overrides_llm_managed_source(self):
        ctx = _ctx({"available_managed_capabilities": [{
            "id": "mozaikspay",
            "capability_source": "managed_capability",
            "facades": [{"module_id": "billing_portal", "provider_module": "mozaikspay"}],
        }]})
        packs, pages, tasks = _apply_selected_pack_files(
            capability_packs=[
                {"capability_pack_id": "mozaikspay", "capability_source": "managed_capability"},
                {"capability_pack_id": "billing_portal", "capability_source": "managed_capability"},
            ],
            pages=[],
            build_tasks=[],
            context_variables=ctx,
        )

        by_id = {pack["capability_pack_id"]: pack for pack in packs}
        assert by_id["billing_portal"]["capability_source"] == "generated_module"
        assert by_id["billing_portal"]["pack_type"] == "managed_facade"
        assert pages == []
        assert tasks == []

    def test_managed_capability_in_available_packs_returns_available_descriptor(self):
        ctx = _ctx({"available_managed_capabilities": [{
            "id": "billing",
            "capability_source": "managed_capability",
            "facades": [{"module_id": "billing_portal"}],
        }]})
        packs = [{"capability_pack_id": "billing", "capability_source": "managed_capability"}]
        result = _selected_managed_capability_descriptors(packs, context_variables=ctx)
        assert "billing" in result
        # Descriptor from available_packs includes facades
        assert "facades" in result["billing"]

    def test_managed_capability_not_in_context_uses_pack_itself(self):
        packs = [{"capability_pack_id": "billing", "capability_source": "managed_capability", "custom": True}]
        result = _selected_managed_capability_descriptors(packs, context_variables=_ctx({}))
        assert "billing" in result
        assert result["billing"]["custom"] is True

    def test_empty_pack_id_excluded(self):
        packs = [{"capability_source": "managed_capability"}]
        result = _selected_managed_capability_descriptors(packs, context_variables=None)
        assert result == {}


# ---------------------------------------------------------------------------
# 11. _managed_facade_route_rules
# ---------------------------------------------------------------------------

class TestHostedFacadeRouteRules:
    def test_no_managed_capabilities_empty_rules(self):
        result = _managed_facade_route_rules([], context_variables=None)
        assert result == {}

    def test_facade_with_provider_actions_builds_rules(self):
        ctx = _ctx({"available_managed_capabilities": [{
            "id": "billing",
            "capability_source": "managed_capability",
            "facades": [{
                "module_id": "billing_portal",
                "provider_module": "mozaikspay",
                "provider_actions": ["create_checkout", "cancel_subscription"],
            }],
        }]})
        packs = [{"capability_pack_id": "billing", "capability_source": "managed_capability"}]
        rules = _managed_facade_route_rules(packs, context_variables=ctx)
        assert rules[("mozaikspay", "create_checkout")] == "billing_portal"
        assert rules[("mozaikspay", "cancel_subscription")] == "billing_portal"

    def test_facade_missing_provider_module_falls_back_to_pack_id(self):
        ctx = _ctx({"available_managed_capabilities": [{
            "id": "billing",
            "capability_source": "managed_capability",
            "facades": [{
                "module_id": "billing_portal",
                "provider_actions": ["checkout"],
            }],
        }]})
        packs = [{"capability_pack_id": "billing", "capability_source": "managed_capability"}]
        rules = _managed_facade_route_rules(packs, context_variables=ctx)
        # Falls back to pack_id as provider_module
        assert rules[("billing", "checkout")] == "billing_portal"

    def test_facade_missing_facade_id_skipped(self):
        ctx = _ctx({"available_managed_capabilities": [{
            "id": "billing",
            "capability_source": "managed_capability",
            "facades": [{
                "provider_module": "mozaikspay",
                "provider_actions": ["checkout"],
            }],
        }]})
        packs = [{"capability_pack_id": "billing", "capability_source": "managed_capability"}]
        rules = _managed_facade_route_rules(packs, context_variables=ctx)
        assert rules == {}


# ---------------------------------------------------------------------------
# 12. _validate_user_facing_managed_capability_tasks
# ---------------------------------------------------------------------------

class TestValidateUserFacingManagedCapabilityTasks:
    def _wallet_pack(self):
        return {
            "capability_pack_id": "wallet",
            "capability_source": "managed_capability",
            "surface_kind": "external_integration",
            "operations": ["wallet.view", "wallet.payout"],
        }

    def _wallet_page(self):
        return {
            "name": "Wallet",
            "route": "/wallet",
            "purpose": "Show wallet balance and payout activity.",
            "primary_actions": ["wallet.view"],
        }

    def test_user_facing_managed_capability_requires_page_bundle(self):
        with pytest.raises(ValueError, match="no page_bundle build task"):
            _validate_user_facing_managed_capability_tasks(
                [self._wallet_pack()],
                [self._wallet_page()],
                [
                    {
                        "task_id": "task_wallet_facade",
                        "task_type": "module_contract",
                        "capability_pack_id": "wallet_dashboard",
                    }
                ],
                managed_capability_ids=frozenset({"wallet"}),
            )

    def test_user_facing_managed_capability_requires_facade_module_contract(self):
        with pytest.raises(ValueError, match="no app-owned facade module_contract task"):
            _validate_user_facing_managed_capability_tasks(
                [self._wallet_pack()],
                [self._wallet_page()],
                [
                    {
                        "task_id": "task_wallet_pages",
                        "task_type": "page_bundle",
                        "capability_pack_id": "wallet_dashboard",
                    }
                ],
                managed_capability_ids=frozenset({"wallet"}),
            )

    def test_backend_only_managed_capability_does_not_require_facade(self):
        _validate_user_facing_managed_capability_tasks(
            [self._wallet_pack()],
            [{"name": "Home", "route": "/", "purpose": "General landing page."}],
            [
                {
                    "task_id": "task_wallet_adapter",
                    "task_type": "api_surface",
                    "capability_pack_id": "wallet",
                }
            ],
            managed_capability_ids=frozenset({"wallet"}),
        )


# ---------------------------------------------------------------------------
# 13. _managed_facade_route_rules — additional edge cases
# ---------------------------------------------------------------------------

def test_empty_provider_actions_produces_no_rules():
    ctx = _ctx({"available_managed_capabilities": [{
        "id": "billing",
        "capability_source": "managed_capability",
        "facades": [{
            "module_id": "billing_portal",
            "provider_module": "mozaikspay",
            "provider_actions": [],
        }],
    }]})
    packs = [{"capability_pack_id": "billing", "capability_source": "managed_capability"}]
    rules = _managed_facade_route_rules(packs, context_variables=ctx)
    assert rules == {}


# ---------------------------------------------------------------------------
# 14. _validate_monetization_provider_selection
# ---------------------------------------------------------------------------

_SUBSCRIPTION_CONFIG_TASK = {"task_type": "subscription_config", "task_id": "sub_cfg"}
_MOZAIKSPAY_DESCRIPTOR = {
    "capability_pack_id": "mozaikspay",
    "capability_source": "managed_capability",
    "display_name": "MozaiksPay",
    "provides_capabilities": ["subscription_write_path"],
}
_ENTITLEMENT_DISPATCH_DESCRIPTOR = {
    "capability_pack_id": "entitlement_dispatch",
    "capability_source": "generated_module",
}


class TestValidateMonetizationProviderSelection:
    def test_no_subscription_config_rejects_provider_value(self):
        with pytest.raises(ValueError, match="only valid when build_tasks include"):
            _validate_monetization_provider_selection(
                [],
                [{"task_type": "module_contract"}],
                monetization_provider="mozaiks_pay",
            )

    def test_subscription_config_requires_provider(self):
        with pytest.raises(ValueError, match="monetization_provider is required"):
            _validate_monetization_provider_selection(
                [_MOZAIKSPAY_DESCRIPTOR],
                [_SUBSCRIPTION_CONFIG_TASK],
                monetization_provider=None,
            )

    def test_mozaiks_pay_with_selected_pack_passes(self):
        _validate_monetization_provider_selection(
            [_MOZAIKSPAY_DESCRIPTOR],
            [_SUBSCRIPTION_CONFIG_TASK],
            monetization_provider="mozaiks_pay",
        )

    def test_mozaiks_pay_requires_mozaikspay_pack(self):
        with pytest.raises(ValueError, match="requires the mozaikspay managed capability pack"):
            _validate_monetization_provider_selection(
                [],
                [_SUBSCRIPTION_CONFIG_TASK],
                monetization_provider="mozaiks_pay",
            )

    def test_entitlement_dispatch_with_selected_pack_passes(self):
        _validate_monetization_provider_selection(
            [_ENTITLEMENT_DISPATCH_DESCRIPTOR],
            [_SUBSCRIPTION_CONFIG_TASK],
            monetization_provider="entitlement_dispatch",
        )

    def test_entitlement_dispatch_rejects_managed_write_path_owner(self):
        with pytest.raises(ValueError, match="must not both be selected"):
            _validate_monetization_provider_selection(
                [_ENTITLEMENT_DISPATCH_DESCRIPTOR, _MOZAIKSPAY_DESCRIPTOR],
                [_SUBSCRIPTION_CONFIG_TASK],
                monetization_provider="entitlement_dispatch",
            )

    def test_multiple_managed_write_path_owners_rejected(self):
        custom = {
            "capability_pack_id": "custom_billing",
            "capability_source": "managed_capability",
            "provides_capabilities": ["subscription_write_path"],
        }
        with pytest.raises(ValueError, match="Multiple managed capability packs"):
            _validate_monetization_provider_selection(
                [_MOZAIKSPAY_DESCRIPTOR, custom],
                [_SUBSCRIPTION_CONFIG_TASK],
                monetization_provider="mozaiks_pay",
            )
