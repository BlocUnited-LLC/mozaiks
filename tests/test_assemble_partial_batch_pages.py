"""A failed task loses its page, not the whole build.

`_apply_planned_page_contracts` raised `missing planned page during assembly`
whenever a planned page had no emitted file. That invariant dates from #524,
when a batch ran `failure_policy: fail_batch` and a partial batch could never
reach assembly at all.

#648 changed that. The batch now runs `continue_with_available`, and
`AppGenerator/transition_graph.yaml` routes a partial batch into AssemblyAgent
deliberately -- "so acceptance can judge what was produced and the bundle
repair loop can act on it". The two halves moved apart: assembly kept the old
invariant and killed the exact path the router was built to support.

Live case (run 7806badf): a ServiceAgent task emitted a file outside its
owned_paths and failed; its dependent `page_bundle` (AppSchemaAgent) never ran;
the batch went partial; assembly then blamed `ui/pages/dashboard.yaml` -- a
message pointing away from the recorded failure sitting in `_failed`.
"""
from __future__ import annotations

import pytest
import yaml

from factory_app.workflows.AppGenerator.tools.assemble_app_tasks import (
    _apply_planned_page_contracts,
    _failed_batch_task_ids,
)
from tests.page_plan_fixtures import _page_from_plan

PLAN_PAGE = {"name": "Dashboard", "route": "/dashboard", "path": "ui/pages/dashboard.yaml"}
# A real AppPageSchema, so a page that IS found gets validated rather than
# tripping over a stand-in that could never have passed anyway.
PAGE_YAML = yaml.safe_dump(_page_from_plan(PLAN_PAGE, "dashboard"), sort_keys=False)


def _plan(owned: list[str] | None = None) -> dict:
    return {
        "pages": [dict(PLAN_PAGE)],
        "build_tasks": [
            {
                "task_id": "task_management_page_bundle",
                "task_type": "page_bundle",
                "owned_paths": owned if owned is not None else ["ui/pages/dashboard.yaml"],
            }
        ],
    }


def _files(*paths: str) -> list[dict]:
    return [{"filename": p, "content": PAGE_YAML} for p in paths]


def test_a_page_whose_task_failed_is_skipped_not_fatal() -> None:
    """The live failure: partial batch reaches assembly and must survive it."""
    result = _apply_planned_page_contracts(
        _files("app.json"),
        _plan(),
        failed_task_ids={"task_management_page_bundle"},
    )
    assert [f["filename"] for f in result] == ["app.json"], (
        "the partial bundle must pass through so acceptance and repair can judge it"
    )


def test_a_missing_page_from_a_task_that_did_not_fail_still_raises() -> None:
    """Skipping unconditionally would hide a real defect."""
    with pytest.raises(ValueError) as err:
        _apply_planned_page_contracts(_files("app.json"), _plan(), failed_task_ids=set())
    message = str(err.value)
    assert "ui/pages/dashboard.yaml" in message
    # The old message named only the path, so a reader could not tell which task
    # was accountable or whether a failure had already been recorded upstream.
    assert "task_management_page_bundle" in message, "name the owning task"
    assert "reported no failure" in message, "say why this is unexpected"


def test_no_failed_ids_supplied_behaves_as_before() -> None:
    with pytest.raises(ValueError, match="missing planned page during assembly"):
        _apply_planned_page_contracts(_files("app.json"), _plan())


def test_an_unrelated_failed_task_does_not_excuse_the_page() -> None:
    with pytest.raises(ValueError, match="missing planned page"):
        _apply_planned_page_contracts(
            _files("app.json"), _plan(), failed_task_ids={"some_other_task"}
        )


def test_a_non_canonical_owned_path_matches_the_emitted_file() -> None:
    """file_map keys are safe_relpath-canonical; the plan side now is too.

    Nothing forces a plan to spell owned_paths canonically, so "./ui/pages/x.yaml"
    used to miss a file the worker emitted as "ui/pages/x.yaml" and raise on a
    page that was present.
    """
    result = _apply_planned_page_contracts(
        _files("ui/pages/dashboard.yaml"),
        _plan(owned=["./ui/pages/dashboard.yaml"]),
        failed_task_ids=set(),
    )
    assert [f["filename"] for f in result] == ["ui/pages/dashboard.yaml"]


def test_a_backslash_owned_path_still_matches() -> None:
    result = _apply_planned_page_contracts(
        _files("ui/pages/dashboard.yaml"),
        _plan(owned=[r"ui\pages\dashboard.yaml"]),
        failed_task_ids=set(),
    )
    assert [f["filename"] for f in result] == ["ui/pages/dashboard.yaml"]


def test_an_unusable_owned_path_is_ignored_rather_than_crashing() -> None:
    for bad in ["", "   ", "/etc/passwd", "../outside.yaml"]:
        result = _apply_planned_page_contracts(_files("app.json"), _plan(owned=[bad]))
        assert [f["filename"] for f in result] == ["app.json"], bad


def test_failed_ids_are_read_from_both_records_the_batch_writes() -> None:
    """_build_batch_outputs writes _failed and _meta.failed_tasks together."""
    assert _failed_batch_task_ids({"_failed": {"t1": {"status": "failed"}}}) == {"t1"}
    assert _failed_batch_task_ids({"_meta": {"failed_tasks": ["t2"]}}) == {"t2"}
    assert _failed_batch_task_ids(
        {"_failed": {"t1": {}}, "_meta": {"failed_tasks": ["t1", "t2"]}}
    ) == {"t1", "t2"}


def test_absent_or_malformed_batch_results_yield_no_failed_ids() -> None:
    for value in [None, {}, [], "partial", {"_meta": {}}, {"_failed": None}]:
        assert _failed_batch_task_ids(value) == set(), value


def test_the_call_site_supplies_the_batch_state() -> None:
    """The guard is inert unless assembly actually passes the failures in."""
    import inspect

    from factory_app.workflows.AppGenerator.tools import assemble_app_tasks

    source = inspect.getsource(assemble_app_tasks.assemble_app_tasks)
    assert "_failed_batch_task_ids" in source, "assembly must read the batch failures"
    assert "app_task_batch_results" in source
