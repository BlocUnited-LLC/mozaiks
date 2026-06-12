from __future__ import annotations

import json
from pathlib import Path

import pytest


def _workspace() -> Path:
    return Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (_workspace() / relative_path).read_text(encoding="utf-8")


def test_header_has_single_nav_source_of_truth() -> None:
    source = _read("chat-ui/src/components/layout/Header.js")
    assert "shell-mobile-label" not in source
    assert "activePage?.label" not in source


def test_shell_chrome_main_column_preserves_footer() -> None:
    source = _read("chat-ui/src/components/RouteRenderer.jsx")
    assert '<main className="flex min-h-0 flex-1 flex-col">{children}</main>' in source


def test_transition_screens_use_shell_safe_height() -> None:
    launcher = _read("chat-ui/src/ui/screens/LauncherScreen.jsx")
    confirm = _read("chat-ui/src/ui/screens/ConfirmScreen.jsx")
    transition = _read("chat-ui/src/ui/screens/TransitionScreen.jsx")
    primitives = _read("chat-ui/src/platform/transitionPrimitives.jsx")

    # No screen may use min-h-screen (breaks inside overlay frames)
    assert "min-h-screen" not in launcher
    assert "min-h-screen" not in confirm
    assert "min-h-screen" not in transition
    # Shell-safe height lives in the shared primitives — screens delegate layout there
    assert "min-h-full flex-1" in primitives


def test_platform_transitions_stay_declarative() -> None:
    registry = json.loads(
        _read("factory_app/workflows/extended_orchestration/extension_registry.json")
    )
    assert any(t.get("transition_type") == "user_choice_context" for t in registry["transitions"])
    for transition in registry["transitions"]:
        assert "component" not in transition
        assert "config" not in transition
        assert "description" not in transition
        assert "context" not in transition
        assert "actions" not in transition
        assert transition["transition_type"] in {
            "user_choice",
            "user_choice_context",
            "user_choice_route",
            "confirm",
            "condition",
            "silent",
            "progress_view",
            "prerequisite_redirect",
            "chat_session",
            "workflow_complete",
        }
        if transition["transition_type"] in {"user_choice", "user_choice_context", "user_choice_route", "confirm"}:
            assert transition.get("ui", {}).get("component")
        if transition["transition_type"] in {"user_choice", "user_choice_context", "user_choice_route"}:
            assert "route_to" not in transition
        for option in transition.get("options", []):
            assert "label" not in option
            assert "description" not in option
            assert "context" not in option
            assert option.get("route_to")
            if "context_variables" in option:
                assert isinstance(option["context_variables"], dict)
    assert (_workspace() / "factory_app" / "workflows" / "extended_orchestration" / "ui").exists()
    # App workspace build context should not have a UI bundle (shared factory owns it)
    from tests.import_utils import active_app_root
    app_root = active_app_root()
    if app_root.resolve() == (_workspace() / "factory_app" / "app").resolve():
        pytest.skip("The repo-local factory bundle intentionally owns the shared transition UI bundle.")
    workflows_root = app_root.parent / "workflows" if app_root.name == "app" else app_root / "workflows"
    assert not (workflows_root / "extended_orchestration" / "ui" / "index.js").exists()

