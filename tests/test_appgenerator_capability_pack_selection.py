from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from mozaiksai.core.runtime.app.module_loader import ModuleLoader

REPO_ROOT = Path(__file__).resolve().parents[1]
BUILD_CONTEXT = REPO_ROOT / "factory_app" / "build_context"


def _read_yaml(relative_path: str):
    return yaml.safe_load((REPO_ROOT / relative_path).read_text(encoding="utf-8"))


def _pack_path(pack_id: str) -> Path:
    return BUILD_CONTEXT / pack_id


def _output_paths(entries: list) -> list[str]:
    return [entry if isinstance(entry, str) else entry.get("path", entry.get("id", "")) for entry in entries]


def test_capability_routing_registers_messaging_support_and_social_separately() -> None:
    routing = _read_yaml("factory_app/build_context/AppGenerator/capability_routing.yaml")
    packs = {pack["id"]: pack for pack in routing["layers"]["capability_pack"]["packs"]}

    assert {"messaging", "support", "social"} <= set(packs)
    assert packs["support"]["requires"] == ["messaging"]
    assert "support request metadata" in packs["support"]["covers"]
    assert "friends" in packs["social"]["covers"].lower()
    assert "social_graph" not in packs


def test_capability_directory_separates_conversation_support_and_social_capabilities() -> None:
    directory = _read_yaml("factory_app/build_context/AppGenerator/capability_directory.yaml")
    by_id = {capability["id"]: capability for capability in directory["capabilities"]}

    assert {"messaging_pack", "support_pack", "social_pack"} <= set(by_id)
    assert "messaging.contacts.list" not in by_id["messaging_pack"].get("capabilities_provided", [])
    assert "support.requests.create" in by_id["support_pack"].get("capabilities_provided", [])
    assert "social.friends.connect" in by_id["social_pack"].get("capabilities_provided", [])
    support_notes = " ".join(by_id["support_pack"].get("generator_notes", []))
    assert "requires messaging_pack" in support_notes


def test_capability_directory_prioritizes_mozaikspay_for_saas_monetization() -> None:
    directory = _read_yaml("factory_app/build_context/AppGenerator/capability_directory.yaml")
    by_id = {capability["id"]: capability for capability in directory["capabilities"]}

    mozaikspay = by_id["mozaikspay"]
    notes = " ".join(mozaikspay.get("generator_notes", []))

    assert mozaikspay["recommendation_rank"] == 1
    assert mozaikspay["capability_kind"] == "operator_pack"
    assert {"billing", "subscriptions", "usage", "saas"} <= set(mozaikspay["domains"])
    assert "Prioritize the mozaikspay capability pack" in notes
    assert "billing_portal facade" in notes
    assert "mozaikspay_client.py" in notes
    assert "Do not generate checkout, webhook handlers" in notes


def test_capability_routing_defaults_subscriptions_to_mozaikspay_pack() -> None:
    routing = _read_yaml("factory_app/build_context/AppGenerator/capability_routing.yaml")
    entries = routing["layers"]["monetization"]["entries"]
    subscriptions = next(entry for entry in entries if entry["revenue_model"] == "subscriptions")
    rule = routing["layers"]["monetization"]["rule"]

    assert subscriptions["capability_pack"] == "mozaikspay"
    assert subscriptions["subscription_contract"] == "required"
    assert "managed mozaikspay pack" in subscriptions["operator_pack_note"]
    assert "hosted MozaiksPay API" in subscriptions["operator_pack_note"]
    assert "prioritize the managed mozaikspay capability pack" in rule
    assert "billing provider" in rule


@pytest.mark.parametrize("pack_id", ["messaging", "support", "social"])
def test_pack_contexts_are_registered_for_appgenerator(pack_id: str) -> None:
    context = _read_yaml(f"factory_app/build_context/{pack_id}/context.yaml")

    assert "AppGenerator" in context["applies_to_workflows"]
    assert context["pack"]["id"] == pack_id
    assert context["pack"]["status"] == "active"
    assert {asset["kind"] for asset in context["assets"]} == {"contract", "templates"}


