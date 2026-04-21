from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
PLATFORM = ROOT / "platform"


def _load_yaml(relative_path: str) -> dict:
    with open(PLATFORM / relative_path, "r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def test_platform_runtime_families_exist() -> None:
    assert (PLATFORM / "app.json").exists()
    assert (PLATFORM / "brand").is_dir()
    assert (PLATFORM / "config").is_dir()
    assert (PLATFORM / "operations").is_dir()
    assert (PLATFORM / "pages").is_dir()
    assert (PLATFORM / "workflows").is_dir()


def test_removed_platform_families_stay_removed() -> None:
    assert not (PLATFORM / "automations").exists()
    assert not (PLATFORM / "components").exists()


def test_workflow_triggers_live_in_orchestrators() -> None:
    writers_room = _load_yaml("workflows/WritersRoom/orchestrator.yaml")
    main_stage = _load_yaml("workflows/MainStage/orchestrator.yaml")

    writers_triggers = writers_room.get("triggers") or []
    mainstage_triggers = main_stage.get("triggers") or []

    assert [trigger["event"] for trigger in writers_triggers] == [
        "set.brief_confirmed",
        "set.rewrite_requested",
    ]
    assert [trigger["event"] for trigger in mainstage_triggers] == [
        "set.direction_selected",
    ]
