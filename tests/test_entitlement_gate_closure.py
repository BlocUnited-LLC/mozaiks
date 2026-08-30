"""Deterministic compile-time closure between entitlement gates and subscriptions.

These tests prove that generated app bundles cannot pass bundle validation and
then permanently deny actions because a declared entitlement_gate references a
capability_id that no subscription plan grants.

Validation boundary
-------------------
``_scan_entitlement_gate_capability_alignment`` in ``generated_bundle_scanner``
is the cross-file validation owner.  It consumes:

- ``config/subscriptions.yaml``  — parsed through the canonical
  ``SubscriptionsConfig`` loader model, then inspected for plan capabilities
- ``modules/*/module.yaml``      — parsed via ``_entitlement_gate_map_from_module_yaml``
  ({action_id → capability_id} for gated actions)

Configured adapter selection
----------------------------
The platform wires ``ConfiguredEntitlementAdapter`` whenever
``config/subscriptions.yaml`` loads successfully.  ``assignment_store`` controls
persisted subscription assignment lookup; it is not the adapter-selection signal.

Apps without ``subscriptions.yaml`` at all use ``NoOpEntitlementAdapter`` and
are skipped.

Duplicate capability detection
--------------------------------
``PlanDef._validate_capabilities`` in ``subscriptions_loader`` rejects plans
that list the same ``capability_id`` twice.  This is tested here alongside the
scanner because the full validation path runs both layers.

Scope
-----
These tests do NOT exercise:
- AG2 reasoning, AppBuildPlan construction, or Jinja materialization
- CapabilityPack template selection or injection
- Real MongoDB or network calls
"""

from __future__ import annotations

import textwrap

import pytest
from pydantic import ValidationError

from factory_app.workflows.AppGenerator.tools.generated_bundle_scanner import (
    _capability_ids_from_subscriptions_yaml,
    _entitlement_gate_map_from_module_yaml,
    _scan_entitlement_gate_capability_alignment,
    scan_generated_bundle,
)
from mozaiksai.core.runtime.app.entitlements import ConfiguredEntitlementAdapter
from mozaiksai.core.runtime.app.subscriptions_loader import (
    PlanDef,
    SubscriptionsConfig,
)

# ---------------------------------------------------------------------------
# Fixtures — minimal valid bundle fragments
# ---------------------------------------------------------------------------

_SUBS_V1_WITH_STORE = textwrap.dedent("""\
    schema_version: mozaiks.subscriptions.v1
    label: Task SaaS
    default_plan_id: free
    assignment_store:
      data_alias: billing.subscriptions
    plans:
      - plan_id: free
        label: Free
        capabilities: []
      - plan_id: pro
        label: Pro
        capabilities:
          - tasks.create
          - tasks.delete
          - analytics.view
""")

_SUBS_V1_NO_STORE = textwrap.dedent("""\
    schema_version: mozaiks.subscriptions.v1
    label: Task App
    default_plan_id: free
    plans:
      - plan_id: free
        label: Free
        capabilities:
          - tasks.create
""")

_SUBS_V2_WITH_STORE = textwrap.dedent("""\
    schema_version: mozaiks.subscriptions.v2
    label: Multi-product SaaS
    default_product_id: core
    products:
      - product_id: core
        label: Core
        default_plan_id: free
        assignment_store:
          data_alias: billing.core_subscriptions
        plans:
          - plan_id: free
            label: Free
            capabilities: []
          - plan_id: pro
            label: Pro
            capabilities:
              - tasks.create
              - tasks.delete
""")

_SUBS_V2_NO_STORE = textwrap.dedent("""\
    schema_version: mozaiks.subscriptions.v2
    label: Multi-product App
    default_product_id: core
    products:
      - product_id: core
        label: Core
        default_plan_id: free
        plans:
          - plan_id: free
            label: Free
            capabilities:
              - tasks.create
""")


def _module_yaml(module_id: str, *, actions: list[dict]) -> str:
    """Build a minimal valid module.yaml string."""
    lines = [
        "schema_version: mozaiks.module.v1",
        "module:",
        f"  id: {module_id}",
        f"  display_name: {module_id.title()}",
        "  version: 1.0.0",
        "  description: Test module",
        "  handler: backend.handler:Handler",
        "permissions: []",
        "actions:",
    ]
    if not actions:
        lines.append("  []")
    else:
        for action in actions:
            lines.append(f"  - id: {action['id']}")
            lines.append(f"    description: {action.get('description', 'desc')}")
            lines.append(f"    handler_method: {action['id']}")
            if action.get("entitlement_gate"):
                lines.append(f"    entitlement_gate: {action['entitlement_gate']}")
    return "\n".join(lines) + "\n"


