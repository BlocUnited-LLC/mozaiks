"""Concrete file syntax and native AG2 correction at the shared response boundary."""
from __future__ import annotations

import json
import socket

import pytest
from ag2 import Agent
from ag2.testing import TestConfig as ScriptedConfig
from ag2.testing import TrackingConfig
from pydantic import ValidationError

from mozaiksai.control_plane.contracts import CodingWorkerPlan, CodingWorkerRequest, FileUpdate
from mozaiksai.control_plane.implementations.structured_coding_provider import (
    StructuredOutputCodingProvider,
)
from mozaiksai.control_plane.implementations.surface_regeneration_worker import (
    SurfaceRegenerationResponse,
)
from mozaiksai.core.adapters.llm_fallback import response_schema_to_ag2

_PATH = "ui/pages/notes.yaml"
_INVALID = 'message: Click \x18New Note\x19 to create your first.\n'
_VALID = 'message: Click “New Note” to create your first.\n'


@pytest.mark.parametrize("path", [_PATH, "notes.yml", "notes.YAML", "notes.YML", r"ui\pages\notes.yaml"])
def test_concrete_yaml_rejects_raw_control_characters(path):
    with pytest.raises(ValidationError) as caught:
        FileUpdate(path=path, content=_INVALID)
    error = caught.value.errors(include_input=False)[0]["msg"]
    assert "invalid YAML (ReaderError) at character 15" in error
    assert "New Note" not in error


@pytest.mark.parametrize("content,code", [
    ("title: [unfinished\n", "ParserError"),
    ("message: safe: invalid\n", "ScannerError"),
    ("title: !!python/object:builtins.object {}\n", "ConstructorError"),
])
def test_concrete_yaml_rejects_parse_errors_without_echoing_content(content, code):
    with pytest.raises(ValidationError) as caught:
        FileUpdate(path=_PATH, content=content)
    error = caught.value.errors(include_input=False)[0]["msg"]
    assert f"{_PATH}: invalid YAML ({code}) at line " in error
    assert ", column " in error
    assert content.strip() not in error


@pytest.mark.parametrize("path", ["/private/notes.yaml", "../notes.yaml", "notes\n.yaml", "a" * 500 + ".yaml"])
def test_yaml_error_path_is_safe_and_bounded(path):
    with pytest.raises(ValidationError) as caught:
        FileUpdate(path=path, content=_INVALID)
    error = caught.value.errors(include_input=False)[0]["msg"]
    assert len(error) < 300
    assert "\n" not in error
    assert "/private" not in error and "../" not in error


@pytest.mark.parametrize("content", [
    _VALID, 'title: "\\u0018"\n', "title: '\\u0018'\n", "message: |\n  café — 星\n",
    "", "null\n", "- one\n- two\n", "plain scalar\n",
])
def test_valid_yaml_bytes_remain_unchanged_without_schema_admission(content):
    assert FileUpdate(path=_PATH, content=content).content == content


@pytest.mark.parametrize("path", ["data.json", "handler.py", "page.jsx", "notes.md", "notes.yaml.j2"])
def test_non_yaml_content_is_unchanged(path):
    assert FileUpdate(path=path, content=_INVALID).content == _INVALID


def _response(model, content):
    result = {"summary": "Update the selected page", "rationale": "Preserve the selected scope",
              "updated_files": [{"path": _PATH, "content": content}]}
    if model is CodingWorkerPlan:
        result.update(owned_paths=[_PATH], validation_strategy="docker", validation_commands=[],
                      start_preview=False, needs_human_review=False)
    return json.dumps(result)


def _message_texts(call):
    for message in call.messages:
        if isinstance(getattr(message, "content", None), str):
            yield message.content
        for part in getattr(message, "parts", []):
            if isinstance(getattr(part, "content", None), str):
                yield part.content


@pytest.mark.asyncio
@pytest.mark.parametrize("model", [CodingWorkerPlan, SurfaceRegenerationResponse])
@pytest.mark.parametrize("mode", ["native", "prompted"])
@pytest.mark.parametrize("outcome,retries,expected_calls", [("corrected", 2, 2), ("exhausted", 2, 3), ("zero", 0, 1)])
async def test_native_ag2_repairs_yaml_with_production_response_models(
    monkeypatch, model, mode, outcome, retries, expected_calls,
):
    """Actual ask/content and error feedback; scripted model transport, no provider."""
    def deny_network(*args, **kwargs):
        pytest.fail("Native structured-output regression must remain offline")

    monkeypatch.setattr(socket.socket, "connect", deny_network)
    monkeypatch.setattr(socket, "getaddrinfo", deny_network)
    rejected = _response(model, _INVALID)
    responses = [rejected, _response(model, _VALID)] if outcome == "corrected" else [rejected] * expected_calls
    tracking = TrackingConfig(ScriptedConfig(*responses))
    scope_prompt = json.dumps({"selected_file_paths": [_PATH], "instruction": "Preserve existing roles and auth."})
    agent = Agent("YamlContractRegression", prompt="Change only the explicitly selected file.", config=tracking)
    reply = await agent.ask(scope_prompt, response_schema=response_schema_to_ag2(model, {"structured_output_mode": mode}))
    if outcome == "corrected":
        result = await reply.content(retries=retries)
        assert isinstance(result, model)
        assert result.updated_files == [FileUpdate(path=_PATH, content=_VALID)]
    else:
        with pytest.raises(ValidationError, match="invalid YAML"):
            await reply.content(retries=retries)
    assert len(tracking.calls) == expected_calls
    for call in tracking.calls[1:]:
        messages = list(_message_texts(call))
        assert scope_prompt in messages
        assert rejected in messages
        feedback = [text for text in messages if "== Validation Error ==" in text]
        assert feedback
        assert all(f"{_PATH}: invalid YAML (ReaderError) at character 15" in text for text in feedback)


def test_syntax_validation_does_not_grant_file_scope():
    plan = CodingWorkerPlan.model_validate_json(_response(CodingWorkerPlan, _VALID))
    request = CodingWorkerRequest(
        app_id="app", user_id="owner", build_family="app_bundle", change_class="patch",
        files={"ui/pages/other.yaml": "title: Other"},
    )
    with pytest.raises(ValueError, match="outside the explicit scoped files"):
        StructuredOutputCodingProvider._resolve_updated_files(request=request, plan=plan)


def test_file_update_wire_fields_remain_required_strings():
    schema = FileUpdate.model_json_schema()
    assert set(schema["properties"]) == set(schema["required"]) == {"path", "content"}
    assert all(field["type"] == "string" for field in schema["properties"].values())
    assert schema["additionalProperties"] is False
