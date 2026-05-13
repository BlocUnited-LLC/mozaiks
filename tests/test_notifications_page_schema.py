from __future__ import annotations

from pathlib import Path

import yaml
import json


def _workspace() -> Path:
    return Path(__file__).resolve().parents[1]


def test_notifications_page_is_canonical_yaml_ui_proof() -> None:
    page_path = _workspace() / "factory_app" / "app" / "ui" / "pages" / "notifications.yaml"
    page = yaml.safe_load(page_path.read_text(encoding="utf-8"))

    assert page["name"] == "notifications"
    assert page["route"] == "/notifications"
    assert [section["primitive"] for section in page["sections"]] == [
        "PageHeader",
        "ResourceTable",
    ]

    table = page["sections"][1]["config"]
    assert table["api_endpoint"] == "/api/notifications"
    assert table["data_key"] == "notifications"
    assert table["search"] is True
    assert table["default_sort"] == "recent"
    assert table["empty"]["title"] == "No notifications"
    assert table["empty"]["error_title"] == "Could not load notifications"


def test_shell_notification_dropdown_links_to_yaml_notification_center() -> None:
    header_path = _workspace() / "chat-ui" / "src" / "components" / "layout" / "Header.js"
    shell_path = _workspace() / "factory_app" / "app" / "config" / "shell.json"
    source = header_path.read_text(encoding="utf-8")
    shell = json.loads(shell_path.read_text(encoding="utf-8"))

    assert shell["notifications"]["path"] == "/notifications"
    assert shell["notifications"]["emptyText"] == "No unread notifications"
    assert "const notificationsPath = notificationsConfig.path;" in source
    assert "View all notifications" in source


def test_app_registry_yaml_page_does_not_duplicate_apps_console() -> None:
    page_path = _workspace() / "factory_app" / "app" / "ui" / "pages" / "app-registry.yaml"

    assert not page_path.exists()
