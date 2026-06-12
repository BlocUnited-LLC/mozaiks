"""
Build context pure helper unit tests.

Covers:
  iter_context_assets:
    - empty assets → []
    - non-list assets → []
    - asset missing path or kind → skipped
    - non-dict asset → skipped
    - kind filter: matching kind returned, other kinds excluded
    - no kind filter: all valid assets returned
    - kind and path normalized in output

  _read_dotted:
    - simple key → value
    - nested key → nested value
    - missing key → None
    - empty path → None
    - non-mapping at mid-path → None
    - whitespace around parts stripped

  _project_rule:
    - string rule → reads from provider_values; present → (True, value); absent → (False, None)
    - rule with "value" → (True, value)
    - rule with "from" → reads from provider_values
    - rule with "from_context" → reads from context_variables
    - rule with "from_trigger" → reads from trigger_payload
    - rule with "default" → (True, default) when no from resolves
    - non-string non-mapping rule → (False, None)

  resolve_build_context_root:
    - explicit build_context_root → Path resolved
    - MOZAIKS_BUILD_CONTEXT_PATH env var → Path resolved
    - explicit workspace_path → workspace / build_context resolved
    - MOZAIKS_APP_WORKSPACE_PATH env var → workspace / build_context resolved
    - nothing provided → None

  normalize_pack_descriptor:
    - pack section merged as base
    - top-level keys (capabilities, etc.) set as defaults
    - id from descriptor.id
    - id falls back to descriptor.pack_id
    - id falls back to context_root.name
    - status defaults to "active"
    - capability_source defaults to "operator_pack"
    - pack_source_path set to str(context_root)
    - missing id raises BuildContextError

  project_build_context:
    - workflow not in applies_to_workflows → {}
    - workflow in applies_to but no projections → {}
    - projections.context_variables is not a mapping → {}
    - string rule resolved from provider_values
    - value rule always projected
    - from_context rule resolved from context_variables
    - from_trigger rule resolved from trigger_payload
    - rule with no resolved value → key not in output
    - default rule used when from not resolved
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from mozaiksai.core.session.build_context import (
    BuildContextError,
    _project_rule,
    _read_dotted,
    iter_context_assets,
    normalize_pack_descriptor,
    project_build_context,
    resolve_build_context_root,
)

# ---------------------------------------------------------------------------
# 1. iter_context_assets
# ---------------------------------------------------------------------------

class TestIterContextAssets:
    def test_empty_assets_returns_empty(self):
        assert iter_context_assets({"assets": []}) == []

    def test_missing_assets_returns_empty(self):
        assert iter_context_assets({}) == []

    def test_non_list_assets_returns_empty(self):
        assert iter_context_assets({"assets": "not_a_list"}) == []

    def test_asset_missing_path_skipped(self):
        assets = [{"kind": "catalog"}]
        assert iter_context_assets({"assets": assets}) == []

    def test_asset_missing_kind_skipped(self):
        assets = [{"path": "file.yaml"}]
        assert iter_context_assets({"assets": assets}) == []

    def test_non_dict_asset_skipped(self):
        assets = ["not_a_dict", {"path": "f.yaml", "kind": "catalog"}]
        result = iter_context_assets({"assets": assets})
        assert len(result) == 1
        assert result[0]["path"] == "f.yaml"

    def test_kind_filter_returns_matching(self):
        assets = [
            {"path": "a.yaml", "kind": "catalog"},
            {"path": "b.yaml", "kind": "contract"},
        ]
        result = iter_context_assets({"assets": assets}, kind="catalog")
        assert len(result) == 1
        assert result[0]["path"] == "a.yaml"

    def test_kind_filter_excludes_others(self):
        assets = [{"path": "a.yaml", "kind": "contract"}]
        result = iter_context_assets({"assets": assets}, kind="catalog")
        assert result == []

    def test_no_kind_filter_returns_all_valid(self):
        assets = [
            {"path": "a.yaml", "kind": "catalog"},
            {"path": "b.yaml", "kind": "contract"},
        ]
        result = iter_context_assets({"assets": assets})
        assert len(result) == 2

    def test_kind_normalized_in_output(self):
        assets = [{"path": "f.yaml", "kind": "  catalog  "}]
        result = iter_context_assets({"assets": assets})
        # kind is stripped in normalization
        assert result[0]["kind"] == "catalog"

    def test_path_normalized_in_output(self):
        assets = [{"path": "  f.yaml  ", "kind": "catalog"}]
        result = iter_context_assets({"assets": assets})
        assert result[0]["path"] == "f.yaml"

    def test_extra_keys_preserved(self):
        assets = [{"path": "f.yaml", "kind": "catalog", "extra": "value"}]
        result = iter_context_assets({"assets": assets})
        assert result[0]["extra"] == "value"


# ---------------------------------------------------------------------------
# 2. _read_dotted
# ---------------------------------------------------------------------------

class TestReadDotted:
    def test_simple_key_returns_value(self):
        assert _read_dotted({"a": 1}, "a") == 1

    def test_nested_key_returns_value(self):
        source = {"a": {"b": {"c": "deep"}}}
        assert _read_dotted(source, "a.b.c") == "deep"

    def test_missing_key_returns_none(self):
        assert _read_dotted({"a": 1}, "b") is None

    def test_missing_nested_key_returns_none(self):
        source = {"a": {"b": 1}}
        assert _read_dotted(source, "a.c") is None

    def test_non_mapping_at_mid_path_returns_none(self):
        source = {"a": "string"}
        assert _read_dotted(source, "a.b") is None

    def test_empty_path_returns_none(self):
        assert _read_dotted({"a": 1}, "") is None

    def test_whitespace_parts_stripped(self):
        source = {"key": "value"}
        assert _read_dotted(source, " key ") == "value"

    def test_list_value_returned_as_is(self):
        source = {"items": [1, 2, 3]}
        assert _read_dotted(source, "items") == [1, 2, 3]


# ---------------------------------------------------------------------------
# 3. _project_rule
# ---------------------------------------------------------------------------

class TestProjectRule:
    def _call(self, rule: Any, *, provider=None, context=None, trigger=None):
        return _project_rule(
            rule,
            provider_values=provider or {},
            context_variables=context or {},
            trigger_payload=trigger or {},
        )

    def test_string_rule_present_returns_true_value(self):
        has_value, value = self._call("my_key", provider={"my_key": "result"})
        assert has_value is True
        assert value == "result"

    def test_string_rule_absent_returns_false_none(self):
        has_value, value = self._call("missing", provider={"other": "x"})
        assert has_value is False
        assert value is None

    def test_value_rule_always_returns_true(self):
        has_value, value = self._call({"value": 42})
        assert has_value is True
        assert value == 42

    def test_from_rule_reads_provider(self):
        has_value, value = self._call({"from": "x"}, provider={"x": "hello"})
        assert has_value is True
        assert value == "hello"

    def test_from_rule_missing_in_provider_returns_false(self):
        has_value, value = self._call({"from": "x"}, provider={})
        assert has_value is False

    def test_from_context_reads_context(self):
        has_value, value = self._call({"from_context": "k"}, context={"k": "ctx_val"})
        assert has_value is True
        assert value == "ctx_val"

    def test_from_trigger_reads_trigger(self):
        has_value, value = self._call({"from_trigger": "t"}, trigger={"t": "trig_val"})
        assert has_value is True
        assert value == "trig_val"

    def test_default_used_when_from_absent(self):
        has_value, value = self._call({"from": "missing", "default": "fallback"}, provider={})
        assert has_value is True
        assert value == "fallback"

    def test_non_string_non_mapping_returns_false(self):
        has_value, value = self._call(42)
        assert has_value is False
        assert value is None

    def test_none_rule_returns_false(self):
        has_value, value = self._call(None)
        assert has_value is False
        assert value is None


# ---------------------------------------------------------------------------
# 4. resolve_build_context_root
# ---------------------------------------------------------------------------

class TestResolveBuildContextRoot:
    def test_explicit_root_returned_as_path(self, tmp_path: Path):
        root = tmp_path / "my_context"
        result = resolve_build_context_root(build_context_root=root)
        assert result is not None
        assert result == root.resolve()

    def test_env_build_context_path_used(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        root = tmp_path / "env_context"
        monkeypatch.setenv("MOZAIKS_BUILD_CONTEXT_PATH", str(root))
        result = resolve_build_context_root()
        assert result is not None
        assert result == root.resolve()

    def test_workspace_path_appends_build_context(self, tmp_path: Path):
        result = resolve_build_context_root(workspace_path=tmp_path)
        assert result is not None
        assert result == (tmp_path / "build_context").resolve()

    def test_env_workspace_path_appends_build_context(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        monkeypatch.setenv("MOZAIKS_APP_WORKSPACE_PATH", str(tmp_path))
        monkeypatch.delenv("MOZAIKS_BUILD_CONTEXT_PATH", raising=False)
        result = resolve_build_context_root()
        assert result is not None
        assert result == (tmp_path / "build_context").resolve()

    def test_nothing_provided_returns_none(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.delenv("MOZAIKS_BUILD_CONTEXT_PATH", raising=False)
        monkeypatch.delenv("MOZAIKS_APP_WORKSPACE_PATH", raising=False)
        result = resolve_build_context_root()
        assert result is None

    def test_explicit_root_takes_priority_over_env(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        explicit = tmp_path / "explicit"
        env_path = tmp_path / "env"
        monkeypatch.setenv("MOZAIKS_BUILD_CONTEXT_PATH", str(env_path))
        result = resolve_build_context_root(build_context_root=explicit)
        assert result == explicit.resolve()


# ---------------------------------------------------------------------------
# 5. normalize_pack_descriptor
# ---------------------------------------------------------------------------

class TestNormalizePackDescriptor:
    def _call(self, pack_data: dict, root_name: str = "my_context") -> dict:
        context_root = Path("/tmp") / root_name
        return normalize_pack_descriptor(pack_data, context_root=context_root)

    def test_id_from_pack_section(self):
        result = self._call({"pack": {"id": "my_pack"}})
        assert result["id"] == "my_pack"

    def test_id_falls_back_to_pack_id(self):
        result = self._call({"pack": {"pack_id": "fallback_id"}})
        assert result["id"] == "fallback_id"

    def test_id_falls_back_to_context_root_name(self):
        result = self._call({})
        assert result["id"] == "my_context"

    def test_missing_id_empty_root_name_raises(self, tmp_path: Path):
        # context_root with an empty-string name — use a Path whose .name strips to ""
        # In practice, we force the issue by using a mock-like path
        class _EmptyNamePath(type(tmp_path)):
            @property
            def name(self):
                return ""
        context_root = _EmptyNamePath(tmp_path)
        with pytest.raises(BuildContextError):
            normalize_pack_descriptor({}, context_root=context_root)

    def test_status_defaults_to_active(self):
        result = self._call({"pack": {"id": "p"}})
        assert result["status"] == "active"

    def test_capability_source_defaults(self):
        result = self._call({"pack": {"id": "p"}})
        assert result["capability_source"] == "operator_pack"

    def test_pack_source_path_set(self, tmp_path: Path):
        result = normalize_pack_descriptor({"pack": {"id": "p"}}, context_root=tmp_path)
        assert result["pack_source_path"] == str(tmp_path)

    def test_top_level_capabilities_set_as_default(self):
        result = self._call({"pack": {"id": "p"}, "capabilities": [{"capability_id": "x"}]})
        assert result["capabilities"] == [{"capability_id": "x"}]

    def test_pack_section_capabilities_take_priority_over_top_level(self):
        result = self._call({
            "pack": {"id": "p", "capabilities": [{"capability_id": "pack_level"}]},
            "capabilities": [{"capability_id": "top_level"}],
        })
        # pack section is the base; setdefault won't overwrite
        assert result["capabilities"] == [{"capability_id": "pack_level"}]


# ---------------------------------------------------------------------------
# 6. project_build_context
# ---------------------------------------------------------------------------

class TestProjectBuildContext:
    def _call(
        self,
        *,
        workflow_id: str = "MyWorkflow",
        config: dict | None = None,
        provider: dict | None = None,
        context: dict | None = None,
        trigger: dict | None = None,
    ) -> dict:
        return project_build_context(
            workflow_id=workflow_id,
            config=config or {},
            provider_values=provider or {},
            context_variables=context or {},
            trigger_payload=trigger or {},
        )

    def test_workflow_not_in_applies_to_returns_empty(self):
        config = {"applies_to_workflows": ["OtherWorkflow"]}
        result = self._call(workflow_id="MyWorkflow", config=config)
        assert result == {}

    def test_no_applies_to_returns_empty(self):
        result = self._call(config={})
        assert result == {}

    def test_applies_to_but_no_projections_returns_empty(self):
        config = {"applies_to_workflows": ["MyWorkflow"]}
        result = self._call(config=config)
        assert result == {}

    def test_projections_context_variables_not_mapping_returns_empty(self):
        config = {
            "applies_to_workflows": ["MyWorkflow"],
            "projections": {"context_variables": "not_a_dict"},
        }
        result = self._call(config=config)
        assert result == {}

    def test_string_rule_resolved_from_provider(self):
        config = {
            "applies_to_workflows": ["MyWorkflow"],
            "projections": {"context_variables": {"my_var": "provider_key"}},
        }
        result = self._call(config=config, provider={"provider_key": "resolved_value"})
        assert result["my_var"] == "resolved_value"

    def test_value_rule_always_projected(self):
        config = {
            "applies_to_workflows": ["MyWorkflow"],
            "projections": {"context_variables": {"const": {"value": "always"}}},
        }
        result = self._call(config=config)
        assert result["const"] == "always"

    def test_from_context_resolved(self):
        config = {
            "applies_to_workflows": ["MyWorkflow"],
            "projections": {"context_variables": {"k": {"from_context": "existing_key"}}},
        }
        result = self._call(config=config, context={"existing_key": "ctx_result"})
        assert result["k"] == "ctx_result"

    def test_from_trigger_resolved(self):
        config = {
            "applies_to_workflows": ["MyWorkflow"],
            "projections": {"context_variables": {"k": {"from_trigger": "t_key"}}},
        }
        result = self._call(config=config, trigger={"t_key": "trigger_val"})
        assert result["k"] == "trigger_val"

    def test_unresolved_rule_key_omitted(self):
        config = {
            "applies_to_workflows": ["MyWorkflow"],
            "projections": {"context_variables": {"missing": {"from": "no_such_key"}}},
        }
        result = self._call(config=config, provider={})
        assert "missing" not in result

    def test_empty_key_in_projections_skipped(self):
        config = {
            "applies_to_workflows": ["MyWorkflow"],
            "projections": {"context_variables": {"": {"value": "x"}, "real": {"value": "y"}}},
        }
        result = self._call(config=config)
        assert "" not in result
        assert result["real"] == "y"
