from __future__ import annotations

import pytest

from mozaiksai.core.runtime.persistence import (
    collection_name_for,
    safe_identifier,
    scope_filter_for,
    scope_metadata,
    short_stable_hash,
)


def test_collection_name_for_is_deterministic() -> None:
    first = collection_name_for(app_id="app_1", app_slug="Task Tracker", module_id="projects", entity_name="projects")
    second = collection_name_for(app_id="app_1", app_slug="Task Tracker", module_id="projects", entity_name="projects")

    assert first == second


def test_same_inputs_produce_same_name() -> None:
    assert collection_name_for(app_id="app_1", module_id="tasks", entity_name="items") == collection_name_for(
        app_id="app_1",
        module_id="tasks",
        entity_name="items",
    )


def test_different_app_id_produces_different_name() -> None:
    left = collection_name_for(app_id="app_1", module_id="tasks", entity_name="items")
    right = collection_name_for(app_id="app_2", module_id="tasks", entity_name="items")

    assert left != right


def test_different_module_id_produces_different_name() -> None:
    left = collection_name_for(app_id="app_1", module_id="tasks", entity_name="items")
    right = collection_name_for(app_id="app_1", module_id="projects", entity_name="items")

    assert left != right


def test_different_entity_name_produces_different_name() -> None:
    left = collection_name_for(app_id="app_1", module_id="tasks", entity_name="items")
    right = collection_name_for(app_id="app_1", module_id="tasks", entity_name="comments")

    assert left != right


def test_spaces_and_special_chars_are_sanitized() -> None:
    name = collection_name_for(
        app_id="app_1",
        app_slug="Task Tracker!",
        module_id="Project Board",
        entity_name="Client Notes/$drafts",
    )

    assert name.startswith("app_task_tracker_")
    assert "__project_board__client_notes_drafts" in name
    assert all(char.islower() or char.isdigit() or char == "_" for char in name)


def test_unicode_is_handled_safely() -> None:
    name = collection_name_for(
        app_id="app_équipe",
        app_slug="Café équipe",
        module_id="déjà vu",
        entity_name="résumés",
    )

    assert name.startswith("app_cafe_equipe_")
    assert "__deja_vu__resumes" in name


def test_collection_name_contains_short_stable_hash() -> None:
    app_hash = short_stable_hash("app_1")

    assert app_hash in collection_name_for(app_id="app_1", module_id="tasks", entity_name="items")


def test_collection_name_length_is_bounded() -> None:
    name = collection_name_for(
        app_id="app_1",
        app_slug="a" * 200,
        module_id="m" * 200,
        entity_name="e" * 200,
    )

    assert len(name) <= 120


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"app_id": "", "module_id": "tasks", "entity_name": "items"}, "app_id is required"),
        ({"app_id": "app_1", "module_id": "", "entity_name": "items"}, "module_id is required"),
        ({"app_id": "app_1", "module_id": "tasks", "entity_name": ""}, "entity_name is required"),
    ],
)
def test_collection_name_for_rejects_missing_required_values(kwargs: dict[str, str], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        collection_name_for(**kwargs)


def test_safe_identifier_handles_special_chars_safely() -> None:
    assert safe_identifier(" Tasks & Notes / 2026 ") == "tasks_notes_2026"
    assert safe_identifier("😀") == "item"


def test_scope_filter_includes_app_id() -> None:
    assert scope_filter_for("app_1") == {"app_id": "app_1"}


def test_scope_filter_merges_extra_filters() -> None:
    assert scope_filter_for("app_1", {"status": "open"}) == {"app_id": "app_1", "status": "open"}


def test_scope_filter_refuses_extra_app_id_override() -> None:
    with pytest.raises(ValueError, match="cannot override app_id"):
        scope_filter_for("app_1", {"app_id": "app_2"})


def test_scope_metadata_includes_app_id_and_optional_scope_values() -> None:
    assert scope_metadata(
        "app_1",
        tenant_id="tenant_1",
        workspace_id="workspace_1",
        user_id="user_1",
    ) == {
        "app_id": "app_1",
        "tenant_id": "tenant_1",
        "workspace_id": "workspace_1",
        "user_id": "user_1",
    }


def test_scope_metadata_omits_missing_optional_scope_values() -> None:
    assert scope_metadata("app_1") == {"app_id": "app_1"}

