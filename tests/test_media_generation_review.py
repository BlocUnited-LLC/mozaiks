"""The declarative review choice survives generation, loading, and AG2 harvesting."""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml
from ag2.events import BinaryResult, ModelMessage, ModelResponse
from pydantic import ValidationError

from factory_app.workflows.AgentGenerator.tools.generate_and_download import _write_bundle_to_disk
from factory_app.workflows.AgentGenerator.tools.hook_primitive_catalog import (
    inject_primitive_catalog,
)
from factory_app.workflows.AgentGenerator.tools.outcome_materialization import (
    materialize_workflow_outcomes,
)
from factory_app.workflows.AgentGenerator.tools.workflow_quality_gate import (
    validate_workflow_bundle_structure,
)
from mozaiksai.core.media import middleware as media_middleware
from mozaiksai.core.media.ag2 import build_ag2_image_generation_tools, prepare_llm_config_for_media
from mozaiksai.core.media.store import LocalMediaContentStore, MediaAssetStore
from mozaiksai.core.media.types import AssetVisibility
from mozaiksai.core.workflow.declarative.contracts import ImageGenerationSpec, parse_agents_config
from tests.test_media_multimodal import _Client, _Collection
from tests.test_workflow_tool_outcomes import _entry


@pytest.mark.parametrize("invalid", [None, "false", "true", 0, 1, [], {}])
def test_media_review_requires_a_boolean(invalid):
    with pytest.raises(ValidationError, match="emit_ui"):
        ImageGenerationSpec.model_validate({"emit_ui": invalid})


@pytest.mark.parametrize("config", [{}, {"emit_ui": True}, {"emit_ui": False}])
@pytest.mark.asyncio
async def test_agent_factory_harvest_preserves_assets_and_applies_review_choice(monkeypatch, tmp_path, config):
    from mozaiksai.core.transport.simple_transport import SimpleTransport
    from mozaiksai.core.workflow import llm_config
    from mozaiksai.core.workflow.agents import factory
    from mozaiksai.core.workflow.agents import tools as agent_tools
    from mozaiksai.core.workflow.outputs import structured

    workflow_config = parse_agents_config({"agents": [{
        "name": "ImageMaker", "system_message": "Create a draft image.",
        "structured_outputs_required": False, "image_generation": {**config, "quality": "low"},
    }]})
    manager = SimpleNamespace(
        get_config=lambda _workflow: workflow_config,
        get_auto_tool_agents=lambda _workflow: set(),
        resolve_workflow_path=lambda _workflow: None,
    )

    async def llm_settings(*args, **kwargs):
        return None, {"config_list": [{"model": "gpt-4.1", "api_type": "openai", "api_key": "test-key"}]}

    monkeypatch.setattr(factory, "workflow_manager", manager)
    monkeypatch.setattr(factory, "load_a2a_agent_specs", lambda _config: {})
    monkeypatch.setattr(factory, "get_structured_outputs_for_workflow", lambda _workflow: {})
    monkeypatch.setattr(llm_config, "get_llm_config", llm_settings)
    monkeypatch.setattr(structured, "get_llm_for_workflow", llm_settings)
    monkeypatch.setattr(agent_tools, "load_agent_tool_functions", lambda _workflow: {})

    collection = _Collection()
    store = MediaAssetStore(client=_Client(collection), content_store=LocalMediaContentStore(root=tmp_path))
    monkeypatch.setattr(media_middleware, "get_media_asset_store", lambda: store)
    transport = SimpleNamespace(send_tool_call_event=AsyncMock())
    get_transport = AsyncMock(return_value=transport)
    monkeypatch.setattr(SimpleTransport, "get_instance", get_transport)
    context = {"app_id": "app-1", "chat_id": "chat-1"}
    agents = await factory.create_agents("DraftImages", context_variables=context)
    wrappers = [
        entry.middleware for entry in agents["ImageMaker"].middleware
        if entry.description.kind == "MozaiksMediaHarvestMiddleware"
    ]
    assert len(wrappers) == 1
    wrapper = wrappers[0]
    assert wrapper.options["emit_ui"] is config.get("emit_ui", True)
    assert "emit_ui" not in wrapper.options["generation_params"]
    response = ModelResponse(
        ModelMessage("Created the draft."), model="test-model", provider="openai",
        files=[BinaryResult(b"draft-image", metadata={"media_type": "image/png"})],
    )
    event = ModelMessage("Create the image.")
    middleware = wrapper(event, SimpleNamespace())
    assert await middleware.on_llm_call(AsyncMock(return_value=response), [event], SimpleNamespace()) is response

    assert len(collection.docs) == 1
    asset_id = collection.docs[0]["asset_id"]
    asset = await store.get_asset(app_id="app-1", asset_id=asset_id)
    assert await store.get_asset_content(asset) == b"draft-image"
    assert asset.asset_visibility == AssetVisibility.PRIVATE
    assert "promotion_status" not in asset.metadata
    assert "emit_ui" not in asset.generation_params
    harvest_context = wrapper.options["context_variables"]
    assert harvest_context.get("generated_media_assets")[0]["asset_id"] == asset_id
    assert harvest_context.get("last_generated_media_assets")[0]["asset_id"] == asset_id
    if config.get("emit_ui", True):
        get_transport.assert_awaited_once()
        transport.send_tool_call_event.assert_awaited_once()
    else:
        get_transport.assert_not_awaited()
        transport.send_tool_call_event.assert_not_awaited()