def _bundle(*, subs: str | None = None, modules: dict[str, str] | None = None) -> dict[str, str]:
    """Assemble a minimal files_map for scanner testing."""
    files: dict[str, str] = {}
    if subs is not None:
        files["config/subscriptions.yaml"] = subs
    for path, content in (modules or {}).items():
        files[path] = content
    return files


# ---------------------------------------------------------------------------
# Unit: _capability_ids_from_subscriptions_yaml
# ---------------------------------------------------------------------------


class TestCapabilityIdsFromSubscriptionsYaml:
    def test_v1_extracts_capabilities_from_all_plans(self) -> None:
        caps = _capability_ids_from_subscriptions_yaml(_SUBS_V1_WITH_STORE)
        assert caps == {"tasks.create", "tasks.delete", "analytics.view"}

    def test_v1_free_plan_with_no_capabilities_returns_empty(self) -> None:
        subs = textwrap.dedent("""\
            schema_version: mozaiks.subscriptions.v1
            label: App
            default_plan_id: free
            plans:
              - plan_id: free
                label: Free
                capabilities: []
        """)
        assert _capability_ids_from_subscriptions_yaml(subs) == set()

    def test_v2_extracts_capabilities_from_product_plans(self) -> None:
        caps = _capability_ids_from_subscriptions_yaml(_SUBS_V2_WITH_STORE)
        assert caps == {"tasks.create", "tasks.delete"}

    def test_invalid_yaml_returns_empty_set(self) -> None:
        assert _capability_ids_from_subscriptions_yaml(": }{bad yaml") == set()

    def test_empty_string_returns_empty_set(self) -> None:
        assert _capability_ids_from_subscriptions_yaml("") == set()


class TestEntitlementGateMapFromModuleYaml:
    def test_returns_action_to_gate_mapping(self) -> None:
        content = _module_yaml(
            "tasks",
            actions=[
                {"id": "create_task", "entitlement_gate": "tasks.create"},
                {"id": "list_tasks"},
                {"id": "delete_task", "entitlement_gate": "tasks.delete"},
            ],
        )
        result = _entitlement_gate_map_from_module_yaml("modules/tasks/module.yaml", content)
        assert result == {"create_task": "tasks.create", "delete_task": "tasks.delete"}

    def test_ungated_module_returns_empty_dict(self) -> None:
        content = _module_yaml("tasks", actions=[{"id": "list_tasks"}])
        result = _entitlement_gate_map_from_module_yaml("modules/tasks/module.yaml", content)
        assert result == {}

    def test_invalid_yaml_returns_empty_dict(self) -> None:
        result = _entitlement_gate_map_from_module_yaml(
            "modules/tasks/module.yaml", ": }{bad yaml"
        )
        assert result == {}


# ---------------------------------------------------------------------------
# Unit: PlanDef duplicate capability detection
# ---------------------------------------------------------------------------


class TestPlanDefDuplicateCapabilities:
    def test_duplicate_capability_raises_validation_error(self) -> None:
        with pytest.raises(ValidationError, match="duplicate capability_ids"):
            PlanDef.model_validate(
                {
                    "plan_id": "pro",
                    "label": "Pro",
                    "capabilities": ["tasks.create", "tasks.delete", "tasks.create"],
                }
            )

    def test_unique_capabilities_pass(self) -> None:
        plan = PlanDef.model_validate(
            {
                "plan_id": "pro",
                "label": "Pro",
                "capabilities": ["tasks.create", "tasks.delete"],
            }
        )
        assert plan.capabilities == ["tasks.create", "tasks.delete"]

    def test_empty_capabilities_pass(self) -> None:
        plan = PlanDef.model_validate({"plan_id": "free", "label": "Free", "capabilities": []})
        assert plan.capabilities == []

    def test_subscriptions_config_rejects_plan_with_duplicate_capabilities(self) -> None:
        """Duplicate detection works through the full SubscriptionsConfig loader."""
        raw = {
            "schema_version": "mozaiks.subscriptions.v1",
            "label": "Test",
            "default_plan_id": "pro",
            "plans": [
                {
                    "plan_id": "pro",
                    "label": "Pro",
                    "capabilities": ["tasks.create", "analytics.view", "tasks.create"],
                }
            ],
        }
        with pytest.raises(ValidationError, match="duplicate capability_ids"):
            SubscriptionsConfig.model_validate(raw)


