"""Keyed, format-preserving splicing of pack entries into an app's shared files."""

from __future__ import annotations

import json

import pytest
import yaml

from factory_app.workflows.AppGenerator.tools.community_component_composition import (
    CompositionFormatError,
    KeyedRoot,
    content_sha256,
    entry_sha256,
    splice_document,
)

PAGES_JSON = KeyedRoot("ui/route_manifest.json", "app", "json", (("pages", "id"),))
PAGES_YAML = KeyedRoot("admin/admin_registry.yaml", "app", "yaml", (("pages", "id"),))
REGISTRY = KeyedRoot(
    "extended_orchestration/extension_registry.json", "workflows", "json", (("workflows", "id"), ("entrypoints", "id")),
)

APP_JSON = (
    '{\n'
    '    "pages": [\n'
    '        { "id": "home", "path": "/", "component": "HomePage" },\n'
    '        {"id":"about","path":"/about","component":"AboutPage"}\n'
    '    ],\n'
    '    "theme": "dark"\n'
    '}\n'
)
APP_YAML = (
    "schema_version: mozaiks.admin.registry.v1\n"
    "\n"
    "# Operator pages.\n"
    "pages:\n"
    "  # Orders come first.\n"
    "  - id: orders\n"
    "    label: Orders\n"
    "    surfaces: [studio]\n"
    "\n"
    "  # Billing pages.\n"
    "  - id: billing\n"
    "    label: Billing\n"
    "\n"
    "footer: keep me\n"
)


def test_adding_an_entry_leaves_every_existing_byte_in_place() -> None:
    result = splice_document(APP_JSON, PAGES_JSON, upserts={("pages", "stock"): {"id": "stock", "path": "/stock"}}, removals=set())

    assert result.startswith(APP_JSON.split("\n    ],")[0])
    assert result.endswith('    ],\n    "theme": "dark"\n}\n')
    assert [page["id"] for page in json.loads(result)["pages"]] == ["home", "about", "stock"]
    assert '        {\n            "id": "stock",\n            "path": "/stock"\n        }' in result


def test_replacing_and_removing_touch_only_those_entries() -> None:
    replaced = splice_document(APP_JSON, PAGES_JSON, upserts={("pages", "about"): {"id": "about", "path": "/team"}}, removals=set())
    removed = splice_document(APP_JSON, PAGES_JSON, upserts={}, removals={("pages", "home")})

    assert '{ "id": "home", "path": "/", "component": "HomePage" },' in replaced
    assert json.loads(replaced)["pages"][1] == {"id": "about", "path": "/team"}
    assert json.loads(removed)["pages"] == [{"id": "about", "path": "/about", "component": "AboutPage"}]
    assert '{"id":"about","path":"/about","component":"AboutPage"}' in removed


def test_an_unchanged_entry_keeps_its_formatting() -> None:
    unchanged = {"id": "about", "path": "/about", "component": "AboutPage"}

    assert splice_document(APP_JSON, PAGES_JSON, upserts={("pages", "about"): unchanged}, removals=set()) == APP_JSON


def test_removing_every_entry_and_adding_to_an_empty_or_missing_list() -> None:
    emptied = splice_document(APP_JSON, PAGES_JSON, upserts={}, removals={("pages", "home"), ("pages", "about")})
    refilled = splice_document(emptied, PAGES_JSON, upserts={("pages", "x"): {"id": "x"}}, removals=set())
    created = splice_document('{\n  "theme": "dark"\n}\n', PAGES_JSON, upserts={("pages", "x"): {"id": "x"}}, removals=set())

    assert json.loads(emptied)["pages"] == []
    assert json.loads(refilled)["pages"] == [{"id": "x"}]
    assert json.loads(created) == {"theme": "dark", "pages": [{"id": "x"}]}


def test_several_containers_are_spliced_in_one_document() -> None:
    registry = '{\n  "version": 3,\n  "workflows": [\n    {"id": "A"}\n  ]\n}\n'

    result = splice_document(
        registry, REGISTRY,
        upserts={("workflows", "B"): {"id": "B"}, ("entrypoints", "b"): {"id": "b", "workflow": "B", "path": "/b"}},
        removals=set(),
    )

    assert json.loads(result) == {
        "version": 3, "workflows": [{"id": "A"}, {"id": "B"}], "entrypoints": [{"id": "b", "workflow": "B", "path": "/b"}],
    }


def test_yaml_comments_and_neighbours_survive_every_edit() -> None:
    added = splice_document(APP_YAML, PAGES_YAML, upserts={("pages", "stock"): {"id": "stock", "label": "Stock"}}, removals=set())
    replaced = splice_document(APP_YAML, PAGES_YAML, upserts={("pages", "orders"): {"id": "orders", "label": "Sales"}}, removals=set())
    removed = splice_document(APP_YAML, PAGES_YAML, upserts={}, removals={("pages", "orders")})

    for text in (added, replaced, removed):
        assert "# Operator pages.\n" in text and "# Billing pages.\n" in text and text.endswith("footer: keep me\n")
    assert added.startswith(APP_YAML.split("\nfooter:")[0])
    assert [page["id"] for page in yaml.safe_load(added)["pages"]] == ["orders", "billing", "stock"]
    assert yaml.safe_load(replaced)["pages"][0] == {"id": "orders", "label": "Sales"}
    assert "  - id: billing\n    label: Billing\n" in replaced
    assert [page["id"] for page in yaml.safe_load(removed)["pages"]] == ["billing"]


def test_yaml_lists_that_cannot_be_spliced_are_refused() -> None:
    flow = "pages: [{id: a}, {id: b}]\n"

    with pytest.raises(CompositionFormatError, match="block list"):
        splice_document(flow, PAGES_YAML, upserts={("pages", "c"): {"id": "c"}}, removals=set())


def test_yaml_empty_and_missing_lists_accept_entries() -> None:
    empty = splice_document("schema_version: v1\npages: []\n", PAGES_YAML, upserts={("pages", "a"): {"id": "a"}}, removals=set())
    missing = splice_document("schema_version: v1\n", PAGES_YAML, upserts={("pages", "a"): {"id": "a"}}, removals=set())
    emptied = splice_document(APP_YAML, PAGES_YAML, upserts={}, removals={("pages", "orders"), ("pages", "billing")})

    assert yaml.safe_load(empty) == {"schema_version": "v1", "pages": [{"id": "a"}]}
    assert yaml.safe_load(missing) == {"schema_version": "v1", "pages": [{"id": "a"}]}
    assert yaml.safe_load(emptied)["pages"] == [] and emptied.endswith("footer: keep me\n")


def test_duplicate_keys_and_malformed_documents_are_refused() -> None:
    duplicated = '{"pages": [{"id": "a"}, {"id": "a"}]}'

    with pytest.raises(CompositionFormatError, match="more than once"):
        splice_document(duplicated, PAGES_JSON, upserts={("pages", "b"): {"id": "b"}}, removals=set())
    with pytest.raises(CompositionFormatError):
        splice_document('{"pages": [', PAGES_JSON, upserts={("pages", "b"): {"id": "b"}}, removals=set())


def test_hashes_ignore_line_endings_and_formatting() -> None:
    assert content_sha256("a\r\nb\r\n") == content_sha256("a\nb\n")
    assert entry_sha256({"b": 1, "a": [1, 2]}) == entry_sha256({"a": [1, 2], "b": 1})
    assert entry_sha256({"a": 1}) != entry_sha256({"a": 2})
