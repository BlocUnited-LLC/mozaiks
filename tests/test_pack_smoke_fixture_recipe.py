"""AppGenerator may bind reviewed pack smoke inputs only to the owning app task."""

import asyncio
import shutil
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from factory_app.workflows._shared.pack_selection import available_pack_rows
from factory_app.workflows.AppGenerator.tools.resolve_managed_capability_templates import (
    PackIntegrityError,
    verified_pack_template_inventory,
    verify_pack_integrity,
)
from mozaiksai.core.workflow.generator_support.code_files import extract_code_file_map_from_payload
from mozaiksai.core.workflow.generator_support.design_access_policy import access_policy_sha256
from mozaiksai.core.workflow.generator_support.module_smoke_fixtures import (
    load_module_smoke_fixtures,
)
from mozaiksai.core.workflow.task_batches import (
    _code_owned_task_output,
    _run_one_task,
    load_task_batches_config,
)
from tests.reviewed_pack_fixture import registered_pack_fixture, reviewed_pack_fixture
from tests.test_pack_app_owned_fixtures import EDITOR, FIXTURE, PACK, _context, _fixture, _selection


@pytest.fixture
def inputs():
    context = _context().snapshot()
    # The worker receives the pre-execution task, never an already accepted result.
    context["app_task_batch_results"] = {}
    task = context["app_task_batch_items"][0]
    return task, context


def test_fixture_only_task_materializes_one_admitted_file_with_reviewed_roles(inputs):
    task, context = inputs
    output = _code_owned_task_output(task, context)
    assert output is not None
    assert output == _code_owned_task_output(task, deepcopy(context))
    contract = output["module_contract"]
    assert set(contract) == {"module_id", "fixtures_yaml"}
    assert contract["module_id"] == "content"
    emitted = extract_code_file_map_from_payload(output)
    assert set(emitted) == {FIXTURE}
    assert yaml.safe_load(emitted[FIXTURE]) == contract["fixtures_yaml"]

    inventory = verified_pack_template_inventory([_selection()])
    templates = {row["filename"]: row["content"] for row in inventory[0]["files"]}
    fixtures = load_module_smoke_fixtures({**templates, **context["generated_files"], **emitted})
    assert set(fixtures) == {("content", action) for action in ("create_entry", "update_entry", "archive_entry")}
    create = fixtures["content", "create_entry"]
    assert create.roles == [EDITOR]
    assert any(param.field == "title" and param.source == "literal" for param in create.params)
    for action in ("update_entry", "archive_entry"):
        fixture = fixtures["content", action]
        assert fixture.roles == [EDITOR]
        step, = fixture.setup
        assert (step.module, step.action, step.roles) == ("content", "create_entry", [EDITOR])
        identifier, = (param for param in fixture.params if param.field == "entry_id")
        assert (identifier.source, identifier.step_id, identifier.response_path) == (
            "response", step.id, ["entry", "entry_id"],
        )


@pytest.mark.asyncio
async def test_fixture_only_task_completes_the_real_batch_without_calling_a_worker(inputs, monkeypatch):
    task, context = inputs
    task["initial_message"] = "Produce the assigned Content smoke fixtures."
    batch = load_task_batches_config("AppGenerator", PACK.parent.parent / "workflows").batches[0]

    async def unexpected_turn(*args, **kwargs):
        pytest.fail("Deterministic fixture task attempted an AG2 turn")

    monkeypatch.setattr("mozaiksai.core.workflow.task_batches.AG2TaskBatchRunner.run", unexpected_turn)
    outcome = await _run_one_task(
        workflow_name="AppGenerator", batch=batch, task=task, all_task_items=[task], base_context=context,
        completed_task_outputs={}, current_batch_outputs={}, agents={task["initial_agent"]: object()},
        chat_id="smoke-fixture-chat", app_id=context["app_id"], user_id="fixture-observer",
        semaphore=asyncio.Semaphore(1), fresh_agents_per_task=False, agents_factory=None, context_authority_policy=None,
    )
    assert outcome["_task_id"] == task["task_id"]
    assert [(row["filename"], yaml.safe_load(row["content"])) for row in outcome["code_files"]] == [
        (FIXTURE, outcome["module_contract"]["fixtures_yaml"]),
    ]


def test_revision_with_app_authored_fixture_defers_and_preserves_it(inputs):
    task, context = inputs
    context["build_mode"] = "revision"
    authored = yaml.safe_dump(_fixture(), sort_keys=False)
    context["generated_files"][FIXTURE] = authored
    assert _code_owned_task_output(task, context) is None
    assert context["generated_files"][FIXTURE] == authored


