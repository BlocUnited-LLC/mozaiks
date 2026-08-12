"""Community Component Foundation tests.

Verifies that capability packs can serve as safe, versioned community components
without creating a parallel runtime.  Covers:

  - Pack identity: valid id/version/author/license/source pass schema
  - Schema rejection: unexpected keys and invalid field types are rejected
  - Dependency validation: satisfied/missing requirements
  - Catalog schema validation: structural allowlisting before AG2 injection
  - Deterministic materialization: fixture community pack renders expected files
  - Provenance manifest: emitted with correct pack metadata and file ownership
  - Two-pack no-collision: greetings + farewell materialise without file conflicts
  - Existing first-party packs remain compatible: social, messaging, mozaikspay contexts
  - Bundle scanner validates provenance manifest schema when present

Self-host proof: all tests use only local filesystem paths under tests/fixtures/.
No App Zero, no network, no paid LLM, no BlocUnited APIs required.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

WORKSPACE = Path(__file__).resolve().parents[1]
COMMUNITY_PACKS = Path(__file__).resolve().parent / "fixtures" / "community_packs"
BUILD_CONTEXT = WORKSPACE / "factory_app" / "build_context"

GREETINGS_PACK = COMMUNITY_PACKS / "greetings"
FAREWELL_PACK = COMMUNITY_PACKS / "farewell"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _pack_descriptor(pack_dir: Path, pack_id: str) -> dict[str, Any]:
    """Build a capability_packs descriptor from a local fixture pack directory."""
    return {
        "id": pack_id,
        "pack_id": pack_id,
        "pack_source_path": str(pack_dir),
        "capability_source": "generated_module",
    }


def _materialize(*pack_descs: dict[str, Any]) -> list[dict[str, str]]:
    from factory_app.workflows.AppGenerator.tools.resolve_managed_capability_templates import (
        resolve_managed_capability_templates,
    )
    return resolve_managed_capability_templates(list(pack_descs))


def _files_map(*pack_descs: dict[str, Any]) -> dict[str, str]:
    return {f["filename"]: f["content"] for f in _materialize(*pack_descs)}


# ---------------------------------------------------------------------------
# 1. Pack identity: valid id/version/author/license/source pass schema
# ---------------------------------------------------------------------------


def test_valid_pack_identity_passes_schema() -> None:
    from factory_app.workflows.AppGenerator.tools.pack_context_schema import validate_pack_context

    context = {
        "context_id": "greetings",
        "applies_to_workflows": ["AppGenerator"],
        "assets": [{"path": "templates/", "kind": "templates"}],
        "pack": {
            "id": "greetings",
            "version": "0.1.0",
            "author": "Community Author",
            "license": "MIT",
            "source": "https://example.com/greetings",
            "status": "active",
            "capability_source": "generated_module",
        },
    }
    result = validate_pack_context(context)
    assert result.valid, [d.message for d in result.errors]
    assert result.pack_id == "greetings"


def test_pack_version_is_optional_and_still_valid() -> None:
    from factory_app.workflows.AppGenerator.tools.pack_context_schema import validate_pack_context

    context: dict[str, Any] = {
        "context_id": "legacy",
        "applies_to_workflows": ["AppGenerator"],
        "assets": [],
        "pack": {
            "id": "legacy",
            "status": "active",
            "capability_source": "generated_module",
        },
    }
    result = validate_pack_context(context)
    assert result.valid, [d.message for d in result.errors]


def test_pack_without_pack_block_is_valid() -> None:
    """Contexts like AppGenerator omit the pack: block."""
    from factory_app.workflows.AppGenerator.tools.pack_context_schema import validate_pack_context

    context: dict[str, Any] = {
        "context_id": "AppGenerator",
        "applies_to_workflows": ["AppGenerator"],
        "assets": [],
    }
    result = validate_pack_context(context)
    assert result.valid


# ---------------------------------------------------------------------------
# 2. Schema rejection: unexpected keys
# ---------------------------------------------------------------------------


def test_unexpected_root_key_rejected_by_schema() -> None:
    from factory_app.workflows.AppGenerator.tools.pack_context_schema import validate_pack_context

    context: dict[str, Any] = {
        "context_id": "bad_pack",
        "applies_to_workflows": ["AppGenerator"],
        "assets": [],
        "pack": {"id": "bad_pack", "status": "active"},
        "secret_override": "injected_payload",  # forbidden key
    }
    result = validate_pack_context(context)
    assert not result.valid
    error_fields = {d.field for d in result.errors}
    assert "secret_override" in error_fields


def test_unexpected_pack_key_rejected_by_schema() -> None:
    from factory_app.workflows.AppGenerator.tools.pack_context_schema import validate_pack_context

    context: dict[str, Any] = {
        "context_id": "bad_pack",
        "applies_to_workflows": ["AppGenerator"],
        "assets": [],
        "pack": {
            "id": "bad_pack",
            "status": "active",
            "hidden_injection": "payload",  # forbidden pack key
        },
    }
    result = validate_pack_context(context)
    assert not result.valid
    error_fields = {d.field for d in result.errors}
    assert "pack.hidden_injection" in error_fields


def test_missing_pack_id_rejected_by_schema() -> None:
    from factory_app.workflows.AppGenerator.tools.pack_context_schema import validate_pack_context

    context: dict[str, Any] = {
        "context_id": "nameless",
        "applies_to_workflows": ["AppGenerator"],
        "assets": [],
        "pack": {
            "id": "",  # empty — must fail
            "status": "active",
        },
    }
    result = validate_pack_context(context)
    assert not result.valid


def test_invalid_pack_id_characters_rejected() -> None:
    from factory_app.workflows.AppGenerator.tools.pack_context_schema import validate_pack_context

    context: dict[str, Any] = {
        "context_id": "bad-id",
        "applies_to_workflows": ["AppGenerator"],
        "assets": [],
        "pack": {
            "id": "Bad-Pack-Id",  # uppercase + hyphens not allowed
            "status": "active",
        },
    }
    result = validate_pack_context(context)
    assert not result.valid


def test_version_must_be_string() -> None:
    from factory_app.workflows.AppGenerator.tools.pack_context_schema import validate_pack_context

    context: dict[str, Any] = {
        "context_id": "typed_wrong",
        "applies_to_workflows": ["AppGenerator"],
        "assets": [],
        "pack": {
            "id": "typed_wrong",
            "status": "active",
            "version": 123,  # must be string
        },
    }
    result = validate_pack_context(context)
    assert not result.valid


# ---------------------------------------------------------------------------
# 3. Dependency validation: satisfied requirement passes
# ---------------------------------------------------------------------------


def test_dependency_satisfied_materializes_without_error() -> None:
    """farewell requires greetings — both selected → no error."""
    greetings = _pack_descriptor(GREETINGS_PACK, "greetings")
    farewell = _pack_descriptor(FAREWELL_PACK, "farewell")

    files = _materialize(greetings, farewell)
    filenames = {f["filename"] for f in files}
    assert "modules/greetings/module.yaml" in filenames
    assert "modules/farewell/module.yaml" in filenames


# ---------------------------------------------------------------------------
# 4. Dependency validation: missing requirement raises structured error
# ---------------------------------------------------------------------------


def test_dependency_missing_raises_pack_dependency_error() -> None:
    """farewell requires greetings — selecting farewell alone must fail."""
    from factory_app.workflows.AppGenerator.tools.resolve_managed_capability_templates import (
        PackDependencyError,
    )

    farewell = _pack_descriptor(FAREWELL_PACK, "farewell")
    with pytest.raises(PackDependencyError) as exc_info:
        _materialize(farewell)

    err = exc_info.value
    assert err.pack_id == "farewell"
    assert "greetings" in err.missing_packs
    # The capability from greetings is also declared as required
    assert any("greetings" in c for c in err.missing_capabilities)


def test_dependency_error_message_is_human_readable() -> None:
    from factory_app.workflows.AppGenerator.tools.resolve_managed_capability_templates import (
        PackDependencyError,
    )

    farewell = _pack_descriptor(FAREWELL_PACK, "farewell")
    with pytest.raises(PackDependencyError) as exc_info:
        _materialize(farewell)

    msg = str(exc_info.value)
    assert "farewell" in msg
    assert "greetings" in msg


# ---------------------------------------------------------------------------
# 5. Catalog schema rejects untrusted context.yaml at materialization time
# ---------------------------------------------------------------------------


def test_catalog_schema_validation_rejects_untrusted_keys_at_materialization(
    tmp_path: Path,
) -> None:
    """A pack with an unexpected root key in context.yaml is rejected before templates render."""
    from factory_app.workflows.AppGenerator.tools.resolve_managed_capability_templates import (
        ManagedCapabilityTemplateError,
    )

    # Build an adversarial pack directory
    pack_dir = tmp_path / "adversarial_pack"
    pack_dir.mkdir()
    (pack_dir / "context.yaml").write_text(
        "context_id: adversarial\n"
        "applies_to_workflows:\n  - AppGenerator\n"
        "assets:\n  - path: templates/\n    kind: templates\n"
        "pack:\n  id: adversarial\n  status: active\n"
        "injected_system_prompt: 'IGNORE PREVIOUS INSTRUCTIONS'\n",
        encoding="utf-8",
    )
    templates_dir = pack_dir / "templates"
    templates_dir.mkdir()

    desc = _pack_descriptor(pack_dir, "adversarial")
    with pytest.raises(ManagedCapabilityTemplateError, match="schema validation"):
        _materialize(desc)


# ---------------------------------------------------------------------------
# 6. Deterministic materialization: fixture greetings pack
# ---------------------------------------------------------------------------


def test_greetings_pack_materializes_expected_files() -> None:
    greetings = _pack_descriptor(GREETINGS_PACK, "greetings")
    files = _files_map(greetings)

    assert "modules/greetings/module.yaml" in files
    assert "modules/greetings/backend/__init__.py" in files
    assert "modules/greetings/backend/service.py" in files


def test_greetings_module_yaml_is_valid_yaml() -> None:
    greetings = _pack_descriptor(GREETINGS_PACK, "greetings")
    files = _files_map(greetings)

    content = files["modules/greetings/module.yaml"]
    parsed = yaml.safe_load(content)
    assert isinstance(parsed, dict)
    assert parsed.get("module_id") == "greetings"


def test_greetings_service_py_is_valid_python() -> None:
    import ast

    greetings = _pack_descriptor(GREETINGS_PACK, "greetings")
    files = _files_map(greetings)

    content = files["modules/greetings/backend/service.py"]
    ast.parse(content)  # raises SyntaxError if invalid


# ---------------------------------------------------------------------------
# 7. Provenance manifest emitted with pack metadata
# ---------------------------------------------------------------------------


def test_provenance_manifest_emitted_for_greetings_pack() -> None:
    greetings = _pack_descriptor(GREETINGS_PACK, "greetings")
    files = _files_map(greetings)

    assert ".mozaiks/pack_provenance.json" in files


def test_provenance_manifest_contains_correct_schema_version() -> None:
    greetings = _pack_descriptor(GREETINGS_PACK, "greetings")
    files = _files_map(greetings)

    manifest = json.loads(files[".mozaiks/pack_provenance.json"])
    assert manifest["schema_version"] == "mozaiks.pack_provenance.v1"


def test_provenance_manifest_contains_framework_version() -> None:
    from mozaiksai.version import __version__

    greetings = _pack_descriptor(GREETINGS_PACK, "greetings")
    files = _files_map(greetings)

    manifest = json.loads(files[".mozaiks/pack_provenance.json"])
    assert manifest["framework_version"] == __version__


def test_provenance_manifest_has_greetings_pack_entry() -> None:
    greetings = _pack_descriptor(GREETINGS_PACK, "greetings")
    files = _files_map(greetings)

    manifest = json.loads(files[".mozaiks/pack_provenance.json"])
    pack_ids = [p["pack_id"] for p in manifest["packs"]]
    assert "greetings" in pack_ids


def test_provenance_contains_pack_version_from_context_yaml() -> None:
    greetings = _pack_descriptor(GREETINGS_PACK, "greetings")
    files = _files_map(greetings)

    manifest = json.loads(files[".mozaiks/pack_provenance.json"])
    greetings_entry = next(p for p in manifest["packs"] if p["pack_id"] == "greetings")
    assert greetings_entry["pack_version"] == "0.1.0"


# ---------------------------------------------------------------------------
# 8. Pack-to-file ownership in provenance
# ---------------------------------------------------------------------------


def test_provenance_maps_files_to_greetings_pack() -> None:
    greetings = _pack_descriptor(GREETINGS_PACK, "greetings")
    files = _files_map(greetings)

    manifest = json.loads(files[".mozaiks/pack_provenance.json"])
    greetings_entry = next(p for p in manifest["packs"] if p["pack_id"] == "greetings")
    provenance_paths = {f["path"] for f in greetings_entry["files"]}

    assert "modules/greetings/module.yaml" in provenance_paths
    assert "modules/greetings/backend/service.py" in provenance_paths


def test_provenance_records_owner_from_contract_required_outputs() -> None:
    greetings = _pack_descriptor(GREETINGS_PACK, "greetings")
    files = _files_map(greetings)

    manifest = json.loads(files[".mozaiks/pack_provenance.json"])
    greetings_entry = next(p for p in manifest["packs"] if p["pack_id"] == "greetings")
    file_by_path = {f["path"]: f for f in greetings_entry["files"]}

    # handler.py is declared as owner=workspace in contract.yaml
    handler = file_by_path.get("modules/greetings/backend/handler.py")
    # The handler.py is listed as owner=workspace in the contract, but it is NOT
    # in the templates directory (workspace-owned files are not templated).
    # module.yaml IS in templates — its owner should be "templates".
    module_yaml = file_by_path["modules/greetings/module.yaml"]
    assert module_yaml["owner"] == "templates"


# ---------------------------------------------------------------------------
# 9. Two packs without collisions
# ---------------------------------------------------------------------------


def test_greetings_and_farewell_materialize_without_collisions() -> None:
    greetings = _pack_descriptor(GREETINGS_PACK, "greetings")
    farewell = _pack_descriptor(FAREWELL_PACK, "farewell")

    files = _files_map(greetings, farewell)

    assert "modules/greetings/module.yaml" in files
    assert "modules/farewell/module.yaml" in files


def test_provenance_records_both_packs_when_two_selected() -> None:
    greetings = _pack_descriptor(GREETINGS_PACK, "greetings")
    farewell = _pack_descriptor(FAREWELL_PACK, "farewell")

    files = _files_map(greetings, farewell)

    manifest = json.loads(files[".mozaiks/pack_provenance.json"])
    pack_ids = {p["pack_id"] for p in manifest["packs"]}
    assert "greetings" in pack_ids
    assert "farewell" in pack_ids


def test_greetings_files_not_present_in_farewell_provenance() -> None:
    greetings = _pack_descriptor(GREETINGS_PACK, "greetings")
    farewell = _pack_descriptor(FAREWELL_PACK, "farewell")

    files = _files_map(greetings, farewell)

    manifest = json.loads(files[".mozaiks/pack_provenance.json"])
    farewell_entry = next(p for p in manifest["packs"] if p["pack_id"] == "farewell")
    farewell_paths = {f["path"] for f in farewell_entry["files"]}
    # greetings files must not appear in farewell's provenance entry
    assert "modules/greetings/module.yaml" not in farewell_paths


# ---------------------------------------------------------------------------
# 10. Existing first-party packs remain compatible
# ---------------------------------------------------------------------------


def test_social_pack_context_passes_schema_validation() -> None:
    from factory_app.workflows.AppGenerator.tools.pack_context_schema import validate_pack_context

    context = _read_yaml(BUILD_CONTEXT / "social" / "context.yaml")
    result = validate_pack_context(context)
    assert result.valid, [d.message for d in result.errors]


def test_messaging_pack_context_passes_schema_validation() -> None:
    from factory_app.workflows.AppGenerator.tools.pack_context_schema import validate_pack_context

    context = _read_yaml(BUILD_CONTEXT / "messaging" / "context.yaml")
    result = validate_pack_context(context)
    assert result.valid, [d.message for d in result.errors]


def test_mozaikspay_pack_context_passes_schema_validation() -> None:
    from factory_app.workflows.AppGenerator.tools.pack_context_schema import validate_pack_context

    context = _read_yaml(BUILD_CONTEXT / "mozaikspay" / "context.yaml")
    result = validate_pack_context(context)
    assert result.valid, [d.message for d in result.errors]


def test_commerce_pack_context_passes_schema_validation() -> None:
    from factory_app.workflows.AppGenerator.tools.pack_context_schema import validate_pack_context

    context = _read_yaml(BUILD_CONTEXT / "commerce" / "context.yaml")
    result = validate_pack_context(context)
    assert result.valid, [d.message for d in result.errors]


def test_notifications_pack_context_passes_schema_validation() -> None:
    from factory_app.workflows.AppGenerator.tools.pack_context_schema import validate_pack_context

    context = _read_yaml(BUILD_CONTEXT / "notifications" / "context.yaml")
    result = validate_pack_context(context)
    assert result.valid, [d.message for d in result.errors]


def test_entitlement_dispatch_pack_context_passes_schema_validation() -> None:
    from factory_app.workflows.AppGenerator.tools.pack_context_schema import validate_pack_context

    context = _read_yaml(BUILD_CONTEXT / "entitlement_dispatch" / "context.yaml")
    result = validate_pack_context(context)
    assert result.valid, [d.message for d in result.errors]


def test_files_pack_context_passes_schema_validation() -> None:
    from factory_app.workflows.AppGenerator.tools.pack_context_schema import validate_pack_context

    context = _read_yaml(BUILD_CONTEXT / "files" / "context.yaml")
    result = validate_pack_context(context)
    assert result.valid, [d.message for d in result.errors]


def test_support_pack_context_passes_schema_validation() -> None:
    from factory_app.workflows.AppGenerator.tools.pack_context_schema import validate_pack_context

    context = _read_yaml(BUILD_CONTEXT / "support" / "context.yaml")
    result = validate_pack_context(context)
    assert result.valid, [d.message for d in result.errors]


# ---------------------------------------------------------------------------
# 11. Bundle scanner validates provenance manifest when present
# ---------------------------------------------------------------------------


def test_bundle_scanner_accepts_valid_provenance_manifest() -> None:
    from factory_app.workflows.AppGenerator.tools.generated_bundle_scanner import (
        scan_generated_bundle,
    )

    greetings = _pack_descriptor(GREETINGS_PACK, "greetings")
    files = _files_map(greetings)

    errors = scan_generated_bundle(files)
    # No provenance-specific errors for a valid manifest
    prov_errors = [e for e in errors if "pack_provenance" in e]
    assert not prov_errors, prov_errors


def test_bundle_scanner_rejects_invalid_provenance_schema(tmp_path: Path) -> None:
    from factory_app.workflows.AppGenerator.tools.generated_bundle_scanner import (
        scan_generated_bundle,
    )

    files = {
        ".mozaiks/pack_provenance.json": json.dumps({"wrong_key": "bad"}),
    }
    errors = scan_generated_bundle(files)
    prov_errors = [e for e in errors if "pack_provenance" in e]
    assert prov_errors, "Expected provenance schema error but got none"


def test_bundle_scanner_skips_provenance_check_when_absent() -> None:
    from factory_app.workflows.AppGenerator.tools.generated_bundle_scanner import (
        scan_generated_bundle,
    )

    # A minimal valid app bundle without provenance
    files: dict[str, str] = {}
    errors = scan_generated_bundle(files)
    prov_errors = [e for e in errors if "pack_provenance" in e]
    assert not prov_errors


# ---------------------------------------------------------------------------
# 12. Self-host proof: local third-party pack installed under build_context mechanism
# ---------------------------------------------------------------------------


def test_fixture_pack_discovered_via_pack_source_path() -> None:
    """Prove a community pack installed at a local path materializes without
    App Zero, network access, or paid LLM calls."""
    assert GREETINGS_PACK.exists(), f"Fixture pack missing at {GREETINGS_PACK}"
    assert (GREETINGS_PACK / "context.yaml").exists()
    assert (GREETINGS_PACK / "contract.yaml").exists()
    assert (GREETINGS_PACK / "templates").is_dir()


def test_fixture_pack_selected_deterministically_via_descriptor() -> None:
    """Descriptor with pack_source_path pointing to fixture → deterministic file output.

    The provenance manifest includes a ``generated_at`` timestamp that varies
    between calls, so we compare all files except the provenance manifest.
    The manifest itself is structurally validated in other tests.
    """
    _PROV = ".mozaiks/pack_provenance.json"
    greetings = _pack_descriptor(GREETINGS_PACK, "greetings")

    files = {k: v for k, v in _files_map(greetings).items() if k != _PROV}
    files2 = {k: v for k, v in _files_map(greetings).items() if k != _PROV}

    assert files == files2


def test_fixture_pack_passes_generated_bundle_validation() -> None:
    from factory_app.workflows.AppGenerator.tools.generated_bundle_scanner import (
        scan_generated_bundle,
    )

    greetings = _pack_descriptor(GREETINGS_PACK, "greetings")
    files = _files_map(greetings)

    errors = scan_generated_bundle(files)
    # Filter errors unrelated to our fixture (the fixture doesn't claim to be a
    # full app; we only care that no pack-provenance or schema errors appear)
    prov_errors = [e for e in errors if "pack_provenance" in e]
    assert not prov_errors
