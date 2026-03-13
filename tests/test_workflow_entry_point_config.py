from __future__ import annotations

from tests.import_utils import import_module_directly

_workflow_manager_mod = import_module_directly("mozaiksai.core.workflow.workflow_manager")
workflow_manager = _workflow_manager_mod.workflow_manager


def test_ai_config_marks_greenroom_as_entry_point() -> None:
    assert workflow_manager.get_config("GreenRoom").get("entry_point") is True


def test_ai_config_marks_writersroom_as_non_entry_point() -> None:
    assert workflow_manager.get_config("WritersRoom").get("entry_point") is False
