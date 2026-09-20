import json
from copy import deepcopy

import pytest

from factory_app.workflows.AppGenerator.tools.assemble_app_tasks import _apply_app_config_contracts
from factory_app.workflows.AppGenerator.tools.save_app_schema import resolve_app_theme_config
from mozaiksai.core.workflow.context.adapter import create_context_container


@pytest.mark.parametrize("patch", [None, {}, {"theme": {"density": "comfortable", "font": None}, "identity": None}])
def test_captured_theme_survives_task_assembly_and_partial_deltas(patch):
    captured = {
        "theme": {"primary": "emerald", "font": "system-ui", "density": "compact"},
        "identity": {"name": "support-desk", "app_name": "Support Desk"},
        "assets": {"logo": None},
        "colors": {"primary": {"main": "#047857"}},
        "ui": {"page": {"sectionGap": "16px"}},
    }
    original = deepcopy(captured)
    context = create_context_container(initial={"captured_theme_config": captured})
    code_files = [{"filename": "app.json", "content": '{"appName":"Support Desk"}'}]
    if patch is not None:
        code_files.append({"filename": "brand/theme_config.json", "content": json.dumps(patch)})
    output = _apply_app_config_contracts(
        code_files, app_id="support-desk", app_build_plan={}, context_variables=context,
    )
    files = {file["filename"]: file["content"] for file in output}
    result = json.loads(files["brand/theme_config.json"])
    expected = deepcopy(captured)
    if patch:
        expected["theme"]["density"] = "comfortable"
    assert result == expected
    assert resolve_app_theme_config(context.get("captured_theme_config"), patch) == expected
    assert context.snapshot()["captured_theme_config"] == original
    assert files["app.json"] == code_files[0]["content"]


def test_no_theme_is_not_replaced_with_an_invented_default():
    assert resolve_app_theme_config(None, None) is None
    assert resolve_app_theme_config(None, {"theme": {"primary": "rose"}}) == {"theme": {"primary": "rose"}}


@pytest.mark.parametrize("captured,patch", [("bad", None), (None, ["bad"]), (None, "bad")])
def test_invalid_theme_shapes_fail_closed(captured, patch):
    with pytest.raises(ValueError, match="must be an object or null"):
        resolve_app_theme_config(captured, patch)
