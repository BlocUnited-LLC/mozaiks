# ==============================================================================
# FILE: tests/test_structured_output_cache_invalidation.py
# DESCRIPTION: Structured-output compiled-model caches must be invalidated by
#              every workflow-manager operation that replaces or removes
#              workflow configuration (reload_workflow, unload_workflow,
#              refresh_all), so compiled Pydantic models never outlive the
#              config they were built from.
# ==============================================================================

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from mozaiksai.core.workflow import workflow_manager as _wm_mod
from mozaiksai.core.workflow.outputs import structured as _so
from mozaiksai.core.workflow.workflow_manager import workflow_manager

_AGENT_TEMPLATE = (
    "  - name: {name}\n"
    "    structured_outputs_required: true\n"
    "    prompt_sections:\n"
    "      - id: role\n"
    "        heading: ROLE\n"
    "        content: probe\n"
)


def _write_workflow(
    root: Path,
    workflow_name: str,
    *,
    field_name: str = "field_a",
    registry_agent: str = "ProbeAgent",
) -> Path:
    workflow_dir = root / workflow_name
    workflow_dir.mkdir(parents=True, exist_ok=True)
    (workflow_dir / "orchestrator.yaml").write_text(
        f"workflow_name: {workflow_name}\n"
        "workflow_startup_mode: BackendOnly\n"
        "initial_agent: ProbeAgent\n"
        "max_turns: 1\n",
        encoding="utf-8",
    )
    (workflow_dir / "agents.yaml").write_text(
        "agents:\n"
        + _AGENT_TEMPLATE.format(name="ProbeAgent")
        + _AGENT_TEMPLATE.format(name="OtherAgent"),
        encoding="utf-8",
    )
    (workflow_dir / "structured_outputs.yaml").write_text(
        "models:\n"
        "  ProbeOutput:\n"
        "    type: model\n"
        "    fields:\n"
        f"      {field_name}: {{ type: str }}\n"
        "registry:\n"
        f"  {registry_agent}: ProbeOutput\n",
        encoding="utf-8",
    )
    return workflow_dir


@pytest.fixture
def isolated_manager(tmp_path: Path):
    """Point the live manager singleton at a tmp workflow root, restoring all
    manager and structured-output cache state afterward.

    outputs.structured binds the singleton object at import time, so tests must
    mutate that exact object rather than swapping UnifiedWorkflowManager._instance.
    """
    saved_manager_state = dict(workflow_manager.__dict__)
    saved_structured = (
        dict(_so._workflow_models),
        dict(_so._workflow_registries),
        dict(_so._workflow_structured_agents),
        dict(_so._provider_response_model_cache),
    )
    try:
        workflow_manager.workflows_base_path = tmp_path
        workflow_manager._workflows = {}
        workflow_manager._workflow_paths = {}
        workflow_manager._config_cache = {}
        _so._workflow_models.clear()
        _so._workflow_registries.clear()
        _so._workflow_structured_agents.clear()
        _so._provider_response_model_cache.clear()
        yield workflow_manager
    finally:
        workflow_manager.__dict__.clear()
        workflow_manager.__dict__.update(saved_manager_state)
        _so._workflow_models.clear()
        _so._workflow_models.update(saved_structured[0])
        _so._workflow_registries.clear()
        _so._workflow_registries.update(saved_structured[1])
        _so._workflow_structured_agents.clear()
        _so._workflow_structured_agents.update(saved_structured[2])
        _so._provider_response_model_cache.clear()
        _so._provider_response_model_cache.update(saved_structured[3])


def _load(manager, name: str) -> None:
    info = manager.reload_workflow(name)
    assert not info.get("error"), info


def test_initial_load_compiles_and_caches(isolated_manager, tmp_path):
    _write_workflow(tmp_path, "CacheProbe")
    _load(isolated_manager, "CacheProbe")

    models, registry = _so.load_workflow_structured_outputs("CacheProbe")
    assert list(models["ProbeOutput"].model_fields) == ["field_a"]
    assert list(registry) == ["ProbeAgent"]
    assert _so.get_structured_output_agents("CacheProbe") == ["ProbeAgent"]
    # Second call serves the identical compiled object (cache hit).
    models_again, _ = _so.load_workflow_structured_outputs("CacheProbe")
    assert models_again["ProbeOutput"] is models["ProbeOutput"]