# ---------------------------------------------------------------------------
# Integration: _scan_entitlement_gate_capability_alignment
# ---------------------------------------------------------------------------


class TestEntitlementGateClosurePositive:
    """Valid bundles must produce zero errors."""

    def test_valid_gated_action_with_granted_capability(self) -> None:
        bundle = _bundle(
            subs=_SUBS_V1_WITH_STORE,
            modules={
                "modules/tasks/module.yaml": _module_yaml(
                    "tasks",
                    actions=[{"id": "create_task", "entitlement_gate": "tasks.create"}],
                )
            },
        )
        assert _scan_entitlement_gate_capability_alignment(bundle) == []

    def test_multiple_plans_one_grants_capability(self) -> None:
        """Gate is valid when only the paid plan, not the free plan, grants it."""
        bundle = _bundle(
            subs=_SUBS_V1_WITH_STORE,
            modules={
                "modules/analytics/module.yaml": _module_yaml(
                    "analytics",
                    actions=[{"id": "view_dashboard", "entitlement_gate": "analytics.view"}],
                )
            },
        )
        # analytics.view is in pro plan only; free plan has none.  Must pass.
        assert _scan_entitlement_gate_capability_alignment(bundle) == []

    def test_ungated_app_without_subscriptions_yaml_passes(self) -> None:
        bundle = _bundle(
            modules={
                "modules/tasks/module.yaml": _module_yaml(
                    "tasks",
                    actions=[{"id": "create_task", "entitlement_gate": "tasks.create"}],
                )
            }
        )
        assert _scan_entitlement_gate_capability_alignment(bundle) == []

    def test_capability_free_subscriptions_with_no_gates_passes(self) -> None:
        """Token/usage-only SaaS: no plan grants capabilities, so no gate is required."""
        subs_no_caps = textwrap.dedent("""\
            schema_version: mozaiks.subscriptions.v1
            label: Token Only SaaS
            default_plan_id: free
            plans:
              - plan_id: free
                label: Free
                capabilities: []
              - plan_id: pro
                label: Pro
                capabilities: []
        """)
        bundle = _bundle(
            subs=subs_no_caps,
            modules={
                "modules/tasks/module.yaml": _module_yaml(
                    "tasks",
                    actions=[{"id": "list_tasks"}],
                )
            },
        )
        assert _scan_entitlement_gate_capability_alignment(bundle) == []

    def test_capabilities_without_module_files_passes(self) -> None:
        """A bundle fragment with no modules has nothing to gate; other scans own file completeness."""
        bundle = _bundle(subs=_SUBS_V1_WITH_STORE)
        assert _scan_entitlement_gate_capability_alignment(bundle) == []

    def test_v2_multi_product_subscriptions_valid_gate(self) -> None:
        bundle = _bundle(
            subs=_SUBS_V2_WITH_STORE,
            modules={
                "modules/tasks/module.yaml": _module_yaml(
                    "tasks",
                    actions=[{"id": "create_task", "entitlement_gate": "tasks.create"}],
                )
            },
        )
        assert _scan_entitlement_gate_capability_alignment(bundle) == []

    def test_v1_without_assignment_store_still_validates_granted_gate(self) -> None:
        """assignment_store is not the runtime adapter-selection signal."""
        bundle = _bundle(
            subs=_SUBS_V1_NO_STORE,
            modules={
                "modules/tasks/module.yaml": _module_yaml(
                    "tasks",
                    actions=[{"id": "create_task", "entitlement_gate": "tasks.create"}],
                )
            },
        )
        assert _scan_entitlement_gate_capability_alignment(bundle) == []

    def test_v2_without_assignment_store_still_validates_granted_gate(self) -> None:
        bundle = _bundle(
            subs=_SUBS_V2_NO_STORE,
            modules={
                "modules/tasks/module.yaml": _module_yaml(
                    "tasks",
                    actions=[{"id": "create_task", "entitlement_gate": "tasks.create"}],
                )
            },
        )
        assert _scan_entitlement_gate_capability_alignment(bundle) == []


