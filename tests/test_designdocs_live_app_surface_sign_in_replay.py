"""Replay the live 4da11a24 DesignDocs output: an app page at the platform login route is removed.

DesignDocs chat 4da11a24 (OSS c8b9ea2e, 2026-10-01) saved, on its first attempt,
an approved ``Authentication`` page at ``/login`` whose only section is an
email/password Form, owned by the ordinary app module ``tasks`` beside its
Dashboard and Task Management pages. Nothing in the design signs anyone in: no
surface declares a sign-in action and no section binds one. The platform serves
sign-in at the auth contract's login route, so AppGenerator plan review handed
the page task ``ui/pages/login.yaml``: a credential form with no action to bind,
at the platform's own route.

The output is the replay corpus fixture ``4da11a24-b55aca6e``
(``tests/fixtures/designdocs_corpus.json``), read from the local AG2 network WAL
with the chat's context reduced to the keys the save path reads.
"""

from __future__ import annotations

import json
import logging
from copy import deepcopy

import pytest
import yaml

from factory_app.workflows._shared import surface_ownership
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge
from mozaiksai.core.workflow.context.frozen import detach
from tests import test_designdocs_corpus_replay as corpus
from tests import test_designdocs_monetization_inventory as inventory

FIXTURE_ID = "4da11a24-b55aca6e"
APP_PAGES = [("Dashboard", "/dashboard"), ("Task Management", "/tasks")]
# Completed at save from the selected MozaiksPay pack (monetization_enabled).
BILLING_PAGES = [("Pricing", "/pricing"), ("Billing", "/billing"), ("Usage", "/usage")]
RECORD = {
    "surface_id": "tasks", "owner": "platform", "removed_collections": [],
    "removed_pages": [{"name": "Authentication", "route": "/login"}],
}
MARKER = "DESIGN_OWNERSHIP_NORMALIZED surface=tasks owner=platform removed=[] removed_pages=[Authentication@/login]"
persistence = inventory.persistence


def _entry() -> dict:
    return next(entry for entry in corpus.CORPUS["fixtures"] if entry["id"] == FIXTURE_ID)


def _save() -> tuple[ContextVariablesBridge, dict]:
    entry = _entry()
    context = ContextVariablesBridge({**deepcopy(entry["context"]), **corpus._SAVE_SEED})
    bundle = {
        "agent_message": "Recorded DesignDocs output.", **corpus._MARKDOWN,
        "surface_map": deepcopy(entry["surface_map"]), "data_contract": deepcopy(entry["data_contract"]),
        "experience_spec": deepcopy(entry["experience_spec"]),
    }
    submitted = deepcopy(bundle)
    result = inventory._save(context, bundle)
    assert bundle == submitted, "the model's structured output is never edited in place"
    return context, result


def _links(experience: dict) -> list[str]:
    """Every typed navigation reference in the saved pages' section config hints."""
    found: list[str] = []

    def walk(node) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in surface_ownership._NAVIGATION_KEYS and isinstance(value, str):
                    found.append(value)
                else:
                    walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    for page in experience["pages"]:
        for section in page.get("sections") or []:
            if isinstance(section.get("config_hint"), str):
                walk(json.loads(section["config_hint"]))
    return found


def test_fixture_records_the_live_evidence():
    entry = _entry()
    assert entry["chat_id"] == "4da11a24-2e1a-43d2-8be0-abc143657d52"
    assert entry["labelled_defaults"] == [] and entry["occurrences"] == 1
    (tasks,) = entry["surface_map"]["surfaces"]
    assert (tasks["surface_id"], tasks["owner"], tasks["surface_kind"]) == ("tasks", "app", "module")
    assert tasks["primary_entities"] == ["Task"]
    assert tasks["owned_pages"] == ["Dashboard", "Task Management", "Authentication"]
    # No sign-in action anywhere: the form had nothing to bind.
    assert [*tasks["owned_mutations"], *tasks["custom_reads"]] == [
        "create_task", "update_task", "delete_task", "summarize_tasks",
    ]
    pages = {page["name"]: page for page in entry["experience_spec"]["pages"]}
    auth = pages["Authentication"]
    assert auth["route"] == "/login"
    (form,) = auth["sections"]
    assert form["primitive"] == "Form"
    assert json.loads(form["config_hint"]) == {"fields": [
        {"label": "Email", "type": "text", "name": "email"},
        {"label": "Password", "type": "password", "name": "password"},
    ]}
    hints = [section.get("config_hint") or "" for page in pages.values() for section in page["sections"]]
    assert not any("data_source" in hint or "/api/modules/" in hint for hint in hints)