def test_reload_serves_replacement_models_registry_and_agent_set(isolated_manager, tmp_path):
    _write_workflow(tmp_path, "CacheProbe", field_name="field_a", registry_agent="ProbeAgent")
    _load(isolated_manager, "CacheProbe")
    models_a, _ = _so.load_workflow_structured_outputs("CacheProbe")

    _write_workflow(tmp_path, "CacheProbe", field_name="field_b", registry_agent="OtherAgent")
    _load(isolated_manager, "CacheProbe")

    models_b, registry_b = _so.load_workflow_structured_outputs("CacheProbe")
    assert list(models_b["ProbeOutput"].model_fields) == ["field_b"]
    assert list(registry_b) == ["OtherAgent"]
    assert _so.get_structured_output_agents("CacheProbe") == ["OtherAgent"]
    assert models_b["ProbeOutput"] is not models_a["ProbeOutput"]


def test_stale_schema_identity_is_rejected_after_reload(isolated_manager, tmp_path):
    _write_workflow(tmp_path, "CacheProbe", field_name="field_a")
    _load(isolated_manager, "CacheProbe")
    models_a, _ = _so.load_workflow_structured_outputs("CacheProbe")
    schema_a = json.dumps(models_a["ProbeOutput"].model_json_schema(), sort_keys=True)

    _write_workflow(tmp_path, "CacheProbe", field_name="field_b")
    _load(isolated_manager, "CacheProbe")
    models_b, _ = _so.load_workflow_structured_outputs("CacheProbe")
    schema_b = json.dumps(models_b["ProbeOutput"].model_json_schema(), sort_keys=True)

    assert schema_a != schema_b
    # A payload shaped for the prior schema no longer validates.
    with pytest.raises(ValidationError):
        models_b["ProbeOutput"].model_validate({"field_a": "value"})
    assert models_b["ProbeOutput"].model_validate({"field_b": "value"}).field_b == "value"


def test_compiled_schema_is_deterministic_across_cold_compiles(isolated_manager, tmp_path):
    _write_workflow(tmp_path, "CacheProbe", field_name="field_a")
    _load(isolated_manager, "CacheProbe")
    models_first, _ = _so.load_workflow_structured_outputs("CacheProbe")
    schema_first = json.dumps(models_first["ProbeOutput"].model_json_schema(), sort_keys=True)

    _so.invalidate_workflow_structured_outputs("CacheProbe")
    models_second, _ = _so.load_workflow_structured_outputs("CacheProbe")
    schema_second = json.dumps(models_second["ProbeOutput"].model_json_schema(), sort_keys=True)

    assert schema_first == schema_second


def test_unload_invalidates_only_target_workflow(isolated_manager, tmp_path):
    _write_workflow(tmp_path, "ProbeOne")
    _write_workflow(tmp_path, "ProbeTwo")
    _load(isolated_manager, "ProbeOne")
    _load(isolated_manager, "ProbeTwo")
    _so.load_workflow_structured_outputs("ProbeOne")
    models_two, _ = _so.load_workflow_structured_outputs("ProbeTwo")

    isolated_manager.unload_workflow("ProbeOne")

    assert "ProbeOne" not in _so._workflow_models
    assert "ProbeOne" not in _so._workflow_registries
    assert "ProbeOne" not in _so._workflow_structured_agents
    # Untouched workflow keeps the identical compiled object.
    still_two, _ = _so.load_workflow_structured_outputs("ProbeTwo")
    assert still_two["ProbeOutput"] is models_two["ProbeOutput"]
    # Unloaded workflow fails closed on the next load.
    with pytest.raises(ValueError):
        _so.load_workflow_structured_outputs("ProbeOne")


def test_reload_does_not_evict_other_workflows(isolated_manager, tmp_path):
    _write_workflow(tmp_path, "ProbeOne")
    _write_workflow(tmp_path, "ProbeTwo")
    _load(isolated_manager, "ProbeOne")
    _load(isolated_manager, "ProbeTwo")
    _so.load_workflow_structured_outputs("ProbeOne")
    models_two, _ = _so.load_workflow_structured_outputs("ProbeTwo")

    _write_workflow(tmp_path, "ProbeOne", field_name="field_b")
    _load(isolated_manager, "ProbeOne")

    still_two, _ = _so.load_workflow_structured_outputs("ProbeTwo")
    assert still_two["ProbeOutput"] is models_two["ProbeOutput"]


