from __future__ import annotations

from mozaiksai.hosts import platform as platform_app


def test_non_runnable_workflow_id_is_rejected() -> None:
    assert platform_app._is_runnable_workflow_name(
        "extended_orchestration",
        ["ValueEngine", "AppGenerator"],
    ) is False


def test_resolve_requested_workflow_prefers_entry_point_for_non_runnable(monkeypatch) -> None:
    monkeypatch.setattr(
        platform_app,
        "_get_ordered_workflow_names",
        lambda: ["ValueEngine", "AppGenerator"],
    )
    monkeypatch.setattr(platform_app, "_get_configured_entry_point", lambda: "AppGenerator")

    assert platform_app._resolve_requested_workflow_name("extended_orchestration") == "AppGenerator"


def test_resolve_requested_workflow_uses_loaded_name_when_known(monkeypatch) -> None:
    monkeypatch.setattr(
        platform_app,
        "_get_ordered_workflow_names",
        lambda: ["ValueEngine", "AppGenerator"],
    )
    monkeypatch.setattr(platform_app, "_get_configured_entry_point", lambda: "AppGenerator")

    assert platform_app._resolve_requested_workflow_name("valueengine") == "ValueEngine"
