from __future__ import annotations

import pytest

from factory_app.control_plane.change_classifier import (
    ChangeClassifierResult,
    LLMChangeClassifier,
)
from mozaiksai.core.control_plane import (
    ControlPlaneCapabilityConfig,
    ControlPlaneConfig,
)


class _FakeCapabilityService:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def generate_json_completion(self, **kwargs):  # noqa: ANN003
        self.calls.append(kwargs)
        return {
            "content": '{"change_class":"feature","rationale":"Adds a new capability.","confidence":0.8,"signals":["new_capability"]}',
            "parsed": {
                "change_class": "feature",
                "rationale": "Adds a new capability.",
                "confidence": 0.8,
                "signals": ["new_capability"],
            },
            "usage": {},
        }


def _enabled_control_plane() -> ControlPlaneConfig:
    return ControlPlaneConfig(
        enabled=True,
        classifier=ControlPlaneCapabilityConfig(
            enabled=True,
            llm_config={
                "model": "gpt-4o-mini",
                "temperature": 0.0,
            },
        ),
    )


@pytest.mark.asyncio
async def test_change_classifier_uses_control_plane_llm_config() -> None:
    service = _FakeCapabilityService()
    classifier = LLMChangeClassifier(
        capability_service=service,
        config_loader=_enabled_control_plane,
    )

    result = await classifier.classify(
        artifact_kind="app_bundle",
        raw_user_request="Add exports for reporting",
        app_id="app_1",
    )

    assert isinstance(result, ChangeClassifierResult)
    assert result.change_class == "feature"
    assert result.rationale == "Adds a new capability."
    assert result.confidence == 0.8
    assert result.signals == ["new_capability"]
    assert len(service.calls) == 1
    assert service.calls[0]["llm_config"] == {
        "model": "gpt-4o-mini",
        "temperature": 0.0,
    }
    assert service.calls[0]["temperature"] == 0.0


@pytest.mark.asyncio
async def test_change_classifier_requires_enabled_control_plane() -> None:
    classifier = LLMChangeClassifier(
        capability_service=_FakeCapabilityService(),
        config_loader=lambda: ControlPlaneConfig(enabled=False),
    )

    with pytest.raises(RuntimeError, match="Control-plane harness is disabled"):
        await classifier.classify(
            artifact_kind="app_bundle",
            raw_user_request="Add exports for reporting",
            app_id="app_1",
        )