def test_refresh_all_invalidates_every_workflow(isolated_manager, tmp_path):
    _write_workflow(tmp_path, "ProbeOne")
    _write_workflow(tmp_path, "ProbeTwo")
    _load(isolated_manager, "ProbeOne")
    _load(isolated_manager, "ProbeTwo")
    models_one, _ = _so.load_workflow_structured_outputs("ProbeOne")
    models_two, _ = _so.load_workflow_structured_outputs("ProbeTwo")

    _write_workflow(tmp_path, "ProbeOne", field_name="field_b")
    _write_workflow(tmp_path, "ProbeTwo", field_name="field_b")
    isolated_manager.refresh_all()

    fresh_one, _ = _so.load_workflow_structured_outputs("ProbeOne")
    fresh_two, _ = _so.load_workflow_structured_outputs("ProbeTwo")
    assert list(fresh_one["ProbeOutput"].model_fields) == ["field_b"]
    assert list(fresh_two["ProbeOutput"].model_fields) == ["field_b"]
    assert fresh_one["ProbeOutput"] is not models_one["ProbeOutput"]
    assert fresh_two["ProbeOutput"] is not models_two["ProbeOutput"]


def test_invalidation_is_idempotent(isolated_manager, tmp_path):
    _write_workflow(tmp_path, "CacheProbe")
    _load(isolated_manager, "CacheProbe")
    _so.load_workflow_structured_outputs("CacheProbe")

    _so.invalidate_workflow_structured_outputs("CacheProbe")
    _so.invalidate_workflow_structured_outputs("CacheProbe")
    _so.invalidate_workflow_structured_outputs("NeverLoaded")
    _so.invalidate_workflow_structured_outputs("")

    models, _ = _so.load_workflow_structured_outputs("CacheProbe")
    assert list(models["ProbeOutput"].model_fields) == ["field_a"]


def test_invalidation_matches_cache_keys_case_insensitively(isolated_manager, tmp_path):
    _write_workflow(tmp_path, "CacheProbe")
    _load(isolated_manager, "CacheProbe")
    # Cache the compiled state under two raw-name aliases of one workflow.
    _so.load_workflow_structured_outputs("CacheProbe")
    _so.load_workflow_structured_outputs("cacheprobe")
    assert {"CacheProbe", "cacheprobe"} <= set(_so._workflow_models)

    _so.invalidate_workflow_structured_outputs("CACHEPROBE")

    assert "CacheProbe" not in _so._workflow_models
    assert "cacheprobe" not in _so._workflow_models
    assert "CacheProbe" not in _so._workflow_registries
    assert "CacheProbe" not in _so._workflow_structured_agents


def test_invalidation_drops_provider_response_model_entries(isolated_manager, tmp_path):
    _write_workflow(tmp_path, "CacheProbe")
    _load(isolated_manager, "CacheProbe")
    models, _ = _so.load_workflow_structured_outputs("CacheProbe")
    probe_cls = models["ProbeOutput"]
    _so._provider_response_model_cache[probe_cls] = probe_cls

    _so.invalidate_workflow_structured_outputs("CacheProbe")

    assert probe_cls not in _so._provider_response_model_cache


def test_malformed_replacement_config_fails_closed(isolated_manager, tmp_path):
    workflow_dir = _write_workflow(tmp_path, "CacheProbe")
    _load(isolated_manager, "CacheProbe")
    _so.load_workflow_structured_outputs("CacheProbe")

    (workflow_dir / "agents.yaml").write_text("- not\n- a\n- mapping\n", encoding="utf-8")
    info = isolated_manager.reload_workflow("CacheProbe")
    assert info.get("error")

    # The failed replacement must not leave prior models or prior config live.
    assert isolated_manager.get_config("CacheProbe") == {}
    with pytest.raises(ValueError):
        _so.load_workflow_structured_outputs("CacheProbe")