class TestEntitlementGateClosureNegative:
    """Invalid bundles must produce at least one error."""

    def test_sold_capabilities_with_zero_gated_actions_fails(self) -> None:
        """Plan capabilities with no gated action anywhere = unenforceable monetization."""
        bundle = _bundle(
            subs=_SUBS_V1_WITH_STORE,
            modules={
                "modules/tasks/module.yaml": _module_yaml(
                    "tasks",
                    actions=[{"id": "list_tasks"}],
                )
            },
        )
        errors = _scan_entitlement_gate_capability_alignment(bundle)
        assert len(errors) == 1
        assert "no module action declares an entitlement_gate" in errors[0]
        assert "tasks.create" in errors[0]
        assert "modules/tasks/module.yaml" in errors[0]

    def test_zero_gate_error_reaches_scan_generated_bundle(self) -> None:
        bundle = _bundle(
            subs=_SUBS_V1_WITH_STORE,
            modules={
                "modules/tasks/module.yaml": _module_yaml(
                    "tasks",
                    actions=[{"id": "list_tasks"}],
                )
            },
        )
        errors = scan_generated_bundle(bundle)
        assert any("no module action declares an entitlement_gate" in error for error in errors)

    def test_unknown_gate_fails(self) -> None:
        bundle = _bundle(
            subs=_SUBS_V1_WITH_STORE,
            modules={
                "modules/tasks/module.yaml": _module_yaml(
                    "tasks",
                    actions=[{"id": "create_task", "entitlement_gate": "tasks.unknown_cap"}],
                )
            },
        )
        errors = _scan_entitlement_gate_capability_alignment(bundle)
        assert len(errors) == 1
        assert "tasks.unknown_cap" in errors[0]
        assert "create_task" in errors[0]
        assert "modules/tasks/module.yaml" in errors[0]

    def test_typo_near_match_fails_with_suggestion(self) -> None:
        """A near-misspelling of a valid capability must fail with a helpful suggestion."""
        bundle = _bundle(
            subs=_SUBS_V1_WITH_STORE,
            modules={
                "modules/tasks/module.yaml": _module_yaml(
                    "tasks",
                    # tasks.creat is a typo of tasks.create
                    actions=[{"id": "create_task", "entitlement_gate": "tasks.creat"}],
                )
            },
        )
        errors = _scan_entitlement_gate_capability_alignment(bundle)
        assert len(errors) == 1
        assert "tasks.creat" in errors[0]
        assert "Near-matches" in errors[0]
        assert "tasks.create" in errors[0]

    def test_capability_not_granted_by_any_plan_fails(self) -> None:
        """Capability exists as a valid ID format but appears in NO plan."""
        bundle = _bundle(
            subs=_SUBS_V1_WITH_STORE,
            modules={
                "modules/analytics/module.yaml": _module_yaml(
                    "analytics",
                    actions=[{"id": "export_data", "entitlement_gate": "analytics.export"}],
                )
            },
        )
        errors = _scan_entitlement_gate_capability_alignment(bundle)
        assert len(errors) == 1
        assert "analytics.export" in errors[0]
        assert "analytics/module.yaml" in errors[0]

    def test_all_plans_grant_no_capabilities_gated_action_fails(self) -> None:
        """When every plan's capabilities is empty, gated actions permanently deny."""
        subs_all_empty = textwrap.dedent("""\
            schema_version: mozaiks.subscriptions.v1
            label: App
            default_plan_id: free
            assignment_store:
              data_alias: billing.subscriptions
            plans:
              - plan_id: free
                label: Free
                capabilities: []
              - plan_id: pro
                label: Pro
                capabilities: []
        """)
        bundle = _bundle(
            subs=subs_all_empty,
            modules={
                "modules/tasks/module.yaml": _module_yaml(
                    "tasks",
                    actions=[{"id": "create_task", "entitlement_gate": "tasks.create"}],
                )
            },
        )
        errors = _scan_entitlement_gate_capability_alignment(bundle)
        assert len(errors) == 1
        assert "tasks.create" in errors[0]
        assert "No plan currently grants any capabilities" in errors[0]

    def test_v1_without_assignment_store_unknown_gate_fails(self) -> None:
        """A configured adapter cannot evade validation by omitting assignment_store."""
        bundle = _bundle(
            subs=_SUBS_V1_NO_STORE,
            modules={
                "modules/tasks/module.yaml": _module_yaml(
                    "tasks",
                    actions=[{"id": "create_task", "entitlement_gate": "custom.dynamic.cap"}],
                )
            },
        )
        errors = _scan_entitlement_gate_capability_alignment(bundle)
        assert len(errors) == 1
        assert "custom.dynamic.cap" in errors[0]
        assert "create_task" in errors[0]

    def test_v2_without_assignment_store_unknown_gate_fails(self) -> None:
        bundle = _bundle(
            subs=_SUBS_V2_NO_STORE,
            modules={
                "modules/tasks/module.yaml": _module_yaml(
                    "tasks",
                    actions=[{"id": "create_task", "entitlement_gate": "dynamic.runtime.cap"}],
                )
            },
        )
        errors = _scan_entitlement_gate_capability_alignment(bundle)
        assert len(errors) == 1
        assert "dynamic.runtime.cap" in errors[0]

    def test_malformed_subscriptions_yaml_fails_gate_validation(self) -> None:
        """Malformed YAML is not a dynamic-adapter exemption."""
        bundle = _bundle(
            subs=": }{not valid yaml",
            modules={
                "modules/tasks/module.yaml": _module_yaml(
                    "tasks",
                    actions=[{"id": "create_task", "entitlement_gate": "tasks.create"}],
                )
            },
        )
        errors = _scan_entitlement_gate_capability_alignment(bundle)
        assert len(errors) == 1
        assert "config/subscriptions.yaml" in errors[0]
        assert "must be valid YAML" in errors[0]

    def test_deterministic_diagnostic_ordering_across_multiple_failures(self) -> None:
        """Multiple gate failures are returned in deterministic sorted order."""
        bundle = _bundle(
            subs=_SUBS_V1_WITH_STORE,
            modules={
                "modules/aaa/module.yaml": _module_yaml(
                    "aaa",
                    actions=[
                        {"id": "z_action", "entitlement_gate": "missing.zzz"},
                        {"id": "a_action", "entitlement_gate": "missing.aaa"},
                    ],
                ),
                "modules/bbb/module.yaml": _module_yaml(
                    "bbb",
                    actions=[{"id": "b_action", "entitlement_gate": "missing.bbb"}],
                ),
            },
        )
        errors = _scan_entitlement_gate_capability_alignment(bundle)
        assert len(errors) == 3
        # Errors must be sorted — second run must produce identical order
        second_run = _scan_entitlement_gate_capability_alignment(bundle)
        assert errors == second_run

    def test_multiple_modules_multiple_failures_each_named(self) -> None:
        """Each failure names its own module and action, not a combined list."""
        bundle = _bundle(
            subs=_SUBS_V1_WITH_STORE,
            modules={
                "modules/tasks/module.yaml": _module_yaml(
                    "tasks",
                    actions=[{"id": "export", "entitlement_gate": "tasks.export"}],
                ),
                "modules/billing/module.yaml": _module_yaml(
                    "billing",
                    actions=[{"id": "refund", "entitlement_gate": "billing.refund"}],
                ),
            },
        )
        errors = _scan_entitlement_gate_capability_alignment(bundle)
        assert len(errors) == 2
        module_paths = {e.split(":")[0] for e in errors}
        assert "modules/tasks/module.yaml" in module_paths
        assert "modules/billing/module.yaml" in module_paths

    def test_v2_subscriptions_unknown_gate_fails(self) -> None:
        bundle = _bundle(
            subs=_SUBS_V2_WITH_STORE,
            modules={
                "modules/tasks/module.yaml": _module_yaml(
                    "tasks",
                    actions=[{"id": "export", "entitlement_gate": "tasks.export"}],
                )
            },
        )
        errors = _scan_entitlement_gate_capability_alignment(bundle)
        assert len(errors) == 1
        assert "tasks.export" in errors[0]


