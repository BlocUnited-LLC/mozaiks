"""Independent repair feedback must not weaken module admission."""

import logging
from copy import deepcopy

import pytest

from mozaiksai.core.workflow.generator_support import module_write_actions
from tests.test_generated_role_grants import _arguments, _closed


def _candidate():
    payload = _closed()
    actions = {action["id"]: action for action in payload["module_contract"]["module_yaml"]["actions"]}
    actions["create_task"]["input_schema"] = {
        "type": "object", "properties": [
            {"name": "title", "type": "string", "required": True, "nullable": True},
        ],
    }
    actions["get_tasks"]["input_schema"] = {
        "type": "object", "properties": [
            {"name": "lookup", "type": "string", "required": True},
        ],
    }
    return payload


def test_write_and_read_fixture_rejections_arrive_together_without_mutating_candidate():
    payload = _candidate()
    before = deepcopy(payload)
    with pytest.raises(ValueError) as rejection:
        module_write_actions.close_module_actions(payload, **_arguments())
    message = str(rejection.value).lower()
    assert "nullable" in message
    assert "get_tasks" in message and "does not admit the observer default" in message
    assert payload == before

    actions = {action["id"]: action for action in payload["module_contract"]["module_yaml"]["actions"]}
    actions["create_task"]["input_schema"]["properties"][0]["nullable"] = False
    with pytest.raises(ValueError, match="does not admit the observer default"):
        module_write_actions.close_module_actions(payload, **_arguments())
    actions["get_tasks"]["input_schema"]["properties"][0]["name"] = "id"
    admitted = module_write_actions.close_module_actions(payload, **_arguments())
    create = next(a for a in admitted["module_contract"]["module_yaml"]["actions"] if a["id"] == "create_task")
    assert create["permissions"] == ["tasks.create_task"]


def test_unavailable_read_diagnostic_preserves_the_original_write_rejection(monkeypatch):
    payload = _candidate()
    before = deepcopy(payload)

    def fail_read_probe(*args, **kwargs):
        raise RuntimeError("synthetic diagnostic failure")

    monkeypatch.setattr(module_write_actions, "close_module_read_actions", fail_read_probe)
    with pytest.raises(ValueError) as rejection:
        module_write_actions.close_module_actions(payload, **_arguments())
    assert "nullable" in str(rejection.value).lower()
    assert "synthetic diagnostic failure" not in str(rejection.value)
    assert payload == before


@pytest.mark.parametrize("failure_stage", ["read_closure", "fixture_validation"])
def test_broken_log_handler_never_masks_write_rejection(monkeypatch, failure_stage):
    from mozaiksai.core.workflow.generator_support import module_smoke_fixtures

    payload = _candidate()
    before = deepcopy(payload)

    def fail_diagnostic(*args, **kwargs):
        raise RuntimeError("synthetic diagnostic failure")

    if failure_stage == "read_closure":
        monkeypatch.setattr(module_write_actions, "close_module_read_actions", fail_diagnostic)
    else:
        monkeypatch.setattr(module_smoke_fixtures, "validate_module_fixture_record_identities", fail_diagnostic)

    class BrokenHandler(logging.Handler):
        emitted = False

        def emit(self, record):
            self.emitted = True
            raise RuntimeError("synthetic logging failure")

    handler = BrokenHandler()
    module_write_actions.logger.addHandler(handler)
    monkeypatch.setattr(module_write_actions.logger, "level", logging.WARNING)
    try:
        with pytest.raises(ValueError) as rejection:
            module_write_actions.close_module_actions(payload, **_arguments())
    finally:
        module_write_actions.logger.removeHandler(handler)
    assert handler.emitted
    assert "nullable" in str(rejection.value).lower()
    assert payload == before