def test_live_output_saves_without_the_platform_sign_in_page(persistence, caplog):
    store, _, summary = persistence

    with caplog.at_level(logging.INFO):
        context, result = _save()

    assert result["outcome"] == "saved", result
    experience = detach(context.get("experience_spec"))
    assert [(page["name"], page["route"]) for page in experience["pages"]] == [*APP_PAGES, *BILLING_PAGES]
    surfaces = {surface["surface_id"]: surface for surface in detach(context.get("design_surface_map"))["surfaces"]}
    tasks = surfaces["tasks"]
    # The surface stays the app's with everything else it owns.
    assert (tasks["owner"], tasks["surface_kind"], tasks["primary_entities"]) == ("app", "module", ["Task"])
    assert tasks["owned_pages"] == ["Dashboard", "Task Management"]
    assert tasks["owned_mutations"] == ["create_task", "update_task", "delete_task"]
    assert tasks["custom_reads"] == ["summarize_tasks"]
    assert tasks["events_emitted"] == ["task_created", "task_updated", "task_deleted"]
    assert summary.await_args.kwargs["summary_payload"]["ownership_normalizations"] == [RECORD]
    assert MARKER in caplog.text
    saved = {call.kwargs["kind"]: call.kwargs for call in store.upsert_design_doc.await_args_list}
    assert MARKER in saved["backend"]["content"]
    assert saved["ui_schema"]["extra_fields"]["experience_spec"] == experience
    ui_schema = yaml.safe_load(saved["ui_schema"]["content"])
    assert [(page["name"], page["route"]) for page in ui_schema["pages"]] == [*APP_PAGES, *BILLING_PAGES]
    assert ui_schema["ownership_normalizations"] == [RECORD]
    # No page, and no typed navigation, is left at the platform's sign-in route.
    login = surface_ownership.auth_contract_routes().login
    assert login == "/login"
    assert login not in {page["route"] for page in experience["pages"]}
    assert login not in _links(experience)


def test_without_content_recognition_the_live_output_saves_the_page_as_it_did_live(persistence, monkeypatch):
    """The negative control: the removal is this rule's, and nothing else in the save made it."""
    monkeypatch.setattr(surface_ownership, "_only_platform_authentication", lambda *args, **kwargs: False)
    _, _, summary = persistence

    context, result = _save()

    assert result["outcome"] == "saved", result
    experience = detach(context.get("experience_spec"))
    assert ("Authentication", "/login") in {(page["name"], page["route"]) for page in experience["pages"]}
    tasks = next(s for s in detach(context.get("design_surface_map"))["surfaces"] if s["surface_id"] == "tasks")
    assert tasks["owned_pages"] == ["Dashboard", "Task Management", "Authentication"]
    assert summary.await_args.kwargs["summary_payload"]["ownership_normalizations"] == []


@pytest.mark.parametrize("name", ["Dashboard", "Task Management"])
def test_the_app_pages_are_saved_as_the_model_wrote_them(persistence, name):
    context, result = _save()

    assert result["outcome"] == "saved", result
    saved = {page["name"]: page for page in detach(context.get("experience_spec"))["pages"]}
    written = {page["name"]: page for page in _entry()["experience_spec"]["pages"]}
    assert saved[name] == written[name]