class TestEntitlementGateClosurePublicScanner:
    """Prove the public generated-bundle scanner sees the same closure errors."""

    def test_scan_generated_bundle_blocks_unknown_gate_without_assignment_store(self) -> None:
        bundle = _bundle(
            subs=_SUBS_V1_NO_STORE,
            modules={
                "modules/tasks/module.yaml": _module_yaml(
                    "tasks",
                    actions=[{"id": "create_task", "entitlement_gate": "custom.dynamic.cap"}],
                )
            },
        )

        errors = scan_generated_bundle(bundle)

        assert any("custom.dynamic.cap" in error for error in errors)
        assert any("modules/tasks/module.yaml" in error for error in errors)

    def test_scan_generated_bundle_blocks_malformed_subscriptions(self) -> None:
        bundle = _bundle(
            subs=": }{not valid yaml",
            modules={
                "modules/tasks/module.yaml": _module_yaml(
                    "tasks",
                    actions=[{"id": "create_task", "entitlement_gate": "tasks.create"}],
                )
            },
        )

        errors = scan_generated_bundle(bundle)

        assert any("config/subscriptions.yaml" in error for error in errors)
        assert any("must be valid YAML" in error for error in errors)


class TestConfiguredEntitlementRuntimeAlignment:
    """Prove scanner classification matches ConfiguredEntitlementAdapter behavior."""

    @pytest.mark.asyncio
    async def test_configured_adapter_without_assignment_store_uses_default_plan(self) -> None:
        config = SubscriptionsConfig.model_validate(
            {
                "schema_version": "mozaiks.subscriptions.v1",
                "label": "Task App",
                "default_plan_id": "free",
                "plans": [
                    {
                        "plan_id": "free",
                        "label": "Free",
                        "capabilities": ["tasks.create"],
                    }
                ],
            }
        )
        adapter = ConfiguredEntitlementAdapter(config=config)

        granted = await adapter.check("tasks.create", app_id="app-1")
        denied = await adapter.check("custom.dynamic.cap", app_id="app-1")

        assert granted.granted is True
        assert granted.reason == "default_plan"
        assert denied.granted is False
        assert denied.reason == "no_grant"


