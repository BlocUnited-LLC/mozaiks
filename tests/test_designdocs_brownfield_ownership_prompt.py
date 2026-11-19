"""Protected existing sources must not be assigned an invented design owner."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from pydantic import ValidationError

from mozaiksai.core.workflow.outputs import structured

WORKFLOW = Path(__file__).resolve().parents[1] / "factory_app/workflows/DesignDocs"


def test_brownfield_prompt_preserves_protected_sources_without_inventing_owner(monkeypatch):
    agents = yaml.safe_load((WORKFLOW / "agents.yaml").read_text(encoding="utf-8"))
    [agent] = [item for item in agents["agents"] if item["name"] == "DesignDocsAgent"]
    [section] = [item for item in agent["prompt_sections"] if item["id"] == "brownfield_scope"]
    guidance = " ".join(section["content"].split())
    assert "read_only_discovered" in guidance
    assert "omit it from surface_map" in guidance
    assert "ownership_boundary" in guidance
    assert "overrides surface candidate hints for protected existing sources" in guidance
    assert "Lists which surfaces are `read_only_discovered` and what read access is permitted" in guidance
    assert '`ownership: generated_adapter` → surface_kind is `external_integration`; owner is `"app"`' in guidance
    assert 'owner: "external"' not in guidance

    config = yaml.safe_load((WORKFLOW / "structured_outputs.yaml").read_text(encoding="utf-8"))
    monkeypatch.setattr(structured, "workflow_manager", SimpleNamespace(
        get_config=lambda workflow: {"structured_outputs": config} if workflow == "DesignDocs" else None,
    ))
    for name in ("_workflow_models", "_workflow_registries", "_workflow_structured_agents"):
        monkeypatch.setattr(structured, name, {})
    models, registry = structured.load_workflow_structured_outputs("DesignDocs")
    assert registry["DesignDocsAgent"] is models["DesignDocsBundle"]
    surface = {
        "surface_id": "approved_adapter", "label": "Approved adapter", "surface_kind": "external_integration",
        "owner": "app", "source_capability_packs": [], "primary_entities": [], "owned_pages": [],
        "owned_mutations": [], "custom_reads": [], "events_emitted": [], "workflow_triggers": [],
        "integrations": [], "notes": None,
    }
    assert models["DesignSurface"].model_validate(surface).owner.value == "app"
    with pytest.raises(ValidationError, match="app.*hosted.*platform"):
        models["DesignSurface"].model_validate({**surface, "owner": "external"})
