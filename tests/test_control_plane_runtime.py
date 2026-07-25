from __future__ import annotations

from pathlib import Path

import mozaiksai.control_plane.runtime as control_plane_runtime
from mozaiksai.control_plane import (
    ControlPlaneCheckpointManifest,
    ControlPlaneCheckpointRuntime,
    ControlPlaneManifest,
    ControlPlanePromptsManifest,
    ControlPlaneToolExecutor,
    ControlPlaneToolsManifest,
    LoadedControlPlanePack,
    OrchestrationControlHarness,
    build_selected_control_plane_harness,
    load_refinement_harness,
)


def test_checkpoint_runtime_binds_and_caches_handlers(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "example_control_plane.py").write_text(
        "\n".join(
            [
                "class ExampleHandler:",
                "    def __init__(self, *, pack_loader=None, tool_executor=None, dependency=None):",
                "        self.pack_loader = pack_loader",
                "        self.tool_executor = tool_executor",
                "        self.dependency = dependency",
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))

    pack = LoadedControlPlanePack(
        path=tmp_path,
        manifest=ControlPlaneManifest(
            schema_version="mozaiks.refinement_harness.v1",
            checkpoints=[
                ControlPlaneCheckpointManifest(
                    event="route_requested",
                )
            ],
        ),
        prompts=ControlPlanePromptsManifest(
            schema_version="mozaiks.refinement_harness.v1.prompts",
            prompts=[],
        ),
        tools=ControlPlaneToolsManifest(
            schema_version="mozaiks.refinement_harness.tools.v1",
            tools=[],
        ),
    )

    runtime = ControlPlaneCheckpointRuntime(
        pack_loader=lambda: pack,
        pack=pack,
        tool_executor=ControlPlaneToolExecutor(pack_loader=lambda: pack),
    )
    monkeypatch.setitem(
        control_plane_runtime.CHECKPOINT_HANDLER_ENTRYPOINTS,
        "route_requested",
        "example_control_plane:ExampleHandler",
    )
    bound = runtime.bind_checkpoint("route_requested", dependency="first")

    assert bound.dependency == "first"
    assert runtime.get_checkpoint("route_requested") is bound
    assert runtime.has_checkpoint("route_requested") is True
    assert runtime.checkpoint_events() == ["route_requested"]


def test_build_selected_control_plane_harness_uses_checkpoint_runtime_for_default_profile() -> None:
    app_root = Path(__file__).resolve().parents[1] / "factory_app" / "app"
    pack = load_refinement_harness(app_root=app_root)

    harness = build_selected_control_plane_harness(pack_loader=lambda: pack)

    assert isinstance(harness, OrchestrationControlHarness)
    assert harness._checkpoint_runtime is not None
    assert harness._checkpoint_runtime.get_checkpoint("request_submitted") is harness._refinement_resolver._classifier
    assert harness._checkpoint_runtime.get_checkpoint("scope_requested") is harness._scope_proposer
    assert harness._checkpoint_runtime.get_checkpoint("coding_requested") is harness._coding_worker