def test_mixed_task_still_needs_its_worker(inputs):
    task, context = inputs
    mixed = {**task, "owned_paths": [FIXTURE, "modules/content/contracts/profile.yaml"]}
    assert _code_owned_task_output(mixed, context) is None


def test_unselected_pack_does_not_supply_a_recipe(inputs):
    task, _ = inputs
    context = reviewed_pack_fixture({"app_build_plan": {"capability_packs": []}}, [])
    assert _code_owned_task_output({**task, "capability_pack_id": None}, context) is None


def test_candidate_auth_cannot_supply_missing_reviewed_grant(inputs):
    task, context = inputs
    # The candidate auth.yaml still grants content.manage. Only the owner review counts.
    context = reviewed_pack_fixture(context, ["content"], access_policy={"roles": [], "grants": []})
    with pytest.raises(ValueError, match="(?i)grant|role|permission"):
        _code_owned_task_output(task, context)


def test_stale_reviewed_pack_pin_cannot_supply_a_recipe(inputs):
    task, context = inputs
    summary = context["design_docs_artifact"]["commit_metadata"]["metadata"]["summary_payload"]
    review = summary["access_policy_review"]
    review["pack_identities"][0]["digest"] = "sha256:" + "0" * 64
    review["policy_sha256"] = access_policy_sha256(
        summary["access_policy"], summary["experience_spec"], pack_identities=review["pack_identities"],
    )
    with pytest.raises(PackIntegrityError, match="changed|renew|digest"):
        _code_owned_task_output(task, context)


@pytest.mark.parametrize("keep_original_recipe", [False, True], ids=["foreign-owner", "duplicate-claim"])
def test_other_selected_pack_cannot_claim_content_fixture(inputs, tmp_path, monkeypatch, keep_original_recipe):
    task, context = inputs
    registered_pack_fixture(tmp_path, monkeypatch, "guest", "generated_module")
    registry = tmp_path / "pack-registry"
    shutil.copytree(PACK, registry / "content", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    content_file = registry / "content" / "contract.yaml"
    content = yaml.safe_load(content_file.read_text(encoding="utf-8"))
    recipe = next(row for row in content["required_outputs"] if row["path"] == FIXTURE)
    guest_file = registry / "guest" / "contract.yaml"
    guest = yaml.safe_load(guest_file.read_text(encoding="utf-8"))
    guest["required_outputs"] = [deepcopy(recipe)]
    guest_file.write_text(yaml.safe_dump(guest, sort_keys=False), encoding="utf-8")
    if not keep_original_recipe:
        del recipe["smoke_fixtures"]
        content_file.write_text(yaml.safe_dump(content, sort_keys=False), encoding="utf-8")
    available = available_pack_rows(context, pack_ids={"content", "guest"})
    assert Path(available["content"]["pack_source_path"]).resolve() == (registry / "content").resolve()
    selection = [{"capability_pack_id": name, "capability_source": row["capability_source"],
                  "version": row["version"], "digest": row["digest"]} for name, row in sorted(available.items())]
    context["app_build_plan"]["capability_packs"] = selection
    context["capability_packs"] = list(available.values())
    summary = context["design_docs_artifact"]["commit_metadata"]["metadata"]["summary_payload"]
    context = reviewed_pack_fixture(context, ["content", "guest"], access_policy=summary["access_policy"])
    with pytest.raises((ValueError, PackIntegrityError), match="(?i)conflict|fixture|owner|claim"):
        _code_owned_task_output(task, context)


@pytest.mark.parametrize("defect", ["owner", "path", "target-role", "setup-role", "invalid-literal"])
def test_pack_integrity_rejects_invalid_recipe_before_task_dispatch(tmp_path, defect):
    source = tmp_path / "content"
    shutil.copytree(PACK, source, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    contract_file = source / "contract.yaml"
    contract = yaml.safe_load(contract_file.read_text(encoding="utf-8"))
    recipe = next(row for row in contract["required_outputs"] if row["path"] == FIXTURE)
    fixtures = recipe["smoke_fixtures"]["fixtures"]
    if defect == "owner":
        recipe["owner"] = "templates"
    elif defect == "path":
        recipe["path"] = "modules/content/contracts/service.yaml"
    elif defect == "target-role":
        fixtures[0]["roles"] = ["admin"]
    elif defect == "setup-role":
        fixtures[1]["setup"][0]["roles"] = ["admin"]
    else:
        fixtures[0]["params"][0]["value_json"] = "invalid JSON"
    contract_file.write_text(yaml.safe_dump(contract, sort_keys=False), encoding="utf-8")
    with pytest.raises(PackIntegrityError, match="smoke_fixtures"):
        verify_pack_integrity(source, "content")
