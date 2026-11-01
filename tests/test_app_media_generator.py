"""AppMediaGenerator: planned app images become one job each and publish as bundle files."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from factory_app.workflows.AppGenerator.tools.save_app_schema import _validate_asset_manifest
from factory_app.workflows.AppMediaGenerator.tools.compose_media_jobs import (
    IMAGE_RULES,
    compose_media_jobs,
)
from factory_app.workflows.AppMediaGenerator.tools.media_bundle import (
    MAX_GENERATED_IMAGES,
    planned_generated_entries,
)
from factory_app.workflows.AppMediaGenerator.tools.publish_app_media import (
    _latest_asset_by_task,
    _placeholder_svg,
)
from mozaiksai.core.workflow.pack.config import load_global_pack_graph

REPO_ROOT = Path(__file__).resolve().parents[1]


class _Context(dict):
    def set(self, key, value):  # noqa: ANN001
        self[key] = value


def _planned(asset_id: str, *, source: str = "generated", aspect: str | None = "square", **extra) -> dict:
    return {"asset_id": asset_id, "kind": "illustration", "source": source, "aspect": aspect,
            "brief": f"A picture for {asset_id}.", "alt": asset_id, "path": None, "url": None, **extra}


def test_compose_media_jobs_assigns_one_maker_per_requested_shape_and_drops_unknown_ids() -> None:
    context = _Context(media_requests=[
        _planned("hero", aspect="landscape"), _planned("tall", aspect="portrait"), _planned("tile", aspect=None),
    ])
    result = compose_media_jobs(
        MediaArtDirection={"style_guide": "Warm flat illustration.", "images": [
            {"asset_id": "hero", "prompt": "A sunny cafe counter."},
            {"asset_id": "tall", "prompt": "A tall glass of cold brew."},
            {"asset_id": "tile", "prompt": "A croissant."},
            {"asset_id": "hero", "prompt": "A duplicate."},
            {"asset_id": "invented", "prompt": "Not requested."},
        ]},
        context_variables=context,
    )

    assert result == {"outcome": "composed", "job_count": 3, "ignored_asset_ids": ["hero", "invented"]}
    jobs = {job["task_id"]: job for job in context["media_job_items"]}
    assert jobs["hero"]["initial_agent"] == "LandscapeImageMakerAgent"
    assert jobs["tall"]["initial_agent"] == "PortraitImageMakerAgent"
    assert jobs["tile"]["initial_agent"] == "SquareImageMakerAgent"
    assert jobs["hero"]["initial_message"] == (
        f"Art direction: Warm flat illustration.\n\nImage: A sunny cafe counter.\n\nRules: {IMAGE_RULES}"
    )


def test_compose_media_jobs_with_nothing_requested_queues_no_jobs() -> None:
    context = _Context(media_requests=[])
    result = compose_media_jobs(MediaArtDirection={"style_guide": "Any.", "images": []}, context_variables=context)
    assert result["job_count"] == 0
    assert context["media_job_items"] == []


def test_only_planned_generated_entries_are_requested_up_to_the_budget() -> None:
    manifest = {"assets": [
        _planned("photo", source="uploaded"),
        {**_planned("made"), "path": "ui/assets/made.webp"},
        {**_planned("no-brief"), "brief": None},
        *[_planned(f"art-{index}") for index in range(MAX_GENERATED_IMAGES + 2)],
    ]}
    requested = [entry["asset_id"] for entry in planned_generated_entries(manifest)]
    assert requested == [f"art-{index}" for index in range(MAX_GENERATED_IMAGES)]


@pytest.mark.parametrize("entry", [_planned("art"), _planned("photo", source="uploaded")])
def test_asset_manifest_accepts_a_planned_image_with_a_brief(entry: dict) -> None:
    _validate_asset_manifest({"version": "1.0", "assets": [entry]})


@pytest.mark.parametrize(
    ("entry", "message"),
    [
        ({**_planned("art"), "brief": None}, "brief is required"),
        (_planned("local", source="local"), "must include at least one of path or url"),
        ({**_planned("art"), "aspect": "wide"}, "aspect must be one of"),
    ],
)
def test_asset_manifest_rejects_unplannable_entries(entry: dict, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        _validate_asset_manifest({"version": "1.0", "assets": [entry]})


def test_publisher_keeps_the_newest_image_per_job_from_this_batch_only() -> None:
    def asset(asset_id: str, task_id: str, *, batch: str = "app_media_jobs", media_type: str = "image/webp"):
        return SimpleNamespace(
            asset_id=asset_id, media_type=media_type,
            generation_params={"task_batch_id": batch, "task_id": task_id},
        )

    newest_first = [
        asset("m3", "hero"), asset("m2", "hero"), asset("m1", "tile"),
        asset("m0", "other", batch="another_batch"), asset("m4", "clip", media_type="video/mp4"),
    ]
    latest = _latest_asset_by_task(newest_first)
    assert {task: item.asset_id for task, item in latest.items()} == {"hero": "m3", "tile": "m1"}


def test_upload_placeholder_is_labeled_and_escapes_the_brief() -> None:
    svg = _placeholder_svg({"asset_id": "photo", "aspect": "landscape", "alt": 'Latte <b>"art"</b>'})
    assert 'width="1536" height="1024"' in svg
    assert "Photo to upload" in svg
    assert "<b>" not in svg and "&lt;b&gt;" in svg


def test_every_app_generation_sequence_makes_media_before_any_security_review() -> None:
    graph = load_global_pack_graph(REPO_ROOT / "factory_app" / "workflows")
    assert graph is not None
    with_media = []
    for sequence in graph.journeys:
        steps = [step.workflows for step in sequence.steps if step.workflows]
        if ["AppGenerator"] in steps:
            generation = steps.index(["AppGenerator"])
            assert steps[generation + 1] == ["AppMediaGenerator"], sequence.id
        if ["AppMediaGenerator"] in steps:
            position = steps.index(["AppMediaGenerator"])
            assert steps[position - 1] == ["AppGenerator"], sequence.id
            if ["SecurityReadiness"] in steps:
                assert steps[position + 1] == ["SecurityReadiness"], sequence.id
            with_media.append(sequence.id)
    assert sorted(with_media) == [
        "app_revision", "app_surface_revision", "brownfield_module_generation",
        "brownfield_overlay_generation", "build", "conceptual_replan", "design_revision",
        "full_rebuild", "subscription_patch", "subscription_revision", "theme_patch",
        "theme_revision", "workflow_revision",
    ]
    entry = next(item for item in graph.workflows if item.id == "AppMediaGenerator")
    assert entry.dependencies == ["AppGenerator"]


@pytest.fixture(name="asset_client")
def asset_client_fixture(monkeypatch, tmp_path):
    from mozaiksai.hosts.routers.shell import router

    app_root = tmp_path / "app"
    (app_root / "config").mkdir(parents=True)
    (app_root / "app.json").write_text("{}", encoding="utf-8")
    (app_root / "ui" / "assets").mkdir(parents=True)
    (app_root / "ui" / "assets" / "hero.webp").write_bytes(b"RIFF....WEBPimage")
    (app_root / "ui" / "assets" / "notes.txt").write_text("not an image", encoding="utf-8")
    (tmp_path / "outside.png").write_bytes(b"\x89PNG outside")
    (app_root / "config" / "asset_manifest.json").write_text(json.dumps({"version": "1.0", "assets": [
        {"asset_id": "hero", "kind": "hero_image", "source": "generated", "path": "ui/assets/hero.webp"},
        {"asset_id": "planned", "kind": "illustration", "source": "generated", "path": None, "brief": "Later."},
        {"asset_id": "escape", "kind": "illustration", "source": "local", "path": "../outside.png"},
        {"asset_id": "notes", "kind": "document", "source": "local", "path": "ui/assets/notes.txt"},
    ]}), encoding="utf-8")
    monkeypatch.setenv("PLATFORM_PATH", str(app_root))
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_app_asset_route_serves_a_declared_bundle_image_with_a_locked_down_policy(asset_client) -> None:
    response = asset_client.get("/api/app-assets/hero")
    assert response.status_code == 200
    assert response.content == b"RIFF....WEBPimage"
    assert response.headers["content-type"] == "image/webp"
    assert "default-src 'none'" in response.headers["content-security-policy"]
    assert response.headers["x-content-type-options"] == "nosniff"


@pytest.mark.parametrize("asset_id", ["planned", "escape", "notes", "missing"])
def test_app_asset_route_refuses_planned_escaping_non_image_and_unknown_assets(asset_client, asset_id: str) -> None:
    assert asset_client.get(f"/api/app-assets/{asset_id}").status_code == 404
