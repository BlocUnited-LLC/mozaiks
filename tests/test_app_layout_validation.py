"""Tests for generated-file layout validation through mozaiks.app_layout.v1."""

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


def test_scanner_ignores_repo_support_but_fails_unknown_runtime_files() -> None:
    assert scan_generated_bundle({"docs/readme.md": "", "app.json": "{}"}) == []

    errors = scan_generated_bundle({"docs/readme.md": "", "server.py": ""})
    assert any("server.py" in error for error in errors)
    assert any("outside the canonical app planes" in error for error in errors)


def test_scanner_extension_selection_is_explicit_not_filename_inferred() -> None:
    path = "config/integrations/payments.yaml"
    extension = LayoutExtension(slot=ExtensionSlot.MANAGED_CAPABILITY_CONFIG, pack_id="payments")

    assert scan_generated_bundle({path: "{}"}, layout_extensions=(extension,)) == []

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
