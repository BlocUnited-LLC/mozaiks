"""A failed mapped module can reach acceptance without losing successful files."""

from copy import deepcopy

import pytest

from factory_app.workflows.AppGenerator.tools.assemble_app_tasks import assemble_app_tasks
from tests.test_appgenerator_entitlement_mapping import MODULE_PATH, _context


def _partial_context(*, failed="module_contract", owned_path=MODULE_PATH):
    context = _context()
    context.set("app_build_plan", {
        "build_tasks": [{"task_id": "module_contract", "owned_paths": [owned_path]}],
    })
    context.set("app_task_batch_results", {
        "support_files": {"code_files": [{"filename": "README.md", "content": "# Tasks\n"}]},
        "_failed": {failed: {"status": "failed", "error": "Module task failed"}},
        "_meta": {"status": "partial", "failed_tasks": [failed]},
    })
    return context


@pytest.mark.asyncio
@pytest.mark.parametrize("owned_path", [MODULE_PATH, f"./{MODULE_PATH}", MODULE_PATH.replace("/", "\\")])
async def test_failed_exact_module_owner_preserves_partial_bundle_for_acceptance(owned_path):
    context = _partial_context(owned_path=owned_path)
    failure = deepcopy(context.snapshot()["app_task_batch_results"]["_failed"])

    result = await assemble_app_tasks(context_variables=context)

    files = {file["filename"]: file["content"] for file in result["code_files"]}
    assert MODULE_PATH not in files
    assert files["README.md"] == "# Tasks\n"
    assert "config/subscriptions.yaml" in files
    assert context.get("generated_files")["README.md"] == files["README.md"]
    assert context.snapshot()["app_task_batch_results_summary"]["failed_tasks"] == ["module_contract"]
    assert context.snapshot()["app_task_batch_results"]["_failed"] == failure


@pytest.mark.asyncio
@pytest.mark.parametrize("failed,owned_path", [
    ("unrelated_task", MODULE_PATH),
    ("module_contract", "modules/other/module.yaml"),
])
async def test_missing_module_requires_its_exact_owner_failure(failed, owned_path):
    context = _partial_context(failed=failed, owned_path=owned_path)

    with pytest.raises(ValueError, match="Missing module.yaml"):
        await assemble_app_tasks(context_variables=context)


@pytest.mark.asyncio
async def test_partial_batch_does_not_excuse_missing_product_mapping():
    context = _partial_context()
    contract = context.snapshot()["subscription_contract"]
    contract["module_contract_updates"] = []
    context.set("subscription_contract", contract)

    with pytest.raises(ValueError, match="Unmapped capability ids"):
        await assemble_app_tasks(context_variables=context)
