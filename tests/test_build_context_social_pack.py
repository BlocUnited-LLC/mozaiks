"""Contract tests for the social build context pack.

Verifies that:
- context.yaml registers the pack correctly for AppGenerator with expected capabilities
- contract.yaml required_outputs all exist under templates; forbidden paths are absent
- Each module (friends, activity_feed, user_posts) declares its expected actions and
  capabilities, with capabilities pointing at declared actions
- Data migration declares all six stable collection aliases
- Backend Python files compile and use the canonical persistence pattern
- Event, reaction, notification, and profile contracts are correctly declared
- AppGenerator capability_directory and capability_routing wire the pack correctly
- Forbidden output paths (modules/social/, standalone page files) are absent
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

WORKSPACE = Path(__file__).resolve().parents[1]
BUILD_CONTEXT = WORKSPACE / "factory_app" / "build_context"
SOCIAL = BUILD_CONTEXT / "social"
TEMPLATES = SOCIAL / "templates"


def _read_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _action_ids(module_yaml: dict[str, Any]) -> set[str]:
    return {action["id"] for action in module_yaml.get("actions") or []}


def _capability_ids(module_yaml: dict[str, Any]) -> set[str]:
    return {cap["capability_id"] for cap in module_yaml.get("capabilities") or []}


def _capability_targets(module_yaml: dict[str, Any]) -> set[str]:
    return {cap["target"] for cap in module_yaml.get("capabilities") or []}


# ---------------------------------------------------------------------------
# Pack registration
# ---------------------------------------------------------------------------

def test_social_context_registers_active_appgenerator_pack() -> None:
    context = _read_yaml(SOCIAL / "context.yaml")

    assert context["context_id"] == "social"
    assert "AppGenerator" in context["applies_to_workflows"]
    assert context["pack"]["id"] == "social"
    assert context["pack"]["status"] == "active"
    assert context["pack"]["capability_source"] == "generated_module"
    assert {asset["kind"] for asset in context["assets"]} == {"contract", "templates"}

    capability_ids = {cap["capability_id"] for cap in context["capabilities"]}
    assert capability_ids == {
        "social.friends.list",
        "social.friends.connect",
        "social.feed.read",
        "social.posts.create",
        "social.posts.list",
        "social.posts.react",
        "social.posts.comment",
    }


# ---------------------------------------------------------------------------
# Required outputs / forbidden outputs
# ---------------------------------------------------------------------------

def test_social_contract_required_outputs_exist_under_templates() -> None:
    contract = _read_yaml(SOCIAL / "contract.yaml")

    assert contract["contract_id"] == "social"
    assert contract["contract_type"] == "build_pack_instructions"
    assert contract["facades"] == []

    # All template-owned required outputs must exist
    missing = [
        output["path"]
        for output in contract["required_outputs"]
        if output.get("owner") == "templates" and not (TEMPLATES / output["path"]).exists()
    ]
    assert missing == [], f"Missing required template outputs: {missing}"


def test_social_pack_forbidden_outputs_are_absent() -> None:
    contract = _read_yaml(SOCIAL / "contract.yaml")

    generated_paths = {
        str(path.relative_to(TEMPLATES)).replace("\\", "/")
        for path in TEMPLATES.rglob("*")
        if path.is_file()
    }

    for forbidden in contract.get("forbidden_outputs", []):
        if "path_prefix" in forbidden:
            assert not any(p.startswith(forbidden["path_prefix"]) for p in generated_paths), (
                f"Forbidden prefix found: {forbidden['path_prefix']}"
            )
        elif "path" in forbidden:
            assert forbidden["path"] not in generated_paths, (
                f"Forbidden path found: {forbidden['path']}"
            )


# ---------------------------------------------------------------------------
# Module: friends
# ---------------------------------------------------------------------------

def test_friends_module_declares_expected_actions() -> None:
    module_yaml = _read_yaml(TEMPLATES / "modules" / "friends" / "module.yaml")

    assert _action_ids(module_yaml) == {
        "send_friend_request",
        "accept_friend_request",
        "decline_friend_request",
        "withdraw_friend_request",
        "remove_friend",
        "list_friends",
        "list_friends_of",
        "list_friend_requests",
        "get_friendship_status",
        "get_friends_count",
    }


def test_friends_module_capabilities_target_existing_actions() -> None:
    module_yaml = _read_yaml(TEMPLATES / "modules" / "friends" / "module.yaml")
    actions = _action_ids(module_yaml)
    targets = _capability_targets(module_yaml)

    assert targets <= actions
    assert _capability_ids(module_yaml) == {"social.friends.list", "social.friends.connect"}


def test_friends_module_declares_user_data_scope() -> None:
    module_yaml = _read_yaml(TEMPLATES / "modules" / "friends" / "module.yaml")
    assert module_yaml.get("module", {}).get("user_data_scope") is True


def test_friends_backend_ships_account_data_handler() -> None:
    handler = TEMPLATES / "modules" / "friends" / "backend" / "account_data_handler.py"
    assert handler.exists()
    source = handler.read_text(encoding="utf-8")
    assert "delete_user_data" in source
    assert "export_user_data" in source


# ---------------------------------------------------------------------------
# Module: activity_feed
# ---------------------------------------------------------------------------

def test_activity_feed_module_declares_expected_actions() -> None:
    module_yaml = _read_yaml(TEMPLATES / "modules" / "activity_feed" / "module.yaml")

    assert _action_ids(module_yaml) == {
        "get_feed",
        "get_profile_activity",
        "record_activity",
    }


def test_activity_feed_module_capabilities_target_existing_actions() -> None:
    module_yaml = _read_yaml(TEMPLATES / "modules" / "activity_feed" / "module.yaml")
    actions = _action_ids(module_yaml)
    targets = _capability_targets(module_yaml)

    assert targets <= actions
    assert _capability_ids(module_yaml) == {"social.feed.read"}


def test_activity_feed_populated_via_reactions_not_direct_calls() -> None:
    """activity_feed populates via domain event reactions, not direct service calls."""
    reactions = _read_yaml(
        TEMPLATES / "modules" / "activity_feed" / "contracts" / "reactions.yaml"
    )
    event_types = {r["event_type"] for r in reactions.get("reactions") or []}

    # Activity feed must react to key social domain events
    assert "domain.social.friendship.created" in event_types
    assert "domain.social.post.published" in event_types

    # The friends service must not call record_activity directly
    friends_service = _read_text(
        TEMPLATES / "modules" / "friends" / "backend" / "service.py"
    )
    assert "record_activity" not in friends_service


# ---------------------------------------------------------------------------
# Module: user_posts
# ---------------------------------------------------------------------------

def test_user_posts_module_declares_expected_actions() -> None:
    module_yaml = _read_yaml(TEMPLATES / "modules" / "user_posts" / "module.yaml")

    assert _action_ids(module_yaml) == {
        "create_post",
        "get_post",
        "delete_post",
        "list_posts",
        "list_user_posts",
        "react_to_post",
        "add_comment",
        "delete_comment",
        "list_comments",
        "get_reaction_summary",
    }


def test_user_posts_module_capabilities_target_existing_actions() -> None:
    module_yaml = _read_yaml(TEMPLATES / "modules" / "user_posts" / "module.yaml")
    actions = _action_ids(module_yaml)
    targets = _capability_targets(module_yaml)

    assert targets <= actions
    assert _capability_ids(module_yaml) == {
        "social.posts.create",
        "social.posts.list",
        "social.posts.react",
        "social.posts.comment",
    }


def test_user_posts_declares_user_data_scope() -> None:
    module_yaml = _read_yaml(TEMPLATES / "modules" / "user_posts" / "module.yaml")
    assert module_yaml.get("module", {}).get("user_data_scope") is True


def test_user_posts_delete_is_soft_not_hard() -> None:
    """Posts must be soft-deleted (status=deleted) to preserve comment threads."""
    service = _read_text(
        TEMPLATES / "modules" / "user_posts" / "backend" / "service.py"
    )
    # Soft delete pattern: sets status to deleted rather than removing the document
    assert "deleted" in service
    # Hard-remove in the service would mean a delete_one / delete_many call — that
    # should not exist outside the account_data_handler
    service_lines = [line for line in service.splitlines() if "delete_one" in line or "delete_many" in line]
    assert service_lines == [], f"Unexpected hard-delete in service.py: {service_lines}"


# ---------------------------------------------------------------------------
# Data migration
# ---------------------------------------------------------------------------

def test_social_data_migration_declares_stable_collection_aliases() -> None:
    import json

    migration_path = TEMPLATES / "data" / "migrations" / "001_social_collections.json"
    assert migration_path.exists()
    migration = json.loads(migration_path.read_text(encoding="utf-8"))

    aliases = {
        collection["data_alias"]
        for surface in migration.get("surfaces", [])
        for collection in surface.get("collections", [])
    }
    assert aliases == {
        "friends.friendships",
        "friends.requests",
        "activity_feed.events",
        "user_posts.posts",
        "user_posts.reactions",
        "user_posts.comments",
    }


# ---------------------------------------------------------------------------
# Backend template contracts
# ---------------------------------------------------------------------------

def test_social_backend_templates_compile() -> None:
    for mod in ("friends", "activity_feed", "user_posts"):
        backend_root = TEMPLATES / "modules" / mod / "backend"
        for path in backend_root.glob("*.py"):
            compile(path.read_text(encoding="utf-8"), str(path), "exec")


def test_social_backend_repos_use_canonical_persistence_pattern() -> None:
    """All repo.py files must use persistence.collection(), not Motor or ctx.db."""
    for mod in ("friends", "activity_feed", "user_posts"):
        repo_text = _read_text(TEMPLATES / "modules" / mod / "backend" / "repo.py")

        # Must use persistence accessor
        assert "persistence.collection" in repo_text or "_collection(ctx" in repo_text, (
            f"{mod}/repo.py does not use canonical persistence pattern"
        )
        assert "get_mongo_client" not in repo_text
        assert "ctx.db" not in repo_text


def test_social_backend_services_emit_domain_events() -> None:
    """friends and user_posts service.py must emit domain events via ctx.emit."""
    for mod in ("friends", "user_posts"):
        service_text = _read_text(TEMPLATES / "modules" / mod / "backend" / "service.py")
        assert "ctx.emit" in service_text, f"{mod}/service.py does not call ctx.emit"


# ---------------------------------------------------------------------------
# Event / reaction / notification / profile contracts
# ---------------------------------------------------------------------------

def test_friends_declares_canonical_social_events() -> None:
    events = _read_yaml(
        TEMPLATES / "modules" / "friends" / "contracts" / "events.yaml"
    )
    event_types = {e["type"] for e in events.get("events") or []}

    assert "domain.social.friendship.created" in event_types
    assert "domain.social.friend_request.sent" in event_types
    assert "domain.social.friendship.removed" in event_types


def test_user_posts_declares_canonical_social_events() -> None:
    events = _read_yaml(
        TEMPLATES / "modules" / "user_posts" / "contracts" / "events.yaml"
    )
    event_types = {e["type"] for e in events.get("events") or []}

    assert "domain.social.post.published" in event_types
    assert "domain.social.post.reacted" in event_types
    assert "domain.social.post.commented" in event_types


def test_social_profile_tabs_declared_for_friends_and_posts() -> None:
    for mod, expected_tab in (("friends", "friends"), ("user_posts", "posts")):
        profile = _read_yaml(
            TEMPLATES / "modules" / mod / "contracts" / "profile.yaml"
        )
        assert profile.get("schema_version") == "mozaiks.profile.v1"
        tab_ids = {tab["id"] for tab in profile.get("tabs") or []}
        assert expected_tab in tab_ids, f"{mod}/profile.yaml missing tab '{expected_tab}'"


# ---------------------------------------------------------------------------
# AppGenerator selection wiring
# ---------------------------------------------------------------------------

def test_appgenerator_selection_wiring_includes_social_pack() -> None:
    directory = _read_yaml(BUILD_CONTEXT / "AppGenerator" / "capability_directory.yaml")
    by_id = {entry["id"]: entry for entry in directory["capabilities"]}

    assert "social_pack" in by_id
    social = by_id["social_pack"]
    assert social["capability_kind"] == "operator_pack"
    assert "social.friends.list" in social["capabilities_provided"]
    assert any("social" in signal.lower() or "friend" in signal.lower() for signal in social["intent_signals"])

    routing = _read_yaml(BUILD_CONTEXT / "AppGenerator" / "capability_routing.yaml")
    packs = {
        pack["id"]: pack
        for pack in routing["layers"]["capability_pack"]["packs"]
    }
    assert "social" in packs
    assert packs["social"]["capability_kind"] == "operator_pack"


def test_social_pack_does_not_generate_monolithic_social_module() -> None:
    """The social pack must not generate a single monolithic modules/social/ module."""
    generated_paths = {
        str(path.relative_to(TEMPLATES)).replace("\\", "/")
        for path in TEMPLATES.rglob("*")
        if path.is_file()
    }
    assert not any(p.startswith("modules/social/") for p in generated_paths)
    assert not any(p.startswith("modules/network/") for p in generated_paths)
    assert not any(p.startswith("services/friends/") for p in generated_paths)