def test_media_review_choice_does_not_reach_image_provider(monkeypatch):
    import ag2.tools

    tool_calls = []
    monkeypatch.setattr(ag2.tools, "ImageGenerationTool", lambda **kwargs: tool_calls.append(kwargs))
    declaration = {"image_generation": {"emit_ui": False, "quality": "low", "image_config": {"aspect_ratio": "1:1"}}}
    build_ag2_image_generation_tools(declaration, {"config_list": [{"api_type": "openai"}]})
    assert tool_calls == [{"quality": "low"}]
    prepared = prepare_llm_config_for_media(declaration, {"config_list": [{"api_type": "google"}]})
    assert "emit_ui" not in prepared
    assert prepared["image_config"] == {"aspect_ratio": "1:1"}


@pytest.mark.parametrize("config", [{}, {"emit_ui": True}, {"emit_ui": False}])
def test_generated_review_choice_survives_quality_gate_export_and_loading(tmp_path, config):
    entry = _entry()
    agent_file = next(file for file in entry["files"] if file["filename"] == "agents.yaml")
    document = yaml.safe_load(agent_file["content"])
    document["agents"][1]["image_generation"] = config
    agent_file["content"] = yaml.safe_dump(document, sort_keys=False)
    emitted = materialize_workflow_outcomes(entry)
    report = validate_workflow_bundle_structure(bundle_entries=[emitted])
    assert report["valid"], report["errors"]
    directory, _ = _write_bundle_to_disk(emitted["workflow_name"], emitted["files"], tmp_path)
    parsed = parse_agents_config(yaml.safe_load((directory / "agents.yaml").read_text(encoding="utf-8")))
    assert parsed["agents"]["RepairAgent"]["image_generation"]["emit_ui"] is config.get("emit_ui", True)


def test_generated_invalid_review_choice_fails_quality_gate():
    entry = _entry()
    agent_file = next(file for file in entry["files"] if file["filename"] == "agents.yaml")
    document = yaml.safe_load(agent_file["content"])
    document["agents"][1]["image_generation"] = {"emit_ui": "false"}
    agent_file["content"] = yaml.safe_dump(document, sort_keys=False)
    report = validate_workflow_bundle_structure(bundle_entries=[entry])
    assert not report["valid"]
    assert any("emit_ui" in error for error in report["errors"])


def test_generator_receives_canonical_image_schema_without_duplicate_sections():
    agent = SimpleNamespace(name="WorkflowBundleBuilderAgent", _system_message="Build the workflow.")
    inject_primitive_catalog(agent, [])
    first = agent._system_message
    inject_primitive_catalog(agent, [])
    assert agent._system_message == first
    assert first.count("[GENERATED MEDIA REVIEW CONTRACT]") == 1
    schema = json.loads(first.split("[GENERATED MEDIA REVIEW CONTRACT]\n", 1)[1].split("\n", 1)[1])
    assert schema == ImageGenerationSpec.model_json_schema()
    assert schema["properties"]["emit_ui"]["default"] is True