def test_failed_reload_leaves_every_lifecycle_surface_consistent(isolated_manager, tmp_path):
    workflow_dir = _write_workflow(tmp_path, "CacheProbe")
    _load(isolated_manager, "CacheProbe")
    _so.load_workflow_structured_outputs("CacheProbe")
    assert "CacheProbe" in isolated_manager.list_loaded_workflows()

    (workflow_dir / "agents.yaml").write_text("- not\n- a\n- mapping\n", encoding="utf-8")
    info = isolated_manager.reload_workflow("CacheProbe")
    assert info.get("error")

    # Every lifecycle surface must agree: the workflow is not successfully
    # loaded anywhere after the replacement config failed validation.
    assert isolated_manager.get_config("CacheProbe") == {}
    workflow_info = isolated_manager.get_workflow_info("CacheProbe")
    assert workflow_info is not None
    assert workflow_info["status"] == "error"
    assert workflow_info["error"]
    assert workflow_info["config"] == {}
    assert "CacheProbe" not in isolated_manager.list_loaded_workflows()
    assert "CacheProbe" not in isolated_manager.get_all_workflow_names()
    summary = isolated_manager.get_status_summary()
    assert summary["loaded_workflows"] == 0
    assert summary["error_workflows"] == 1
    assert "CacheProbe" not in _so._workflow_models
    with pytest.raises(ValueError):
        _so.load_workflow_structured_outputs("CacheProbe")


def test_initialize_workflows_root_switch_invalidates_all(isolated_manager, tmp_path):
    root_a = tmp_path / "root_a"
    root_b = tmp_path / "root_b"
    _write_workflow(root_a, "CacheProbe", field_name="field_a", registry_agent="ProbeAgent")
    _write_workflow(root_b, "CacheProbe", field_name="field_b", registry_agent="OtherAgent")

    _wm_mod.initialize_workflows(base_path=str(root_a))
    models_a, _ = _so.load_workflow_structured_outputs("CacheProbe")
    probe_cls_a = models_a["ProbeOutput"]
    assert list(probe_cls_a.model_fields) == ["field_a"]
    schema_a = json.dumps(probe_cls_a.model_json_schema(), sort_keys=True)
    _so._provider_response_model_cache[probe_cls_a] = probe_cls_a

    _wm_mod.initialize_workflows(base_path=str(root_b))

    config_b = isolated_manager.get_config("CacheProbe")
    assert list(config_b["structured_outputs"]["models"]["ProbeOutput"]["fields"]) == ["field_b"]
    models_b, registry_b = _so.load_workflow_structured_outputs("CacheProbe")
    assert list(models_b["ProbeOutput"].model_fields) == ["field_b"]
    assert list(registry_b) == ["OtherAgent"]
    assert _so.get_structured_output_agents("CacheProbe") == ["OtherAgent"]
    assert models_b["ProbeOutput"] is not probe_cls_a
    schema_b = json.dumps(models_b["ProbeOutput"].model_json_schema(), sort_keys=True)
    assert schema_a != schema_b
    # Stale provider response-model entries built from the replaced class are gone.
    assert probe_cls_a not in _so._provider_response_model_cache


def test_loader_failure_writes_no_partial_cache(isolated_manager, monkeypatch):
    # A registry row naming an unknown model is rejected by the manager's YAML
    # contract at load time, so inject it at the get_config seam: the loader
    # builds the models, then raises on the registry row — after model
    # compilation but before any cache write.
    bad_config = {
        "structured_outputs": {
            "models": {
                "ProbeOutput": {"type": "model", "fields": {"field_a": {"type": "str"}}}
            },
            "registry": {"ProbeAgent": "MissingModel"},
        }
    }
    monkeypatch.setattr(isolated_manager, "get_config", lambda name: bad_config)

    with pytest.raises(ValueError):
        _so.load_workflow_structured_outputs("CacheProbe")
    assert "CacheProbe" not in _so._workflow_models
    assert "CacheProbe" not in _so._workflow_registries
    assert "CacheProbe" not in _so._workflow_structured_agents


def test_manager_module_exposes_no_private_cache_reaching():
    """The manager must invoke the invalidation seam, not reach into the
    structured-output module's private cache dicts."""
    source = Path(_wm_mod.__file__).read_text(encoding="utf-8")
    assert "_workflow_models" not in source
    assert "_workflow_registries" not in source
    assert "_workflow_structured_agents" not in source
    assert "invalidate_workflow_structured_outputs" in source
    assert "invalidate_all_workflow_structured_outputs" in source
