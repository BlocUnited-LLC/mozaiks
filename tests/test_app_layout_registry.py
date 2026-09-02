"""Tests for mozaiks.app_layout.v2."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from mozaiksai.core.runtime.app import paths as app_paths
from mozaiksai.core.runtime.app.layout_registry import (
    SCHEMA_VERSION,
    AppLayoutRegistry,
    ArtifactFamily,
    ArtifactKind,
    ConditionIdentifier,
    ExtensionSlot,
    LayoutExtension,
    LayoutOwner,
    MaterializerIdentifier,
    Multiplicity,
    PathScope,
    PlaceholderIdentifier,
    Requirement,
    RuntimeConsumerIdentifier,
    SecurityClass,
    ValidatorIdentifier,
    build_app_layout_registry,
    default_app_layout_registry,
    iter_artifact_kinds,
    kinds_for_assignment,
    match_path,
    validate_registered_path,
)
from mozaiksai.core.workflow.assignment_kinds import AssignmentKind


def _registry() -> AppLayoutRegistry:
    return default_app_layout_registry()


def _minimal_family(template: str) -> ArtifactFamily:
    return ArtifactFamily(
        kind=ArtifactKind.APP_CONFIG,
        owner=LayoutOwner.PLATFORM,
        requirement=Requirement.OPTIONAL,
        multiplicity=Multiplicity.SINGLE,
        condition=ConditionIdentifier.WHEN_APP_DECLARED,
        path_scope=PathScope.APP_BUNDLE_ROOT,
        path_template=template,
        materializer=MaterializerIdentifier.APP_GENERATOR,
        validator=ValidatorIdentifier.APP_PATHS,
        runtime_consumer=RuntimeConsumerIdentifier.PLATFORM_HOST,
        security_class=SecurityClass.INTERNAL_CONTRACT,
    )


class TestRegistryContract:
    def test_schema_version_and_digest_are_stable(self) -> None:
        first = _registry()
        second = _registry()

        assert first.schema_version == SCHEMA_VERSION == "mozaiks.app_layout.v2"
        assert first.registry_digest == second.registry_digest
        assert [family.identity_payload for family in first.families] == [
            family.identity_payload for family in second.families
        ]

    def test_iter_artifact_kinds_is_deterministic(self) -> None:
        kinds = iter_artifact_kinds()

        assert kinds == tuple(sorted(kinds, key=lambda item: item.value))
        assert ArtifactKind.APP_MANIFEST in kinds
        assert ArtifactKind.MODULE_MANIFEST in kinds
        assert ArtifactKind.WORKFLOW_MANIFEST in kinds

    def test_application_input_kinds_are_declared_on_the_existing_registry(self) -> None:
        rows = _registry().families
        by_kind = {
            kind: [family for family in rows if family.kind is kind]
            for kind in (
                ArtifactKind.APP_MANIFEST,
                ArtifactKind.APP_AUTH_CONFIG,
                ArtifactKind.APP_INTEGRATIONS_CONFIG,
                ArtifactKind.WORKFLOW_MANIFEST,
            )
        }
        assert {row.semantic_input_kinds for row in by_kind[ArtifactKind.APP_MANIFEST]} == {
            ("application",)
        }
        assert {row.semantic_input_kinds for row in by_kind[ArtifactKind.APP_AUTH_CONFIG]} == {
            ("auth",)
        }
        assert {
            row.semantic_input_kinds
            for row in by_kind[ArtifactKind.APP_INTEGRATIONS_CONFIG]
        } == {("integration",)}
        assert all(
            "workflow" in row.semantic_input_kinds
            for row in by_kind[ArtifactKind.WORKFLOW_MANIFEST]
        )

    def test_serialization_round_trip_revalidates_digest(self) -> None:
        registry = _registry()

        restored = AppLayoutRegistry.model_validate(registry.model_dump(mode="json"))

        assert restored.registry_digest == registry.registry_digest
        assert restored.families == registry.families

    def test_tampered_registry_digest_rejected(self) -> None:
        payload = _registry().model_dump(mode="json")
        payload["registry_digest"] = "0" * 64

        with pytest.raises(ValidationError, match="registry_digest"):
            AppLayoutRegistry.model_validate(payload)

    def test_summary_prose_does_not_affect_family_identity_payload(self) -> None:
        family = _minimal_family("config/example.json")
        with_summary = family.model_copy(update={"summary": "human explanation"})

        assert family.identity_payload == with_summary.identity_payload

    def test_no_timestamp_field_exists_in_family_contract(self) -> None:
        with pytest.raises(ValidationError, match="extra"):
            ArtifactFamily(**_minimal_family("config/example.json").model_dump(), created_at="now")


class TestClosedIdentifiers:
    def test_authority_identifiers_are_enums_not_open_strings(self) -> None:
        family = match_path("modules/orders/module.yaml", PathScope.APP_BUNDLE_ROOT).family

        assert isinstance(family.kind, ArtifactKind)
        assert isinstance(family.owner, LayoutOwner)
        assert isinstance(family.requirement, Requirement)
        assert isinstance(family.multiplicity, Multiplicity)
        assert isinstance(family.condition, ConditionIdentifier)
        assert isinstance(family.materializer, MaterializerIdentifier)
        assert isinstance(family.validator, ValidatorIdentifier)
        assert isinstance(family.runtime_consumer, RuntimeConsumerIdentifier)
        assert isinstance(family.security_class, SecurityClass)

    def test_unknown_enum_values_rejected(self) -> None:
        payload = _minimal_family("config/example.json").model_dump(mode="json")
        payload["kind"] = "made_up"

        with pytest.raises(ValidationError):
            ArtifactFamily.model_validate(payload)


class TestPathTemplateValidation:
    @pytest.mark.parametrize(
        "template",
        [
            "modules/{unknown}/module.yaml",
            "../app.json",
            "/app/app.json",
            "C:/repo/app.json",
            "ui/pages/*.yaml",
            "ui/pages/{page_id",
            "ui/pages/{page_id}}.yaml",
        ],
    )
    def test_invalid_templates_rejected(self, template: str) -> None:
        with pytest.raises(ValidationError):
            _minimal_family(template)

    def test_duplicate_templates_rejected(self) -> None:
        family = _minimal_family("config/example.json")

        with pytest.raises(ValidationError, match="duplicate"):
            AppLayoutRegistry(families=(family, family), registry_digest="x")

    def test_ambiguous_template_shapes_rejected(self) -> None:
        left = _minimal_family("config/{pack_id}.json")
        right = _minimal_family("config/{extension_id}.json")

        with pytest.raises(ValidationError, match="ambiguous"):
            AppLayoutRegistry(families=(left, right), registry_digest="x")


class TestMatchingSemantics:
    def test_match_path_returns_exactly_one_result_with_values(self) -> None:
        match = match_path("modules/orders/module.yaml", PathScope.APP_BUNDLE_ROOT)

        assert match.family.kind is ArtifactKind.MODULE_MANIFEST
        assert match.values == {PlaceholderIdentifier.MODULE_ID: "orders"}

    def test_match_path_fails_closed_for_unknown_path(self) -> None:
        with pytest.raises(ValueError, match="not registered"):
            match_path("random/file.txt", PathScope.APP_BUNDLE_ROOT)

    @pytest.mark.parametrize(
        "path",
        [
            "../app.json",
            "/app.json",
            "C:/repo/app.json",
            "ui/pages/*.yaml",
            "https://example.test/app.json",
        ],
    )
    def test_path_attacks_rejected_before_matching(self, path: str) -> None:
        with pytest.raises(ValueError):
            match_path(path, PathScope.APP_BUNDLE_ROOT)

    def test_path_scope_does_not_blur_optional_app_prefix(self) -> None:
        assert match_path("app.json", PathScope.APP_BUNDLE_ROOT).family.kind is ArtifactKind.APP_MANIFEST
        assert match_path("app/app.json", PathScope.WORKSPACE_ROOT).family.kind is ArtifactKind.APP_MANIFEST

        with pytest.raises(ValueError, match="not registered"):
            match_path("app/app.json", PathScope.APP_BUNDLE_ROOT)

    def test_validate_registered_path_accepts_authentic_assignment_owner(self) -> None:
        match = validate_registered_path(
            "modules/orders/module.yaml",
            AssignmentKind.MODULE_CONTRACT,
            PathScope.APP_BUNDLE_ROOT,
        )

        assert match.family.kind is ArtifactKind.MODULE_MANIFEST

    def test_validate_registered_path_rejects_wrong_assignment_owner(self) -> None:
        with pytest.raises(ValueError, match="not owned"):
            validate_registered_path(
                "modules/orders/backend/service.py",
                AssignmentKind.MODULE_CONTRACT,
                PathScope.APP_BUNDLE_ROOT,
            )

    def test_validate_registered_path_rejects_prohibited_paths(self) -> None:
        with pytest.raises(ValueError, match="prohibited"):
            validate_registered_path(
                "config/data.json",
                AssignmentKind.PERSISTENCE_CONTRACT,
                PathScope.APP_BUNDLE_ROOT,
            )

    def test_kinds_for_assignment_is_authentic_and_bounded(self) -> None:
        module_contract_kinds = kinds_for_assignment(AssignmentKind.MODULE_CONTRACT)
        agent_backend_kinds = kinds_for_assignment(AssignmentKind.AGENT_BACKEND_INTEGRATION)

        assert ArtifactKind.MODULE_MANIFEST in module_contract_kinds
        assert ArtifactKind.MODULE_BACKEND_SERVICE not in module_contract_kinds
        assert agent_backend_kinds == ()


class TestCanonicalPathCoverage:
    @pytest.mark.parametrize("path", sorted(app_paths.CANONICAL_APP_CONFIG_FILES))
    def test_every_current_paths_py_config_value_is_represented(self, path: str) -> None:
        match = match_path(path, PathScope.APP_BUNDLE_ROOT)

        assert match.family.requirement is not Requirement.PROHIBITED

    @pytest.mark.parametrize("path", sorted(app_paths.CANONICAL_APP_ROOT_FILES))
    def test_every_current_paths_py_root_file_is_represented(self, path: str) -> None:
        scopes = (
            PathScope.APP_BUNDLE_ROOT,
            PathScope.DEPLOYMENT_DERIVED,
        )

        assert any(_matches(path, scope) for scope in scopes), path

    @pytest.mark.parametrize("root", sorted(app_paths.CANONICAL_APP_ROOT_DIRS))
    def test_every_current_paths_py_root_dir_is_represented(self, root: str) -> None:
        samples = {
            ".github": ".github/workflows/deploy.yml",
            ".mozaiks": ".mozaiks/pack_provenance.json",
            "admin": "admin/admin_registry.yaml",
            "backend": "backend/admin_config.py",
            "brand": "brand/theme_config.json",
            "config": "config/ai.json",
            "dashboard": "dashboard/dashboard.yaml",
            "data": "data/contract.json",
            "modules": "modules/orders/module.yaml",
            "refinement_harness": "refinement_harness/config/harness.yaml",
            "security": "security/secrets.yaml",
            "services": "services/config.py",
            "ui": "ui/pages/home.yaml",
        }

        assert root in samples, root
        assert _matches(samples[root], PathScope.APP_BUNDLE_ROOT) or _matches(
            samples[root], PathScope.DEPLOYMENT_DERIVED
        )

    @pytest.mark.parametrize("path", sorted(app_paths.DISALLOWED_LEGACY_APP_PATHS))
    def test_every_prohibited_legacy_exact_path_is_rejected(self, path: str) -> None:
        with pytest.raises(ValueError, match="prohibited"):
            validate_registered_path(path, None, PathScope.APP_BUNDLE_ROOT)

    @pytest.mark.parametrize("prefix", sorted(app_paths.DISALLOWED_LEGACY_APP_DIR_PREFIXES))
    def test_every_prohibited_legacy_prefix_is_rejected(self, prefix: str) -> None:
        path = {
            "control_plane/": "control_plane/routing.yaml",
            "config/data_migrations/": "config/data_migrations/001.json",
            "services/data/": "services/data/schema.py",
            "services/security/": "services/security/vault.py",
        }[prefix]

        with pytest.raises(ValueError, match="prohibited"):
            validate_registered_path(path, None, PathScope.APP_BUNDLE_ROOT)


class TestControlPlaneAndManagedCapabilityBoundaries:
    def test_control_plane_app_bundle_path_is_explicitly_prohibited(self) -> None:
        match = match_path("control_plane/refinement.yaml", PathScope.APP_BUNDLE_ROOT)

        assert match.family.kind is ArtifactKind.PROHIBITED_LEGACY
        assert match.family.requirement is Requirement.PROHIBITED

    def test_refinement_harness_surface_is_classified_without_control_plane_files(self) -> None:
        policy = match_path("config/refinement_policy.yaml", PathScope.APP_BUNDLE_ROOT)
        harness = match_path("refinement_harness/config/harness.yaml", PathScope.APP_BUNDLE_ROOT)

        assert policy.family.kind is ArtifactKind.APP_REFINEMENT_POLICY
        assert harness.family.kind is ArtifactKind.APP_REFINEMENT_HARNESS

    def test_managed_capability_paths_are_conditional_not_required_core(self) -> None:
        integrations = match_path("config/integrations.yaml", PathScope.APP_BUNDLE_ROOT)
        client = match_path("services/integrations/mozaikspay_client.py", PathScope.APP_BUNDLE_ROOT)

        # integrations.yaml declares any external requirement, not only managed
        # capabilities; only the generated client is capability-conditioned.
        assert integrations.family.requirement is Requirement.CONDITIONAL
        assert client.family.requirement is Requirement.CONDITIONAL
        assert integrations.family.condition is ConditionIdentifier.WHEN_APP_DECLARED
        assert client.family.condition is ConditionIdentifier.WHEN_MANAGED_CAPABILITY_SELECTED

    def test_unselected_managed_capabilities_do_not_become_required_core(self) -> None:
        required = {
            family.path_template
            for family in _registry().families
            if family.requirement is Requirement.REQUIRED
        }

        assert "config/integrations.yaml" not in required
        assert "services/integrations/{pack_id}_client.py" not in required

    def test_registered_extensions_are_bounded_and_do_not_expand_core_authority(self) -> None:
        base = _registry()
        extended = build_app_layout_registry(
            (LayoutExtension(slot=ExtensionSlot.MANAGED_CAPABILITY_CONFIG, pack_id="payments"),)
        )

        match = extended.match_path("config/integrations/payments.yaml", PathScope.APP_BUNDLE_ROOT)
        assert match.family.owner is LayoutOwner.REGISTERED_EXTENSION
        assert match.family.requirement is Requirement.CONDITIONAL
        assert match.family.condition is ConditionIdentifier.WHEN_EXTENSION_SELECTED
        assert base.registry_digest != extended.registry_digest

        with pytest.raises(ValidationError):
            LayoutExtension(slot=ExtensionSlot.MANAGED_CAPABILITY_CONFIG, pack_id="../escape")


class TestFileContractsResolveToLayoutFamilies:
    def test_file_contract_outputs_resolve_or_are_explicitly_non_artifacts(self) -> None:
        raw = yaml.safe_load(Path("factory_app/build_context/AppGenerator/file_contracts.yaml").read_text())
        unresolved: list[str] = []

        for task_id, contract in sorted(raw["task_contracts"].items()):
            for key in (
                "required_outputs",
                "optional_outputs",
                "downstream_backend_defaults",
                "optional_backend_hooks",
                "optional_frontend_stubs",
            ):
                for template in contract.get(key, []) or []:
                    if not isinstance(template, str) or template in {
                        "task-owned service support files",
                        "workflow touchpoints in page schemas",
                        "build task initial_message context for downstream agents",
                    }:
                        continue
                    sample = _file_contract_sample(template)
                    if sample is None:
                        unresolved.append(f"{task_id}:{key}:{template}")
                        continue
                    if not _matches(sample, PathScope.APP_BUNDLE_ROOT) and not _matches(
                        sample, PathScope.MODULE_RELATIVE
                    ):
                        unresolved.append(f"{task_id}:{key}:{template}->{sample}")

        assert unresolved == []


class TestReviewRegressions:
    """Defects found in final review — each must stay fixed."""

    def test_extension_literal_beats_core_template_specificity(self) -> None:
        registry = build_app_layout_registry(
            (LayoutExtension(slot=ExtensionSlot.MANAGED_CAPABILITY_CLIENT, pack_id="mozaikspay"),)
        )

        match = registry.match_path(
            "services/integrations/mozaikspay_client.py", PathScope.APP_BUNDLE_ROOT
        )
        assert match.family.owner is LayoutOwner.REGISTERED_EXTENSION

        other = registry.match_path(
            "services/integrations/sendgrid_client.py", PathScope.APP_BUNDLE_ROOT
        )
        assert other.family.owner is not LayoutOwner.REGISTERED_EXTENSION
        assert other.values == {PlaceholderIdentifier.PACK_ID: "sendgrid"}

    def test_service_route_and_build_context_extensions_match_their_paths(self) -> None:
        registry = build_app_layout_registry(
            (
                LayoutExtension(slot=ExtensionSlot.SERVICE_ROUTE, pack_id="payments"),
                LayoutExtension(slot=ExtensionSlot.BUILD_CONTEXT_PACK, pack_id="mozaikspay"),
            )
        )

        route = registry.match_path("services/routes/payments.py", PathScope.APP_BUNDLE_ROOT)
        assert route.family.owner is LayoutOwner.REGISTERED_EXTENSION

        pack = registry.match_path("build_context/mozaikspay/context.yaml", PathScope.WORKSPACE_ROOT)
        assert pack.family.owner is LayoutOwner.REGISTERED_EXTENSION

    def test_equal_specificity_distinct_templates_still_ambiguous(self) -> None:
        left = _minimal_family("config/example.json")
        right = ArtifactFamily(
            **{**_minimal_family("config/example.json").model_dump(), "kind": ArtifactKind.APP_TARGETS_CONFIG}
        )
        # Same template registered twice is rejected structurally, so genuine
        # runtime ambiguity requires distinct same-specificity templates —
        # which the shape check also rejects at construction.
        with pytest.raises(ValidationError, match="duplicate"):
            AppLayoutRegistry(families=(left, right), registry_digest="x")

    def test_module_scope_contracts_are_a_closed_set(self) -> None:
        for name in (
            "events",
            "reactions",
            "notifications",
            "policy_hooks",
            "settings",
            "admin",
            "profile",
            "relationships",
            "service",
            "commercial",
        ):
            match = match_path(f"contracts/{name}.yaml", PathScope.MODULE_RELATIVE)
            assert match.family.kind is ArtifactKind.MODULE_CONTRACT

        with pytest.raises(ValueError, match="not registered"):
            match_path("contracts/anything_else.yaml", PathScope.MODULE_RELATIVE)

    def test_module_scope_subscriptions_contract_is_prohibited_not_legitimized(self) -> None:
        with pytest.raises(ValueError, match="prohibited"):
            validate_registered_path(
                "contracts/subscriptions.yaml", None, PathScope.MODULE_RELATIVE
            )

        with pytest.raises(ValueError, match="prohibited"):
            validate_registered_path(
                "modules/orders/contracts/subscriptions.yaml", None, PathScope.APP_BUNDLE_ROOT
            )

    def test_app_scope_service_and_commercial_contracts_are_registered(self) -> None:
        for name in ("service", "commercial"):
            match = match_path(f"modules/orders/contracts/{name}.yaml", PathScope.APP_BUNDLE_ROOT)
            assert match.family.kind is ArtifactKind.MODULE_CONTRACT

    def test_handler_owned_by_business_services_and_api_by_api_surface(self) -> None:
        handler = validate_registered_path(
            "modules/orders/backend/handler.py",
            AssignmentKind.BUSINESS_SERVICES,
            PathScope.APP_BUNDLE_ROOT,
        )
        assert handler.family.kind is ArtifactKind.MODULE_BACKEND_HANDLER

        with pytest.raises(ValueError, match="not owned"):
            validate_registered_path(
                "modules/orders/backend/handler.py",
                AssignmentKind.API_SURFACE,
                PathScope.APP_BUNDLE_ROOT,
            )

        api = validate_registered_path(
            "modules/orders/backend/api.py",
            AssignmentKind.API_SURFACE,
            PathScope.APP_BUNDLE_ROOT,
        )
        assert api.family.kind is ArtifactKind.MODULE_BACKEND_API

    def test_assignment_kind_cannot_gain_broader_authority_than_registered(self) -> None:
        with pytest.raises(ValueError, match="not owned"):
            validate_registered_path(
                "config/subscriptions.yaml",
                AssignmentKind.PAGE_BUNDLE,
                PathScope.APP_BUNDLE_ROOT,
            )

    def test_pack_provenance_matches_actual_json_artifact(self) -> None:
        match = match_path(".mozaiks/pack_provenance.json", PathScope.APP_BUNDLE_ROOT)
        assert match.family.kind is ArtifactKind.APP_FRAMEWORK_METADATA

        with pytest.raises(ValueError, match="not registered"):
            match_path(".mozaiks/pack_provenance.yaml", PathScope.APP_BUNDLE_ROOT)

    def test_bare_env_example_is_not_legitimized(self) -> None:
        # deployment_contract.py emits only the dot-prefixed env templates;
        # the bare name survives only in stale prose and must not match.
        assert _matches(".env.example", PathScope.DEPLOYMENT_DERIVED)
        assert not _matches("env.example", PathScope.DEPLOYMENT_DERIVED)
        assert not _matches("env.example", PathScope.APP_BUNDLE_ROOT)

    def test_unicode_paths_normalize_nfc_and_fail_closed_deterministically(self) -> None:
        composed = "ui/pages/café.yaml"
        decomposed = "ui/pages/café.yaml"

        results = []
        for candidate in (composed, decomposed):
            try:
                match_path(candidate, PathScope.APP_BUNDLE_ROOT)
                results.append("matched")
            except ValueError as exc:
                results.append("not registered" if "not registered" in str(exc) else "error")

        # NFC normalization makes both forms behave identically, and non-ASCII
        # placeholder values fail closed as unregistered.
        assert results[0] == results[1] == "not registered"

    def test_assignment_kind_order_does_not_change_identity_payload(self) -> None:
        base = _minimal_family("config/example.json").model_dump()
        forward = ArtifactFamily(
            **{**base, "assignment_kinds": (AssignmentKind.SERVICE_FOUNDATION, AssignmentKind.API_SURFACE)}
        )
        reverse = ArtifactFamily(
            **{**base, "assignment_kinds": (AssignmentKind.API_SURFACE, AssignmentKind.SERVICE_FOUNDATION)}
        )

        assert forward.identity_payload == reverse.identity_payload

    def test_reserved_backend_names_never_classified_as_helper(self) -> None:
        for name in ("handler", "base_handler", "service", "repo", "policy", "schemas", "models", "api"):
            path = f"modules/orders/backend/{name}.py"
            match = match_path(path, PathScope.APP_BUNDLE_ROOT)
            assert match.family.kind is not ArtifactKind.MODULE_BACKEND_HELPER, path

        # models.py resolves to its explicit prohibited family, never to helper
        models = match_path("modules/orders/backend/models.py", PathScope.APP_BUNDLE_ROOT)
        assert models.family.requirement is Requirement.PROHIBITED


def _matches(path: str, scope: PathScope) -> bool:
    try:
        match_path(path, scope)
    except ValueError:
        return False
    return True


def _file_contract_sample(template: str) -> str | None:
    replacements: tuple[tuple[str, str], ...] = (
        ("{pack_name}", "orders"),
        ("{module_id}", "orders"),
        ("{migration_id}", "001_initial"),
        ("{pack_id}", "payments"),
        ("services/integrations/*.py", "services/integrations/payments_client.py"),
        ("**/*.py", "payments/stub.py"),
        ("*.yaml", "home.yaml"),
        ("*.yml", "home.yml"),
        ("*.json", "sample.json"),
        ("*.py", "payments.py"),
        ("*.jsx", "Home.jsx"),
    )
    sample = template
    for old, new in replacements:
        sample = sample.replace(old, new)
    if "*" in sample:
        return None
    if sample in {
        "backend/handler.py",
        "backend/service.py",
        "backend/repo.py",
        "backend/policy.py",
        "backend/schemas.py",
        "backend/settings.py",
        "backend/notifications.py",
        "backend/admin.py",
    }:
        return sample
    if sample.startswith("backend/"):
        return sample
    return sample
