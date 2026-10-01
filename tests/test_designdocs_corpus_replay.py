"""Replay every recorded DesignDocs output through the real save path.

``fixtures/designdocs_corpus.json`` holds every distinct DesignDocsBundle the
live DesignDocs agent produced up to 2026-09-28 (89 outputs from 85 chats, 95
with resubmissions), read from the local AG2 network WAL with each chat's
context reduced to the keys the save path reads, plus later live outputs
appended when a run exposed a save defect (c65f5d0f, 2026-09-30; 4da11a24,
2026-10-01). Outputs recorded before entity/tenancy/owner_field became required
carry LABELLED defaults: each value the model did not write is listed with the
rule that chose it.

The live model changes a rejected design at most once and then resubmits it
unchanged until the run is blocked, so a rejection is only right when what to
change is a design decision. Every fixture pins its outcome: saved, with the
ownership and field normalizations the save recorded, or a named judgment
rejection whose message says exactly what to change.

An intended change in outcome is accepted by regenerating the expectations and
explaining every changed fixture in the pull request::

    python -m tests.test_designdocs_corpus_replay --update
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import pytest
import yaml

from factory_app.workflows.DesignDocs.tools import save_design_doc
from mozaiksai.core.workflow.agents.factory import ContextVariablesBridge
from mozaiksai.core.workflow.context.frozen import detach
from mozaiksai.core.workflow.context.structured_output_overlay import StructuredOutputOverlay
from mozaiksai.core.workflow.declarative.contracts import ToolOutcomeSpec
from mozaiksai.core.workflow.validation.tool_outcomes import wrap_tool_outcome

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = Path(__file__).parent / "fixtures" / "designdocs_corpus.json"
# A judgment is a design decision the save cannot make; its message names the choices.
JUDGMENTS = {
    "records_on_ui_only_surface": (
        "which holds no durable records. Move",
        "make {surface} a module surface",
        "or drop the collection and declare a custom read on the module that owns its data",
    ),
}
_MARKDOWN = {
    "frontend_markdown": "# Frontend Design\nRecorded output.",
    "backend_markdown": "# Backend Design\nRecorded output.",
    "database_markdown": "# Database Design\nRecorded output.",
}
_SAVE_SEED = {"design_docs_save_outcome": "blocked", "design_docs_save_attempts": 0, "design_docs_save_feedback": ""}


def _corpus() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _wrapped_save():
    tools = yaml.safe_load((ROOT / "factory_app/workflows/DesignDocs/tools.yaml").read_text(encoding="utf-8"))["tools"]
    entry = next(tool for tool in tools if tool["function"] == "save_design_docs_bundle")
    return wrap_tool_outcome(save_design_doc.save_design_docs_bundle, ToolOutcomeSpec.model_validate(entry["outcome"]))


def replay(entry: dict[str, Any]) -> dict[str, Any]:
    """Save one recorded output as a first attempt; only storage and logging are replaced."""
    context = ContextVariablesBridge({**deepcopy(entry["context"]), **_SAVE_SEED})
    bundle = {
        "agent_message": "Recorded DesignDocs output.", **_MARKDOWN,
        "surface_map": deepcopy(entry["surface_map"]), "data_contract": deepcopy(entry["data_contract"]),
        "experience_spec": deepcopy(entry["experience_spec"]),
    }
    submitted = deepcopy(bundle)
    store = Mock()
    store.mark_design_doc_status = AsyncMock()
    store.upsert_design_doc = AsyncMock()
    store.save_data_contract = AsyncMock()
    summary = AsyncMock()
    log = Mock()
    with (
        patch.object(save_design_doc, "AG2PersistenceManager", Mock()),
        patch.object(save_design_doc, "BuilderArtifactStore", Mock(return_value=store)),
        patch.object(save_design_doc, "persist_summary_artifact", summary),
        patch.object(save_design_doc, "logger", log),
    ):
        result = detach(asyncio.run(_wrapped_save()(context_variables=StructuredOutputOverlay(context, bundle))))
    assert bundle == submitted, "the model's structured output is never edited in place"
    if result.get("outcome") != "saved":
        return {"outcome": result.get("outcome"), "error": result.get("error") or result.get("outcome_error")}
    saved = {key: detach(context.get(key)) for key in ("design_surface_map", "data_contract", "experience_spec")}
    return {
        "outcome": "saved",
        "ownership_normalizations": summary.await_args.kwargs["summary_payload"]["ownership_normalizations"],
        "field_normalizations": [
            call.args[0].split("DATA_CONTRACT_FIELD_NORMALIZED: ", 1)[1]
            for call in log.warning.call_args_list
            if call.args and "DATA_CONTRACT_FIELD_NORMALIZED: " in str(call.args[0])
        ],
        # The saved design itself: a change the records do not show still fails.
        "saved_design_sha256": hashlib.sha256(json.dumps(saved, sort_keys=True).encode("utf-8")).hexdigest(),
    }


CORPUS = _corpus()


def test_the_fixture_is_the_recorded_corpus():
    fixtures = CORPUS["fixtures"]
    assert (CORPUS["outputs_unique"], CORPUS["outputs_total"], CORPUS["chats"]) == (91, 97, 87)
    assert len(fixtures) == 91 and len({entry["id"] for entry in fixtures}) == 91
    assert sum(entry["occurrences"] for entry in fixtures) == 97
    assert len({entry["chat_id"] for entry in fixtures}) == 87
    for entry in fixtures:
        for label in entry["labelled_defaults"]:
            assert set(label) == {"path", "field", "value", "rule"}, label


def test_the_corpus_saves_all_but_its_judgment_cases():
    outcomes = [entry["expected"] for entry in CORPUS["fixtures"]]
    saved = [expected for expected in outcomes if expected["outcome"] == "saved"]
    judged = [expected for expected in outcomes if expected["outcome"] != "saved"]
    assert len(saved) >= 89
    assert all(expected["outcome"] == "revise" and expected["judgment"] in JUDGMENTS for expected in judged)


@pytest.mark.parametrize("entry", CORPUS["fixtures"], ids=[entry["id"] for entry in CORPUS["fixtures"]])
def test_recorded_output_saves_or_names_its_judgment(entry):
    expected = entry["expected"]
    actual = replay(entry)

    if expected["outcome"] == "saved":
        changed = {key: (expected.get(key), actual.get(key)) for key in {*expected, *actual} if expected.get(key) != actual.get(key)}
        assert not changed, f"{entry['id']} changed (expected, actual): {json.dumps(changed, indent=1)[:3000]}"
        return
    assert actual["outcome"] == "revise", actual
    assert actual["error"] == expected["error"]
    surface = actual["error"].split("surface ", 1)[1].split(",", 1)[0]
    for phrase in JUDGMENTS[expected["judgment"]]:
        assert phrase.format(surface=surface) in actual["error"], phrase


def _update() -> None:
    corpus = _corpus()
    changed = []
    for entry in corpus["fixtures"]:
        actual = replay(entry)
        if actual["outcome"] == "saved":
            expected: dict[str, Any] = actual
        else:
            judgment = next(
                (name for name, phrases in JUDGMENTS.items() if phrases[0] in str(actual["error"])), None,
            )
            if judgment is None:
                raise SystemExit(f"{entry['id']}: unnamed rejection, add a judgment or fix the save: {actual['error']}")
            expected = {**actual, "judgment": judgment}
        if expected != entry["expected"]:
            changed.append(entry["id"])
            entry["expected"] = expected
    header = {key: value for key, value in corpus.items() if key != "fixtures"}
    lines = ",\n".join("    " + json.dumps(entry, separators=(",", ":"), ensure_ascii=True) for entry in corpus["fixtures"])
    FIXTURE.write_text(
        json.dumps(header, indent=2, ensure_ascii=True)[:-2] + ',\n  "fixtures": [\n' + lines + "\n  ]\n}\n",
        encoding="utf-8",
    )
    print(f"updated {len(changed)} expectations: {changed}")


if __name__ == "__main__":
    if sys.argv[1:] != ["--update"]:
        raise SystemExit("usage: python -m tests.test_designdocs_corpus_replay --update")
    _update()
