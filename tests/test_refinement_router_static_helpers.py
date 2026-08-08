"""
Pure static helper unit tests for:
  mozaiksai/control_plane/implementations/refinement_router.py

Covers RefinementTriggerRouteResolver static/class methods that have
no external IO or global state dependency:

  _artifact_label:
    - no policy → underscores replaced with spaces in build_family
    - policy with label → label used (underscores replaced)
    - None build_family handled

  _to_policy:
    - build_family and label stored
    - empty label falls back to build_family
    - patch, design, feature, core routes stored

  _dedupe_paths:
    - duplicates removed (preserves first occurrence)
    - empty strings removed
    - backslashes normalized to slashes
    - leading slashes stripped
    - whitespace trimmed

  _manifest_paths_from_extra:
    - "files_manifest" key: string entries → paths
    - "files_manifest" key: dict entries → path extracted
    - "file_manifest" fallback key
    - "artifact_files_manifest" fallback key
    - no matching key → []
    - paths deduped
    - non-list value skipped

  _manifest_entries_from_extra:
    - string entries become {"path": entry}
    - dict entries with path key → normalized
    - dict entries without path key → skipped
    - empty path in dict → skipped
    - no matching key → []
    - non-list value skipped

  _module_ids_from_manifest:
    - "modules/{id}/..." paths → id extracted
    - wildcard "*" and "**" module parts ignored
    - non-modules paths ignored
    - short paths ignored
    - duplicates removed

  _is_canonical_module_manifest_path:
    - "modules/{id}/module.yaml" → True
    - "modules/{id}/runtime_extensions.yaml" → True
    - "modules/{id}/contracts/events.yaml" → True
    - "modules/{id}/contracts/reactions.yaml" → True
    - "modules/{id}/backend/handler.py" → True
    - "modules/{id}/backend/service.py" → True
    - path for different module → False
    - "modules/{id}/other_file.txt" → False
    - "modules/{id}/contracts/other.txt" → False (not .yaml/.yml)

  _module_path_sort_key:
    - module.yaml → (module_id, 0, 0, "module.yaml")
    - contracts/events.yaml → (module_id, 1, 0, "events.yaml")
    - contracts/admin.yaml → (module_id, 1, 4, "admin.yaml")
    - backend/handler.py → (module_id, 2, 0, "handler.py")
    - backend/schemas.py → (module_id, 2, 4, "schemas.py")
    - runtime_extensions.yaml → (module_id, 3, 0, "runtime_extensions.yaml")
    - unknown path → (module_id, 100, 0, relative)

  _pack_ids_from_integration_clients:
    - "services/integrations/payment_provider_client.py" → ["payment_provider"]
    - multiple paths → multiple pack_ids
    - non-matching path → []
    - duplicates removed

  RefinementRequest._normalize_ARTIFACT_KIND:
    - uppercase normalized to lowercase
    - enum value used
    - whitespace stripped
    - empty string raises ValueError

  _request_impact_text:
    - combines raw_user_request, source_surface, and intent signals
    - result is lowercase
    - None source_surface handled
    - signals joined
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from mozaiksai.control_plane.implementations.refinement_router import (
    ArtifactRoutePolicy,
    ChangeClass,
    ChangeIntent,
    RefinementRequest,
    RefinementTriggerRouteResolver,
)
from mozaiksai.control_plane.schema import (
    ControlPlaneArtifactChangeRoutesManifest,
    ControlPlaneArtifactRoutingManifest,
    ControlPlaneChangeRouteManifest,
)

# ---------------------------------------------------------------------------
# Helpers for constructing test objects
# ---------------------------------------------------------------------------

def _route(seq: str = "full_build") -> ControlPlaneChangeRouteManifest:
    return ControlPlaneChangeRouteManifest(workflow_sequence=seq)


def _artifact_routing_manifest(build_family: str, label: str | None = None) -> ControlPlaneArtifactRoutingManifest:
    return ControlPlaneArtifactRoutingManifest(
        build_family=build_family,
        label=label,
        routes=ControlPlaneArtifactChangeRoutesManifest(
            patch=_route("patch_seq"),
            design=_route("design_seq"),
            feature=_route("feature_seq"),
            core=_route("core_seq"),
        ),
    )


def _request(raw: str = "", surface: str | None = None) -> RefinementRequest:
    return RefinementRequest(build_family="app_bundle", raw_user_request=raw, source_surface=surface)


def _intent(signals: list[str] | None = None) -> ChangeIntent:
    return ChangeIntent(
        change_class=ChangeClass.PATCH,
        rationale="test",
        signals=signals or [],
    )


# ---------------------------------------------------------------------------
# 1. _artifact_label
# ---------------------------------------------------------------------------

class TestArtifactLabel:
    def test_no_policy_underscores_replaced(self):
        result = RefinementTriggerRouteResolver._artifact_label("app_bundle")
        assert result == "app bundle"

    def test_no_policy_preserves_spaces(self):
        result = RefinementTriggerRouteResolver._artifact_label("workflow bundle")
        assert result == "workflow bundle"

    def test_policy_label_used(self):
        policy = ArtifactRoutePolicy(
            build_family="app_bundle",
            label="App_Bundle",
            patch=_route(),
            design=_route(),
            feature=_route(),
            core=_route(),
        )
        result = RefinementTriggerRouteResolver._artifact_label("app_bundle", policy)
        assert result == "App Bundle"

    def test_policy_none_uses_ARTIFACT_KIND(self):
        result = RefinementTriggerRouteResolver._artifact_label("design_docs", None)
        assert result == "design docs"


# ---------------------------------------------------------------------------
# 2. _to_policy
# ---------------------------------------------------------------------------

class TestToPolicy:
    def test_build_family_stored(self):
        manifest = _artifact_routing_manifest("app_bundle")
        policy = RefinementTriggerRouteResolver._to_policy(manifest)
        assert policy.build_family == "app_bundle"

    def test_explicit_label_stored(self):
        manifest = _artifact_routing_manifest("app_bundle", label="App Bundle")
        policy = RefinementTriggerRouteResolver._to_policy(manifest)
        assert policy.label == "App Bundle"

    def test_empty_label_falls_back_to_ARTIFACT_KIND(self):
        manifest = _artifact_routing_manifest("app_bundle", label=None)
        policy = RefinementTriggerRouteResolver._to_policy(manifest)
        assert policy.label == "app_bundle"

    def test_patch_route_stored(self):
        manifest = _artifact_routing_manifest("app_bundle")
        policy = RefinementTriggerRouteResolver._to_policy(manifest)
        assert policy.patch.workflow_sequence == "patch_seq"

    def test_design_route_stored(self):
        manifest = _artifact_routing_manifest("app_bundle")
        policy = RefinementTriggerRouteResolver._to_policy(manifest)
        assert policy.design.workflow_sequence == "design_seq"

    def test_feature_route_stored(self):
        manifest = _artifact_routing_manifest("app_bundle")
        policy = RefinementTriggerRouteResolver._to_policy(manifest)
        assert policy.feature.workflow_sequence == "feature_seq"

    def test_core_route_stored(self):
        manifest = _artifact_routing_manifest("app_bundle")
        policy = RefinementTriggerRouteResolver._to_policy(manifest)
        assert policy.core.workflow_sequence == "core_seq"


# ---------------------------------------------------------------------------
# 3. _dedupe_paths
# ---------------------------------------------------------------------------

class TestDedupePaths:
    def test_duplicates_removed_first_wins(self):
        result = RefinementTriggerRouteResolver._dedupe_paths(["a/b.py", "c/d.py", "a/b.py"])
        assert result == ["a/b.py", "c/d.py"]

    def test_empty_strings_removed(self):
        result = RefinementTriggerRouteResolver._dedupe_paths(["", "a/b.py", ""])
        assert result == ["a/b.py"]

    def test_none_entries_removed(self):
        result = RefinementTriggerRouteResolver._dedupe_paths([None, "a/b.py"])
        assert result == ["a/b.py"]

    def test_backslashes_normalized(self):
        result = RefinementTriggerRouteResolver._dedupe_paths(["a\\b\\c.py"])
        assert result == ["a/b/c.py"]

    def test_leading_slashes_stripped(self):
        result = RefinementTriggerRouteResolver._dedupe_paths(["/app/module.py"])
        assert result == ["app/module.py"]

    def test_whitespace_stripped(self):
        result = RefinementTriggerRouteResolver._dedupe_paths(["  a/b.py  "])
        assert result == ["a/b.py"]

    def test_empty_list_returns_empty(self):
        assert RefinementTriggerRouteResolver._dedupe_paths([]) == []

    def test_order_preserved(self):
        paths = ["c.py", "a.py", "b.py"]
        result = RefinementTriggerRouteResolver._dedupe_paths(paths)
        assert result == ["c.py", "a.py", "b.py"]


# ---------------------------------------------------------------------------
# 4. _manifest_paths_from_extra
# ---------------------------------------------------------------------------

class TestManifestPathsFromExtra:
    def test_string_entries_returned_as_paths(self):
        extra = {"files_manifest": ["app/module.yaml", "app/handler.py"]}
        result = RefinementTriggerRouteResolver._manifest_paths_from_extra(extra)
        assert "app/module.yaml" in result
        assert "app/handler.py" in result

    def test_dict_entries_path_extracted(self):
        extra = {"files_manifest": [{"path": "modules/my_mod/module.yaml"}]}
        result = RefinementTriggerRouteResolver._manifest_paths_from_extra(extra)
        assert "modules/my_mod/module.yaml" in result

    def test_file_manifest_fallback_key(self):
        extra = {"file_manifest": ["app/x.py"]}
        result = RefinementTriggerRouteResolver._manifest_paths_from_extra(extra)
        assert "app/x.py" in result

    def test_artifact_files_manifest_fallback(self):
        extra = {"artifact_files_manifest": ["app/y.py"]}
        result = RefinementTriggerRouteResolver._manifest_paths_from_extra(extra)
        assert "app/y.py" in result

    def test_no_matching_key_returns_empty(self):
        extra = {"other_key": ["path1"]}
        result = RefinementTriggerRouteResolver._manifest_paths_from_extra(extra)
        assert result == []

    def test_non_list_value_skipped(self):
        # If files_manifest exists but is not a list, skip it and try next
        extra = {"files_manifest": "not_a_list", "file_manifest": ["a.py"]}
        result = RefinementTriggerRouteResolver._manifest_paths_from_extra(extra)
        assert "a.py" in result

    def test_paths_deduped(self):
        extra = {"files_manifest": ["a.py", "a.py", "b.py"]}
        result = RefinementTriggerRouteResolver._manifest_paths_from_extra(extra)
        assert result.count("a.py") == 1

    def test_empty_extra_returns_empty(self):
        assert RefinementTriggerRouteResolver._manifest_paths_from_extra({}) == []

    def test_files_manifest_key_takes_priority(self):
        # files_manifest exists and is a list → returned, file_manifest not consulted
        extra = {"files_manifest": ["x.py"], "file_manifest": ["y.py"]}
        result = RefinementTriggerRouteResolver._manifest_paths_from_extra(extra)
        assert "x.py" in result
        assert "y.py" not in result


# ---------------------------------------------------------------------------
# 5. _manifest_entries_from_extra
# ---------------------------------------------------------------------------

class TestManifestEntriesFromExtra:
    def test_string_entry_wrapped_in_dict(self):
        extra = {"files_manifest": ["modules/my_mod/module.yaml"]}
        result = RefinementTriggerRouteResolver._manifest_entries_from_extra(extra)
        assert result[0] == {"path": "modules/my_mod/module.yaml"}

    def test_dict_entry_with_path_key_returned(self):
        entry = {"path": "app/x.py", "kind": "module"}
        extra = {"files_manifest": [entry]}
        result = RefinementTriggerRouteResolver._manifest_entries_from_extra(extra)
        assert result[0]["path"] == "app/x.py"
        assert result[0]["kind"] == "module"

    def test_dict_entry_without_path_skipped(self):
        extra = {"files_manifest": [{"name": "no_path_here"}]}
        result = RefinementTriggerRouteResolver._manifest_entries_from_extra(extra)
        assert result == []

    def test_dict_entry_empty_path_skipped(self):
        extra = {"files_manifest": [{"path": ""}]}
        result = RefinementTriggerRouteResolver._manifest_entries_from_extra(extra)
        assert result == []

    def test_no_matching_key_returns_empty(self):
        assert RefinementTriggerRouteResolver._manifest_entries_from_extra({"other": []}) == []

    def test_non_list_value_skipped(self):
        extra = {"files_manifest": "not_a_list", "file_manifest": ["a.py"]}
        result = RefinementTriggerRouteResolver._manifest_entries_from_extra(extra)
        assert result == [{"path": "a.py"}]

    def test_empty_extra_returns_empty(self):
        assert RefinementTriggerRouteResolver._manifest_entries_from_extra({}) == []


# ---------------------------------------------------------------------------
# 6. _module_ids_from_manifest
# ---------------------------------------------------------------------------

class TestModuleIdsFromManifest:
    def test_module_id_extracted(self):
        paths = ["modules/my_module/module.yaml"]
        result = RefinementTriggerRouteResolver._module_ids_from_manifest(paths)
        assert result == ["my_module"]

    def test_multiple_files_same_module_deduped(self):
        paths = ["modules/my_mod/module.yaml", "modules/my_mod/backend/handler.py"]
        result = RefinementTriggerRouteResolver._module_ids_from_manifest(paths)
        assert result.count("my_mod") == 1

    def test_multiple_modules_extracted(self):
        paths = ["modules/mod_a/module.yaml", "modules/mod_b/module.yaml"]
        result = RefinementTriggerRouteResolver._module_ids_from_manifest(paths)
        assert "mod_a" in result
        assert "mod_b" in result

    def test_wildcard_module_id_skipped(self):
        paths = ["modules/*/module.yaml"]
        result = RefinementTriggerRouteResolver._module_ids_from_manifest(paths)
        assert result == []

    def test_double_wildcard_skipped(self):
        paths = ["modules/**/module.yaml"]
        result = RefinementTriggerRouteResolver._module_ids_from_manifest(paths)
        assert result == []

    def test_non_modules_path_ignored(self):
        paths = ["ui/pages/home.yaml", "app/services/my_client.py"]
        result = RefinementTriggerRouteResolver._module_ids_from_manifest(paths)
        assert result == []

    def test_short_modules_path_ignored(self):
        # Only "modules/" without a module_id part
        paths = ["modules/"]
        result = RefinementTriggerRouteResolver._module_ids_from_manifest(paths)
        assert result == []

    def test_order_preserved(self):
        paths = [
            "modules/mod_c/module.yaml",
            "modules/mod_a/module.yaml",
            "modules/mod_b/module.yaml",
        ]
        result = RefinementTriggerRouteResolver._module_ids_from_manifest(paths)
        assert result == ["mod_c", "mod_a", "mod_b"]


# ---------------------------------------------------------------------------
# 7. _is_canonical_module_manifest_path
# ---------------------------------------------------------------------------

class TestIsCanonicalModuleManifestPath:
    def test_module_yaml_true(self):
        assert RefinementTriggerRouteResolver._is_canonical_module_manifest_path(
            "modules/my_mod/module.yaml", "my_mod"
        ) is True

    def test_runtime_extensions_yaml_true(self):
        assert RefinementTriggerRouteResolver._is_canonical_module_manifest_path(
            "modules/my_mod/runtime_extensions.yaml", "my_mod"
        ) is True

    def test_contracts_events_yaml_true(self):
        assert RefinementTriggerRouteResolver._is_canonical_module_manifest_path(
            "modules/my_mod/contracts/events.yaml", "my_mod"
        ) is True

    def test_contracts_reactions_yaml_true(self):
        assert RefinementTriggerRouteResolver._is_canonical_module_manifest_path(
            "modules/my_mod/contracts/reactions.yaml", "my_mod"
        ) is True

    def test_contracts_yml_extension_true(self):
        assert RefinementTriggerRouteResolver._is_canonical_module_manifest_path(
            "modules/my_mod/contracts/admin.yml", "my_mod"
        ) is True

    def test_backend_handler_py_true(self):
        assert RefinementTriggerRouteResolver._is_canonical_module_manifest_path(
            "modules/my_mod/backend/handler.py", "my_mod"
        ) is True

    def test_backend_service_py_true(self):
        assert RefinementTriggerRouteResolver._is_canonical_module_manifest_path(
            "modules/my_mod/backend/service.py", "my_mod"
        ) is True

    def test_different_module_id_false(self):
        assert RefinementTriggerRouteResolver._is_canonical_module_manifest_path(
            "modules/other_mod/module.yaml", "my_mod"
        ) is False

    def test_backend_txt_file_false(self):
        assert RefinementTriggerRouteResolver._is_canonical_module_manifest_path(
            "modules/my_mod/backend/notes.txt", "my_mod"
        ) is False

    def test_contracts_txt_file_false(self):
        assert RefinementTriggerRouteResolver._is_canonical_module_manifest_path(
            "modules/my_mod/contracts/readme.txt", "my_mod"
        ) is False

    def test_top_level_other_file_false(self):
        assert RefinementTriggerRouteResolver._is_canonical_module_manifest_path(
            "modules/my_mod/custom_file.json", "my_mod"
        ) is False

    def test_non_modules_path_false(self):
        assert RefinementTriggerRouteResolver._is_canonical_module_manifest_path(
            "ui/pages/home.yaml", "my_mod"
        ) is False


# ---------------------------------------------------------------------------
# 8. _module_path_sort_key
# ---------------------------------------------------------------------------

class TestModulePathSortKey:
    def test_module_yaml_priority_0(self):
        key = RefinementTriggerRouteResolver._module_path_sort_key(
            "modules/my_mod/module.yaml"
        )
        assert key[1] == 0

    def test_contracts_events_yaml_priority_1(self):
        key = RefinementTriggerRouteResolver._module_path_sort_key(
            "modules/my_mod/contracts/events.yaml"
        )
        assert key[1] == 1
        assert key[2] == 0  # events.yaml is order 0

    def test_contracts_admin_yaml_order_4(self):
        key = RefinementTriggerRouteResolver._module_path_sort_key(
            "modules/my_mod/contracts/admin.yaml"
        )
        assert key[1] == 1
        assert key[2] == 4

    def test_backend_handler_py_priority_2(self):
        key = RefinementTriggerRouteResolver._module_path_sort_key(
            "modules/my_mod/backend/handler.py"
        )
        assert key[1] == 2
        assert key[2] == 0  # handler.py is order 0

    def test_backend_schemas_py_order_4(self):
        key = RefinementTriggerRouteResolver._module_path_sort_key(
            "modules/my_mod/backend/schemas.py"
        )
        assert key[1] == 2
        assert key[2] == 4

    def test_runtime_extensions_priority_3(self):
        key = RefinementTriggerRouteResolver._module_path_sort_key(
            "modules/my_mod/runtime_extensions.yaml"
        )
        assert key[1] == 3

    def test_unknown_path_priority_100(self):
        key = RefinementTriggerRouteResolver._module_path_sort_key(
            "modules/my_mod/other_file.json"
        )
        assert key[1] == 100

    def test_module_id_in_first_position(self):
        key = RefinementTriggerRouteResolver._module_path_sort_key(
            "modules/my_module/module.yaml"
        )
        assert key[0] == "my_module"


# ---------------------------------------------------------------------------
# 9. _pack_ids_from_integration_clients
# ---------------------------------------------------------------------------

class TestPackIdsFromIntegrationClients:
    def test_payment_provider_client_extracted(self):
        paths = ["services/integrations/payment_provider_client.py"]
        result = RefinementTriggerRouteResolver._pack_ids_from_integration_clients(paths)
        assert result == ["payment_provider"]

    def test_multiple_clients(self):
        paths = [
            "services/integrations/payment_provider_client.py",
            "services/integrations/sendgrid_client.py",
        ]
        result = RefinementTriggerRouteResolver._pack_ids_from_integration_clients(paths)
        assert "payment_provider" in result
        assert "sendgrid" in result

    def test_non_matching_path_ignored(self):
        paths = ["app/modules/billing/backend/handler.py"]
        result = RefinementTriggerRouteResolver._pack_ids_from_integration_clients(paths)
        assert result == []

    def test_duplicates_removed(self):
        paths = [
            "services/integrations/payment_provider_client.py",
            "services/integrations/payment_provider_client.py",
        ]
        result = RefinementTriggerRouteResolver._pack_ids_from_integration_clients(paths)
        assert result.count("payment_provider") == 1

    def test_empty_list_returns_empty(self):
        assert RefinementTriggerRouteResolver._pack_ids_from_integration_clients([]) == []

    def test_hyphenated_pack_id(self):
        paths = ["services/integrations/my-provider_client.py"]
        result = RefinementTriggerRouteResolver._pack_ids_from_integration_clients(paths)
        assert result == ["my-provider"]


# ---------------------------------------------------------------------------
# 10. RefinementRequest._normalize_ARTIFACT_KIND (via Pydantic validation)
# ---------------------------------------------------------------------------

class TestNormalizeArtifactKind:
    def test_uppercase_normalized_to_lowercase(self):
        req = RefinementRequest(build_family="APP_BUNDLE")
        assert req.build_family == "app_bundle"

    def test_whitespace_stripped(self):
        req = RefinementRequest(build_family="  app_bundle  ")
        assert req.build_family == "app_bundle"

    def test_enum_value_used(self):
        from mozaiksai.control_plane.implementations.refinement_router import ArtifactKind
        req = RefinementRequest(build_family=ArtifactKind.APP_BUNDLE)
        assert req.build_family == "app_bundle"

    def test_empty_string_raises(self):
        with pytest.raises(ValidationError):
            RefinementRequest(build_family="")

    def test_whitespace_only_raises(self):
        with pytest.raises(ValidationError):
            RefinementRequest(build_family="   ")


# ---------------------------------------------------------------------------
# 11. _request_impact_text
# ---------------------------------------------------------------------------

class TestRequestImpactText:
    def test_combines_request_and_surface(self):
        req = _request("Add a new page", surface="studio")
        intent = _intent()
        result = RefinementTriggerRouteResolver._request_impact_text(request=req, intent=intent)
        assert "add a new page" in result
        assert "studio" in result

    def test_signals_joined(self):
        req = _request("update navigation")
        intent = _intent(signals=["navigation", "shell"])
        result = RefinementTriggerRouteResolver._request_impact_text(request=req, intent=intent)
        assert "navigation" in result
        assert "shell" in result

    def test_result_is_lowercase(self):
        req = _request("ADD NAVIGATION")
        intent = _intent(signals=["SHELL"])
        result = RefinementTriggerRouteResolver._request_impact_text(request=req, intent=intent)
        assert result == result.lower()

    def test_none_surface_handled(self):
        req = _request("change color", surface=None)
        intent = _intent()
        result = RefinementTriggerRouteResolver._request_impact_text(request=req, intent=intent)
        assert "change color" in result

    def test_empty_request_and_no_signals(self):
        req = _request("")
        intent = _intent()
        result = RefinementTriggerRouteResolver._request_impact_text(request=req, intent=intent)
        assert isinstance(result, str)