# ---------------------------------------------------------------------------
# Integration: full SubscriptionsConfig duplicate-capability validation
# ---------------------------------------------------------------------------


class TestSubscriptionsConfigDuplicateCapabilityValidation:
    """Prove that the loader rejects duplicate capabilities end-to-end."""

    def test_v1_plan_with_duplicate_capabilities_fails_load(self) -> None:
        raw = {
            "schema_version": "mozaiks.subscriptions.v1",
            "label": "Test",
            "default_plan_id": "pro",
            "plans": [
                {
                    "plan_id": "pro",
                    "label": "Pro",
                    "capabilities": ["tasks.create", "tasks.delete", "tasks.create"],
                }
            ],
        }
        with pytest.raises(ValidationError, match="duplicate capability_ids"):
            SubscriptionsConfig.model_validate(raw)

    def test_v1_plan_without_duplicates_loads_successfully(self) -> None:
        raw = {
            "schema_version": "mozaiks.subscriptions.v1",
            "label": "Test",
            "default_plan_id": "pro",
            "assignment_store": {"data_alias": "billing.subscriptions"},
            "plans": [
                {
                    "plan_id": "free",
                    "label": "Free",
                    "capabilities": [],
                },
                {
                    "plan_id": "pro",
                    "label": "Pro",
                    "capabilities": ["tasks.create", "tasks.delete"],
                },
            ],
        }
        config = SubscriptionsConfig.model_validate(raw)
        pro_plan = next(p for p in config.plans if p.plan_id == "pro")
        assert pro_plan.capabilities == ["tasks.create", "tasks.delete"]

    def test_v2_product_plan_with_duplicate_capabilities_fails_load(self) -> None:
        raw = {
            "schema_version": "mozaiks.subscriptions.v2",
            "label": "Test",
            "default_product_id": "core",
            "products": [
                {
                    "product_id": "core",
                    "label": "Core",
                    "default_plan_id": "pro",
                    "plans": [
                        {
                            "plan_id": "pro",
                            "label": "Pro",
                            "capabilities": ["tasks.create", "tasks.create"],
                        }
                    ],
                }
            ],
        }
        with pytest.raises(ValidationError, match="duplicate capability_ids"):
            SubscriptionsConfig.model_validate(raw)

    def test_empty_grants_do_not_raise(self) -> None:
        raw = {
            "schema_version": "mozaiks.subscriptions.v1",
            "label": "App",
            "default_plan_id": "free",
            "plans": [{"plan_id": "free", "label": "Free", "capabilities": []}],
        }
        config = SubscriptionsConfig.model_validate(raw)
        assert config.plans[0].capabilities == []
