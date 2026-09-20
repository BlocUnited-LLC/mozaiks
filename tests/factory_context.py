"""Explicit platform-issued context for generator unit-test fixtures."""

from collections.abc import Mapping
from typing import Any

from mozaiksai.core.session.build_binding import RunBuildBinding


def factory_context(initial: Mapping[str, Any] | None = None) -> dict[str, Any]:
    data = dict(initial or {})
    # Existing fixture app IDs describe the output under test, not the host.
    target_app_id = data.pop("app_id", "generated-app")
    build_id = data.pop("build_id", "build-test")
    registry_id = data.pop("build_registry_id", "registry-test")
    data.setdefault("run_build_binding", RunBuildBinding(
        target_app_id=target_app_id,
        build_id=build_id,
        build_registry_id=registry_id,
        phase="genesis",
    ).model_dump())
    data["app_id"] = "factory-test"
    return data
