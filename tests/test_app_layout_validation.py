"""Tests for generated-file layout validation through mozaiks.app_layout.v2."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from factory_app.workflows.AppGenerator.tools.generated_bundle_scanner import scan_generated_bundle
from mozaiksai.core.runtime.app.layout_registry import (
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
    Requirement,
    RuntimeConsumerIdentifier,
    SecurityClass,
    ValidatorIdentifier,
    default_app_layout_registry,
    validate_registered_path,
)
from mozaiksai.core.runtime.app.layout_validation import (
    LayoutClassificationStatus,
    LayoutDiagnosticCode,
    classify_layout_path,
    layout_extensions_from_selected_packs,
    validate_file_map_layout,
)
from mozaiksai.core.workflow.assignment_kinds import AssignmentKind

_FIXTURE_APP = Path("web_shell/playwright/fixtures/generated-app/app")


def _fixture_files() -> dict[str, str]:
    return {
        path.relative_to(_FIXTURE_APP).as_posix(): path.read_text(encoding="utf-8")
        for path in _FIXTURE_APP.rglob("*")
        if path.is_file()
    }


def _family(template: str, *, kind: ArtifactKind = ArtifactKind.APP_CONFIG) -> ArtifactFamily:
    return ArtifactFamily(
        kind=kind,
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


def _by_path(report):
    return {item.normalized_path: item for item in report.classifications}


def test_known_generated_fixture_paths_classify_uniquely() -> None:
    report = validate_file_map_layout(_fixture_files())

    assert report.passed
    assert len(report.classifications) == len(_fixture_files())
    assert all(item.status is LayoutClassificationStatus.REGISTERED for item in report.classifications)

    classified = _by_path(report)
    assert classified["app.json"].artifact_kind == ArtifactKind.APP_MANIFEST.value
    assert classified["brand/theme_config.json"].artifact_kind == ArtifactKind.APP_BRAND_THEME.value
    assert classified["config/shell.json"].artifact_kind == ArtifactKind.APP_SHELL_CONFIG.value
    assert classified["ui/index.js"].artifact_kind == ArtifactKind.APP_UI_EXTENSION_BARREL.value
    assert classified["ui/lib/moduleApi.js"].artifact_kind == ArtifactKind.APP_UI_MODULE_API.value
    assert classified["ui/pages/tickets.yaml"].artifact_kind == ArtifactKind.APP_UI_PAGE_SCHEMA.value
    assert classified["ui/pages/custom/GatedFeaturePage.jsx"].artifact_kind == ArtifactKind.APP_UI_CUSTOM_ROUTE.value


def test_report_serialization_round_trip_is_strict() -> None:
    report = validate_file_map_layout({"app.json": "{}"})
    restored = type(report).model_validate(report.model_dump(mode="json"))

    assert restored == report

    payload = report.model_dump(mode="json")
    payload["generated_at"] = "2026-08-17T00:00:00Z"
    with pytest.raises(ValidationError, match="extra"):
        type(report).model_validate(payload)


def test_prohibited_control_plane_and_credential_paths_fail() -> None:
    report = validate_file_map_layout(
        {
            "control_plane/refinement.yaml": "",
            "config/secrets.yaml": "",
            "services/security/vault.py": "",
        }
    )

    assert not report.passed
    assert [diagnostic.code for diagnostic in report.diagnostics] == [
        LayoutDiagnosticCode.PROHIBITED_PATH,
        LayoutDiagnosticCode.PROHIBITED_PATH,
        LayoutDiagnosticCode.PROHIBITED_PATH,
    ]
    assert [diagnostic.normalized_path for diagnostic in report.diagnostics] == [
        "config/secrets.yaml",
        "control_plane/refinement.yaml",
        "services/security/vault.py",
    ]


def test_unknown_runtime_affecting_app_bundle_paths_fail_closed() -> None:
    report = validate_file_map_layout({"server.py": "print('not a registered app artifact')"})

    assert not report.passed
    assert report.diagnostics[0].code is LayoutDiagnosticCode.UNKNOWN_PATH
    assert "outside the canonical app planes" in report.diagnostics[0].message


def test_repo_support_files_are_ignored_not_interpreted_as_bundle_files() -> None:
    report = validate_file_map_layout(
        {
            "docs/readme.md": "",
            "tests/test_generated_app.py": "",
            "scripts/build.ps1": "",
            ".claude/settings.local.json": "",
            ".github/ISSUE_TEMPLATE/bug.md": "",
        }
    )

    assert report.passed
    assert all(
        item.status is LayoutClassificationStatus.IGNORED_REPO_SUPPORT
        for item in report.classifications
    )


def test_deployment_workflow_is_registered_even_under_github_repo_support_prefix() -> None:
    report = validate_file_map_layout({".github/workflows/deploy.yml": ""})

    assert report.passed
    classification = report.classifications[0]
    assert classification.scope is PathScope.DEPLOYMENT_DERIVED
    assert classification.artifact_kind == ArtifactKind.APP_DEPLOYMENT_ARTIFACT.value


def test_generated_staging_and_deployment_files_use_distinct_scopes() -> None:
    report = validate_file_map_layout(
        {
            "Dockerfile": "",
            "deployment.manifest.json": "",
            "generated/apps/customer/build-001/app/app.json": "",
        }
    )

    classified = _by_path(report)
    assert classified["Dockerfile"].scope is PathScope.DEPLOYMENT_DERIVED
    assert classified["deployment.manifest.json"].scope is PathScope.DEPLOYMENT_DERIVED
    assert classified["generated/apps/customer/build-001/app/app.json"].scope is PathScope.GENERATED_STAGING


def test_selected_extensions_classify_only_when_passed_explicitly() -> None:
    extension = LayoutExtension(slot=ExtensionSlot.MANAGED_CAPABILITY_CONFIG, pack_id="payments")

    absent = validate_file_map_layout({"config/integrations/payments.yaml": ""})
    present = validate_file_map_layout(
        {"config/integrations/payments.yaml": ""},
        selected_extensions=(extension,),
    )

    assert not absent.passed
    assert absent.diagnostics[0].code is LayoutDiagnosticCode.UNKNOWN_PATH
    assert present.passed
    classification = present.classifications[0]
    assert classification.owner == LayoutOwner.REGISTERED_EXTENSION.value
    assert classification.extension_selection == "managed_capability_config:payments"


def test_selected_pack_projection_preserves_zero_artifact_absence() -> None:
    extensions = layout_extensions_from_selected_packs(
        [{"id": "mozaikspay", "capability_source": "managed_capability"}]
    )
    report = validate_file_map_layout({"app.json": "{}"}, selected_extensions=extensions)

    assert report.passed
    assert len(report.classifications) == 1
    assert report.classifications[0].artifact_kind == ArtifactKind.APP_MANIFEST.value


def test_exact_capability_pack_outputs_classify_under_their_own_scopes() -> None:
    extensions = (
        LayoutExtension(
            slot=ExtensionSlot.CAPABILITY_PACK_OUTPUT,
            pack_id="operator_readiness",
            path="config/operator_readiness.yaml",
        ),
        LayoutExtension(
            slot=ExtensionSlot.CAPABILITY_PACK_OUTPUT,
            pack_id="operator_readiness",
            path="docs/operations/operator-readiness.md",
        ),
        LayoutExtension(
            slot=ExtensionSlot.CAPABILITY_PACK_OUTPUT,
            pack_id="operator_readiness",
            path="scripts/check_operator_readiness_local.ps1",
        ),
    )

    report = validate_file_map_layout(
        {
            "config/operator_readiness.yaml": "",
            "docs/operations/operator-readiness.md": "",
            "scripts/check_operator_readiness_local.ps1": "",
        },
        selected_extensions=extensions,
    )
    classified = _by_path(report)

    assert report.passed
    assert classified["config/operator_readiness.yaml"].scope is PathScope.APP_BUNDLE_ROOT
    assert classified["docs/operations/operator-readiness.md"].scope is PathScope.WORKSPACE_ROOT
    assert classified["scripts/check_operator_readiness_local.ps1"].scope is PathScope.WORKSPACE_ROOT
    assert classified["scripts/check_operator_readiness_local.ps1"].security_class == (
        SecurityClass.EXECUTABLE_STUB.value
    )
    assert all(
        item.extension_selection == "capability_pack_output:operator_readiness"
        for item in report.classifications
    )


def test_capability_pack_output_extension_requires_exact_safe_path() -> None:
    with pytest.raises(ValidationError, match="exact path"):
        LayoutExtension(slot=ExtensionSlot.CAPABILITY_PACK_OUTPUT, pack_id="operator_readiness")

    with pytest.raises(ValidationError, match="path is only valid"):
        LayoutExtension(
            slot=ExtensionSlot.MANAGED_CAPABILITY_CONFIG,
            pack_id="operator_readiness",
            path="config/operator_readiness.yaml",
        )

    with pytest.raises(ValidationError):
        LayoutExtension(
            slot=ExtensionSlot.CAPABILITY_PACK_OUTPUT,
            pack_id="operator_readiness",
            path="../config/operator_readiness.yaml",
        )


def test_assignment_ownership_cannot_broaden_path_authority() -> None:
    with pytest.raises(ValueError, match="not owned"):
        validate_registered_path(
            "modules/orders/backend/service.py",
            AssignmentKind.MODULE_CONTRACT,
            PathScope.APP_BUNDLE_ROOT,
        )

    classification = classify_layout_path("modules/orders/backend/service.py")
    assert AssignmentKind.MODULE_CONTRACT.value not in classification.assignment_kinds


def test_scopes_cannot_be_swapped() -> None:
    assert classify_layout_path("Dockerfile").scope is PathScope.DEPLOYMENT_DERIVED
    assert classify_layout_path("app/app.json").status is LayoutClassificationStatus.UNKNOWN

    with pytest.raises(ValueError, match="not registered"):
        default_app_layout_registry().match_path("Dockerfile", PathScope.APP_BUNDLE_ROOT)


def test_unicode_and_case_normalization_is_stable() -> None:
    report = validate_file_map_layout(
        {
            "ui/pages/café.yaml": "",
            "ui/pages/café.yaml": "",
            "UI/pages/home.yaml": "",
            "ui/pages/home.yaml": "",
        }
    )
    classified = _by_path(report)

    assert classified["ui/pages/café.yaml"].status is LayoutClassificationStatus.UNKNOWN
    assert classified["UI/pages/home.yaml"].status is LayoutClassificationStatus.UNKNOWN
    assert classified["ui/pages/home.yaml"].status is LayoutClassificationStatus.REGISTERED
    assert [diagnostic.normalized_path for diagnostic in report.diagnostics] == [
        "UI/pages/home.yaml",
        "ui/pages/café.yaml",
        "ui/pages/café.yaml",
    ]


def test_ambiguous_registry_matches_are_diagnostics_not_success() -> None:
    left = _family("config/{pack_id}.json")
    right = _family("config/{extension_id}.json", kind=ArtifactKind.APP_TARGETS_CONFIG)
    registry = AppLayoutRegistry.model_construct(
        families=(left, right),
        registry_digest="bypassed-for-ambiguity-facade-test",
    )

    report = validate_file_map_layout({"config/example.json": "{}"}, registry=registry)

    assert not report.passed
    assert report.diagnostics[0].code is LayoutDiagnosticCode.AMBIGUOUS_PATH
    assert "ambiguous" in report.diagnostics[0].message


def test_scanner_fails_undeclared_repo_support_and_unknown_runtime_files() -> None:
    # An undeclared repository-support file never sails through, packs or not.
    undeclared = scan_generated_bundle({"docs/readme.md": "", "app.json": "{}"})
    assert any("undeclared repository-support pack outputs" in error for error in undeclared)

    errors = scan_generated_bundle({"docs/readme.md": "", "server.py": ""})
    assert any("server.py" in error for error in errors)
    assert any("outside the canonical app planes" in error for error in errors)


def test_scanner_extension_selection_is_explicit_not_filename_inferred() -> None:
    path = "config/integrations/payments.yaml"
    extension = LayoutExtension(slot=ExtensionSlot.MANAGED_CAPABILITY_CONFIG, pack_id="payments")

    # Explicit selection classifies through the validation facade; the scanner
    # derives extension authority only from selected pack contracts and exposes
    # no caller-supplied extension bypass.
    import inspect

    assert "layout_extensions" not in inspect.signature(scan_generated_bundle).parameters
    explicit = validate_file_map_layout({path: "{}"}, selected_extensions=(extension,))
    assert explicit.passed

    errors = scan_generated_bundle({path: "{}"})
    assert any(path in error for error in errors)
    assert any("outside the canonical app planes" in error for error in errors)


def test_scanner_requires_repo_support_pack_outputs_to_be_declared_when_packs_selected() -> None:
    pack = {
        "id": "operator_readiness",
        "capability_source": "config_file",
        "pack_source_path": "factory_app/build_context/operator_readiness",
    }

    assert (
        scan_generated_bundle(
            {
                "app.json": "{}",
                "docs/operations/operator-readiness.md": "",
            },
            capability_packs=[pack],
        )
        == []
    )

    errors = scan_generated_bundle(
        {
            "app.json": "{}",
            "docs/operations/undeclared.md": "",
        },
        capability_packs=[pack],
    )
    assert any("undeclared repository-support pack outputs" in error for error in errors)


# ---------------------------------------------------------------------------
# Review regressions — each defect below was reproduced before fixing.
# ---------------------------------------------------------------------------


def test_pack_output_cannot_shadow_prohibited_families() -> None:
    """An extension literal must never out-rank a prohibited family."""
    with pytest.raises(ValidationError, match="not a permitted pack output lane"):
        LayoutExtension(
            slot=ExtensionSlot.CAPABILITY_PACK_OUTPUT,
            pack_id="evil",
            path="services/data/schema.py",
        )

    # And prohibition wins at match time over any more-literal family.
    report = validate_file_map_layout({"services/data/schema.py": "x"})
    assert not report.passed
    assert report.diagnostics[0].code is LayoutDiagnosticCode.PROHIBITED_PATH


@pytest.mark.parametrize(
    "path",
    [
        ".claude/settings.local.json",
        ".github/workflows/deploy.yml",
        "generated/apps/a/b/app/app.json",
        "Dockerfile",
        "deployment.manifest.json",
        "config/secrets.yaml",
        "security/secrets.yaml",
        "tests/test_x.py",
        "control_plane/routing.yaml",
        "config/api_keys.json",
        "certs/server.pem",
        ".env.production.example",
        "ui/pages/{page_id}.yaml",
    ],
)
def test_pack_output_forbidden_lanes_fail_closed(path: str) -> None:
    with pytest.raises(ValidationError):
        LayoutExtension(
            slot=ExtensionSlot.CAPABILITY_PACK_OUTPUT,
            pack_id="sneaky",
            path=path,
        )


def test_extension_registration_failure_is_diagnostic_not_crash() -> None:
    """A pack output duplicating a core literal fails closed with a diagnostic."""
    duplicate = LayoutExtension(
        slot=ExtensionSlot.CAPABILITY_PACK_OUTPUT,
        pack_id="dup",
        path="services/config.py",
    )
    report = validate_file_map_layout(
        {"services/config.py": "x"}, selected_extensions=(duplicate,)
    )
    assert not report.passed
    assert report.diagnostics[0].code is (
        LayoutDiagnosticCode.INVALID_EXTENSION_REGISTRATION
    )


def test_multi_scope_acceptance_is_ambiguity_not_first_scope_wins() -> None:
    """A path accepted by more than one candidate scope fails closed."""
    workspace = LayoutExtension(
        slot=ExtensionSlot.CAPABILITY_PACK_OUTPUT,
        pack_id="readiness",
        path="docs/operations/guide.md",
    )
    base = validate_file_map_layout(
        {"docs/operations/guide.md": ""}, selected_extensions=(workspace,)
    )
    assert base.passed  # single-scope acceptance stays valid

    from mozaiksai.core.runtime.app.layout_registry import build_app_layout_registry

    registry = build_app_layout_registry((workspace,))
    forged = AppLayoutRegistry.model_construct(
        families=(*registry.families, _family("docs/operations/guide.md")),
        registry_digest="bypassed-for-multi-scope-test",
    )
    report = validate_file_map_layout(
        {"docs/operations/guide.md": ""},
        selected_extensions=(workspace,),
        registry=forged,
    )
    assert not report.passed
    assert report.diagnostics[0].code is LayoutDiagnosticCode.AMBIGUOUS_PATH
    assert "more than one scope" in report.diagnostics[0].message


def test_duplicate_pack_output_claims_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    from factory_app.workflows.AppGenerator.tools import generated_bundle_scanner as scanner
    from factory_app.workflows.AppGenerator.tools.resolve_managed_capability_templates import (
        ManagedCapabilityTemplateError,
    )

    def _fake_declared(packs, **_kwargs):
        return frozenset({"scripts/shared_output.ps1"})

    monkeypatch.setattr(scanner, "resolve_declared_pack_output_paths", _fake_declared)

    packs = [
        {"id": "pack_one", "capability_source": "config_file"},
        {"id": "pack_two", "capability_source": "config_file"},
    ]
    with pytest.raises(ManagedCapabilityTemplateError, match="duplicate pack output"):
        scanner._layout_extensions_for_selected_packs(packs)

    errors = scanner.scan_generated_bundle({"app.json": "{}"}, capability_packs=packs)
    assert any("duplicate pack output claims" in error for error in errors)


def test_declared_workspace_script_content_is_still_secret_scanned() -> None:
    pack = {
        "id": "operator_readiness",
        "capability_source": "config_file",
        "pack_source_path": "factory_app/build_context/operator_readiness",
    }
    leaked = "$env:PAYMENT_SECRET = 'provider_live_0123456789abcdef'\n"
    errors = scan_generated_bundle(
        {
            "app.json": "{}",
            "scripts/check_operator_readiness_local.ps1": leaked,
        },
        capability_packs=[pack],
    )
    assert any(
        "scripts/check_operator_readiness_local.ps1" in error
        and "raw provider secret" in error
        for error in errors
    )


def test_custom_route_yaml_is_not_a_registered_family() -> None:
    """ui/pages/custom/*.yaml has no emitter or runtime consumer — must fail."""
    report = validate_file_map_layout({"ui/pages/custom/thing.yaml": ""})
    assert not report.passed
    assert report.diagnostics[0].code is LayoutDiagnosticCode.UNKNOWN_PATH


def test_auth_adapter_family_matches_rendered_template_output() -> None:
    """ui/auth/authAdapter.js is authentic: the webapp_builder template exists."""
    template = Path(
        "factory_app/build_context/webapp_builder/templates/ui/auth/authAdapter.js"
    )
    assert template.is_file()
    classification = classify_layout_path("ui/auth/authAdapter.js")
    assert classification.status is LayoutClassificationStatus.REGISTERED
    assert classification.artifact_kind == ArtifactKind.APP_UI_AUTH_ADAPTER.value
