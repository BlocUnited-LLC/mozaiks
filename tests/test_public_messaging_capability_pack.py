from __future__ import annotations

from pathlib import Path

import yaml


def _workspace() -> Path:
    return Path(__file__).resolve().parents[1]


def _read_yaml(relative_path: str):
    path = _workspace() / relative_path
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _read_text(relative_path: str) -> str:
    path = _workspace() / relative_path
    return path.read_text(encoding="utf-8")


def test_public_messaging_pack_manifest_and_overlay_contracts() -> None:
    manifest = _read_yaml("factory_app/capability_packs/public/messaging/manifest.yaml")
    module_yaml = _read_yaml("factory_app/app/modules/communications/module.yaml")
    events_yaml = _read_yaml("factory_app/app/modules/communications/events.yaml")
    notifications_yaml = _read_yaml("factory_app/app/modules/communications/notifications.yaml")
    settings_yaml = _read_yaml("factory_app/app/modules/communications/settings.yaml")
    admin_yaml = _read_yaml("factory_app/app/modules/communications/admin.yaml")
    channels_yaml = _read_yaml("factory_app/app/modules/communications/channels.yaml")
    page_yaml = _read_yaml("factory_app/app/ui/pages/messages.yaml")

    assert manifest["capability_pack_id"] == "messaging"
    assert manifest["pack_type"] == "messaging_pack"
    assert manifest["delivery_mode"] == "app_embedded"
    assert "communications" in manifest["contributes"]["modules"]
    assert manifest["contributes"]["functions"] == [
        "messaging.resolve_thread_recipients",
        "messaging.validate_announcement_scope",
    ]
    assert manifest["function_entrypoints"] == [
        "functions.notifications_hooks:resolve_thread_recipients",
        "functions.policy_hooks:validate_announcement_scope",
    ]

    assert module_yaml["schema_version"] == "mozaiks.module.v1"
    assert module_yaml["module"]["id"] == "communications"
    assert module_yaml["module"]["type"] == "messaging"
    assert module_yaml["module"]["handler"] == "backend.handler:CommunicationsModule"
    assert [action["id"] for action in module_yaml["actions"]] == [
        "create_thread",
        "send_message",
        "mark_thread_read",
        "post_announcement",
    ]
    assert any(capability["kind"] == "page" and capability["target"] == "/messages" for capability in module_yaml["capabilities"])

    event_types = [event["type"] for event in events_yaml["events"]]
    assert event_types == [
        "domain.communications.thread_created",
        "domain.communications.message_sent",
        "domain.communications.thread_read",
        "domain.communications.announcement_posted",
    ]

    notification_ids = [entry["id"] for entry in notifications_yaml["notifications"]]
    assert notification_ids == [
        "communications.message_sent.in_app",
        "communications.announcement_posted.broadcast",
    ]
    assert settings_yaml["schema_version"] == "mozaiks.settings.v1"
    assert any(feature["id"] == "communications.announcements_enabled" for feature in settings_yaml["features"])

    assert admin_yaml["schema_version"] == "mozaiks.admin.v2"
    assert admin_yaml["panels"][0]["section"] == "operations"

    channel_transports = [channel["transport"] for channel in channels_yaml["channels"]]
    assert channel_transports == ["websocket", "push"]

    assert page_yaml["route"] == "/messages"
    assert page_yaml["layout"] == "split"
    assert [section["id"] for section in page_yaml["sections"]] == [
        "threads-inbox",
        "announcements-feed",
        "messages-guidance",
    ]


def test_public_messaging_pack_functions_and_overlay_cleanup() -> None:
    workspace = _workspace()
    assert not (workspace / "factory_app/capability_packs/public/messaging/app_overlay").exists()

    init_source = _read_text("factory_app/capability_packs/public/messaging/functions/__init__.py")
    notifications_source = _read_text("factory_app/capability_packs/public/messaging/functions/notifications_hooks.py")
    policy_source = _read_text("factory_app/capability_packs/public/messaging/functions/policy_hooks.py")

    assert "resolve_thread_recipients" in init_source
    assert "validate_announcement_scope" in init_source
    assert "async def resolve_thread_recipients" in notifications_source
    assert "def validate_announcement_scope" in policy_source