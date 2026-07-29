from __future__ import annotations

import json

import pytest
import yaml

from mozaiksai.core.runtime.app import AppLoader, AppLoadError
from mozaiksai.core.runtime.app.provenance import (
    AppProvenanceLoadError,
    build_default_app_provenance,
    dump_app_provenance_yaml,
    load_app_provenance,
    write_app_provenance,
)


def test_default_app_provenance_round_trips(tmp_path) -> None:
    provenance = build_default_app_provenance(
        app_kind="generated",
        created_mode="factory",
        workflow="AppGenerator",
        workflow_sequence="full_build",
        overlays={"dashboard": "dashboard/dashboard.yaml"},
    )

    path = write_app_provenance(tmp_path, provenance)
    loaded = load_app_provenance(tmp_path)

    assert path.name == "provenance.yaml"
    assert loaded is not None
    assert loaded.app_kind == "generated"
    assert loaded.created_with.mode == "factory"
    assert loaded.created_with.workflow == "AppGenerator"
    assert loaded.contracts["dashboard"] == "mozaiks.dashboard.v1"
    assert loaded.overlays["dashboard"] == "dashboard/dashboard.yaml"


def test_missing_app_provenance_is_optional(tmp_path) -> None:
    assert load_app_provenance(tmp_path) is None

    with pytest.raises(AppProvenanceLoadError, match="provenance.yaml not found"):
        load_app_provenance(tmp_path, required=True)


@pytest.mark.parametrize("path", ["../secret.yaml", "/tmp/app.yaml", "C:/Repos/app.yaml", "https://example.test/app.yaml"])
def test_app_provenance_rejects_unsafe_overlay_paths(path: str) -> None:
    with pytest.raises(ValueError, match="overlays"):
        build_default_app_provenance(
            app_kind="generated",
            created_mode="factory",
            overlays={"dashboard": path},
        )


def test_app_provenance_yaml_omits_null_fields() -> None:
    payload = yaml.safe_load(
        dump_app_provenance_yaml(
            build_default_app_provenance(
                app_kind="hand_authored",
                created_mode="cli",
            )
        )
    )

    assert "workflow" not in payload["created_with"]
    assert "last_refined_with" not in payload


@pytest.mark.asyncio
async def test_app_loader_loads_optional_provenance(tmp_path) -> None:
    (tmp_path / "app.json").write_text(json.dumps({"appName": "Demo"}), encoding="utf-8")
    write_app_provenance(
        tmp_path,
        build_default_app_provenance(
            app_kind="hand_authored",
            created_mode="cli",
            overlays={"refinement_policy": "config/refinement_policy.yaml"},
        ),
    )

    result = await AppLoader.load(str(tmp_path))

    assert result.provenance is not None
    assert result.provenance.app_kind == "hand_authored"
    assert result.provenance.overlays["refinement_policy"] == "config/refinement_policy.yaml"


@pytest.mark.asyncio
async def test_app_loader_rejects_invalid_provenance(tmp_path) -> None:
    (tmp_path / "app.json").write_text(json.dumps({"appName": "Demo"}), encoding="utf-8")
    (tmp_path / "provenance.yaml").write_text(
        """
        schema_version: mozaiks.provenance.v1
        app_kind: generated
        created_with:
          mode: factory
        overlays:
          dashboard: ../dashboard.yaml
        """,
        encoding="utf-8",
    )

    with pytest.raises(AppLoadError, match="Invalid provenance.yaml"):
        await AppLoader.load(str(tmp_path))