def test_messaging_pack_is_thread_substrate_without_contacts_or_runtime_worker() -> None:
    context = _read_yaml("factory_app/build_context/messaging/context.yaml")
    contract = _read_yaml("factory_app/build_context/messaging/contract.yaml")
    module = _read_yaml("factory_app/build_context/messaging/templates/modules/messages/module.yaml")
    action_ids = {action["id"] for action in module["actions"]}
    cap_ids = {capability["capability_id"] for capability in module["capabilities"]}
    paths = _output_paths(contract["required_outputs"])

    assert {capability["capability_id"] for capability in context["capabilities"]} == cap_ids
    assert {"create_thread", "list_threads", "get_thread", "send_message", "mark_thread_read"} <= action_ids
    assert "messaging.threads.list" in cap_ids
    assert "messaging.messages.send" in cap_ids
    assert any("modules/messages/module.yaml" in path for path in paths)
    assert not (_pack_path("messaging") / "templates" / "modules" / "contacts").exists()
    assert not (_pack_path("messaging") / "templates" / "modules" / "messages" / "runtime_extensions.yaml").exists()
    assert not (_pack_path("messaging") / "templates" / "modules" / "messages" / "backend" / "event_subscriber.py").exists()


def test_messaging_template_module_loads_with_domain_events() -> None:
    loaded = ModuleLoader(str(_pack_path("messaging") / "templates")).load("messages")

    assert loaded.name == "messages"
    assert "domain.messages.message_sent" in loaded.manifests.events.event_types
    assert loaded.manifests.notifications.notifications[0].audience.user_id_field == "recipient_ids"


def test_messaging_page_uses_module_actions_not_websocket_or_legacy_events() -> None:
    source = (
        _pack_path("messaging")
        / "templates"
        / "ui"
        / "pages"
        / "custom"
        / "Messages.jsx"
    ).read_text(encoding="utf-8")

    assert 'moduleAction("messages", "list_threads"' in source
    assert 'moduleAction("messages", "send_message"' in source
    assert "new WebSocket" not in source
    assert "app.messages" not in source
    assert "contacts" not in source.lower()


def test_support_pack_requires_messaging_and_stores_only_ticket_metadata() -> None:
    contract = _read_yaml("factory_app/build_context/support/contract.yaml")
    module = _read_yaml("factory_app/build_context/support/templates/modules/support/module.yaml")
    action_ids = {action["id"] for action in module["actions"]}
    paths = _output_paths(contract["required_outputs"])

    assert contract["required_packs"] == ["messaging"]
    assert {"create_support_request", "list_support_requests", "link_message_thread", "update_support_status"} <= action_ids
    assert "message_thread_id" in module["actions"][0]["input_schema"]["properties"]
    assert any("modules/support/module.yaml" in path for path in paths)
    assert any("modules/support/contracts/notifications.yaml" in path for path in paths)
    assert not any("support_messages" in path for path in paths)


def test_support_template_module_loads_with_current_contracts() -> None:
    loaded = ModuleLoader(str(_pack_path("support") / "templates")).load("support")

    assert loaded.name == "support"
    assert "domain.support.request_created" in loaded.manifests.events.event_types
    assert "domain.support.status_changed" in loaded.manifests.events.event_types
    assert loaded.manifests.reactions.reactions == []
    assert loaded.manifests.notifications is not None
    notification_events = {rule.event_type for rule in loaded.manifests.notifications.notifications}
    assert {"domain.support.request_created", "domain.support.status_changed"} <= notification_events


def test_support_page_creates_real_message_threads() -> None:
    source = (
        _pack_path("support")
        / "templates"
        / "ui"
        / "pages"
        / "custom"
        / "Support.jsx"
    ).read_text(encoding="utf-8")

    assert "moduleAction('support', 'create_support_request'" in source
    assert "moduleAction('messages', 'create_thread'" in source
    assert "moduleAction('support', 'link_message_thread'" in source
    assert "moduleAction('messages', 'send_message'" in source
    assert "DEMO" not in source


def test_social_pack_owns_friends_invites_posts_and_feed() -> None:
    context = _read_yaml("factory_app/build_context/social/context.yaml")
    contract = _read_yaml("factory_app/build_context/social/contract.yaml")
    capabilities = {capability["capability_id"] for capability in context["capabilities"]}
    paths = _output_paths(contract["required_outputs"])

    assert "social.friends.connect" in capabilities
    assert "social.posts.comment" in capabilities
    assert any("modules/friends/module.yaml" in path for path in paths)
    assert any("modules/user_posts/module.yaml" in path for path in paths)
    assert any("modules/activity_feed/module.yaml" in path for path in paths)
    assert any("ui/components/SocialProfileTabs.jsx" in path for path in paths)
    assert "ctx.persistence.collection(module_id, entity_name)" in " ".join(
        boundary["rule"] for boundary in contract["runtime_boundaries"]
    )
