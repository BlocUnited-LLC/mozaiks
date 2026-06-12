"""
ExecutorRegistry unit tests.

Covers:
  - register / get / has for WORKFLOW and MODULE types
  - workflow_executor and module_executor property shortcuts
  - registered_types returns accurate list
  - summary returns type-to-class-name mapping
  - Executor Protocol structural conformance
  - overwrite semantics (re-registering same type replaces prior)
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

from mozaiksai.core.runtime.composition.executor_registry import (
    Executor,
    ExecutorRegistry,
    ExecutorType,
)

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

def _fake_executor(executor_type: ExecutorType, name: str = "FakeExecutor") -> MagicMock:
    """Return a MagicMock that satisfies the Executor protocol."""
    ex = MagicMock()
    ex.executor_type = executor_type
    ex.execute = AsyncMock(return_value={"ok": True})
    ex.health = AsyncMock(return_value={"status": "healthy"})
    type(ex).__name__ = name
    return ex


def _workflow_executor(name: str = "WorkflowExecutor") -> MagicMock:
    return _fake_executor(ExecutorType.WORKFLOW, name)


def _module_executor(name: str = "ModuleExecutor") -> MagicMock:
    return _fake_executor(ExecutorType.MODULE, name)


# ---------------------------------------------------------------------------
# 1. Register and get
# ---------------------------------------------------------------------------

class TestRegisterAndGet:
    def test_get_returns_none_when_empty(self):
        reg = ExecutorRegistry()
        assert reg.get(ExecutorType.WORKFLOW) is None

    def test_registered_executor_is_returned(self):
        reg = ExecutorRegistry()
        wf = _workflow_executor()
        reg.register(wf)
        assert reg.get(ExecutorType.WORKFLOW) is wf

    def test_module_executor_registered_independently(self):
        reg = ExecutorRegistry()
        mod = _module_executor()
        reg.register(mod)
        assert reg.get(ExecutorType.MODULE) is mod
        assert reg.get(ExecutorType.WORKFLOW) is None

    def test_overwrite_replaces_previous(self):
        reg = ExecutorRegistry()
        first = _workflow_executor("First")
        second = _workflow_executor("Second")
        reg.register(first)
        reg.register(second)
        assert reg.get(ExecutorType.WORKFLOW) is second

    def test_both_types_registered_independently(self):
        reg = ExecutorRegistry()
        wf = _workflow_executor()
        mod = _module_executor()
        reg.register(wf)
        reg.register(mod)
        assert reg.get(ExecutorType.WORKFLOW) is wf
        assert reg.get(ExecutorType.MODULE) is mod


# ---------------------------------------------------------------------------
# 2. has()
# ---------------------------------------------------------------------------

class TestHas:
    def test_has_returns_false_when_not_registered(self):
        reg = ExecutorRegistry()
        assert reg.has(ExecutorType.MODULE) is False

    def test_has_returns_true_when_registered(self):
        reg = ExecutorRegistry()
        reg.register(_workflow_executor())
        assert reg.has(ExecutorType.WORKFLOW) is True

    def test_has_false_for_unregistered_type_when_other_type_present(self):
        reg = ExecutorRegistry()
        reg.register(_workflow_executor())
        assert reg.has(ExecutorType.MODULE) is False


# ---------------------------------------------------------------------------
# 3. Property shortcuts
# ---------------------------------------------------------------------------

class TestPropertyShortcuts:
    def test_workflow_executor_property_returns_registered(self):
        reg = ExecutorRegistry()
        wf = _workflow_executor()
        reg.register(wf)
        assert reg.workflow_executor is wf

    def test_workflow_executor_property_returns_none_when_missing(self):
        reg = ExecutorRegistry()
        assert reg.workflow_executor is None

    def test_module_executor_property_returns_registered(self):
        reg = ExecutorRegistry()
        mod = _module_executor()
        reg.register(mod)
        assert reg.module_executor is mod

    def test_module_executor_property_returns_none_when_missing(self):
        reg = ExecutorRegistry()
        assert reg.module_executor is None


# ---------------------------------------------------------------------------
# 4. registered_types()
# ---------------------------------------------------------------------------

class TestRegisteredTypes:
    def test_empty_registry_returns_empty_list(self):
        reg = ExecutorRegistry()
        assert reg.registered_types() == []

    def test_returns_workflow_type_after_register(self):
        reg = ExecutorRegistry()
        reg.register(_workflow_executor())
        assert ExecutorType.WORKFLOW in reg.registered_types()

    def test_returns_both_types_when_both_registered(self):
        reg = ExecutorRegistry()
        reg.register(_workflow_executor())
        reg.register(_module_executor())
        types = set(reg.registered_types())
        assert types == {ExecutorType.WORKFLOW, ExecutorType.MODULE}

    def test_overwrite_does_not_grow_list(self):
        reg = ExecutorRegistry()
        reg.register(_workflow_executor())
        reg.register(_workflow_executor())  # overwrite
        assert len(reg.registered_types()) == 1


# ---------------------------------------------------------------------------
# 5. summary()
# ---------------------------------------------------------------------------

class TestSummary:
    def test_empty_registry_returns_empty_dict(self):
        reg = ExecutorRegistry()
        assert reg.summary() == {}

    def test_summary_maps_type_value_to_class_name(self):
        reg = ExecutorRegistry()

        class MyWorkflowExecutor:
            executor_type = ExecutorType.WORKFLOW
            async def execute(self, r, c): ...
            async def health(self): ...

        reg.register(MyWorkflowExecutor())
        s = reg.summary()
        assert s["workflow"] == "MyWorkflowExecutor"

    def test_summary_lists_all_registered_types(self):
        reg = ExecutorRegistry()
        reg.register(_workflow_executor())
        reg.register(_module_executor())
        s = reg.summary()
        assert "workflow" in s
        assert "module" in s


# ---------------------------------------------------------------------------
# 6. ExecutorType enum
# ---------------------------------------------------------------------------

class TestExecutorType:
    def test_workflow_value_is_string(self):
        assert ExecutorType.WORKFLOW == "workflow"

    def test_module_value_is_string(self):
        assert ExecutorType.MODULE == "module"

    def test_is_str_subclass(self):
        assert isinstance(ExecutorType.WORKFLOW, str)


# ---------------------------------------------------------------------------
# 7. Executor Protocol structural conformance
# ---------------------------------------------------------------------------

class TestExecutorProtocol:
    def test_conforming_class_passes_isinstance_check(self):
        """Class with executor_type, execute, health satisfies the Protocol."""

        class GoodExecutor:
            executor_type = ExecutorType.WORKFLOW

            async def execute(self, request: Any, context: Any) -> Any:
                return {}

            async def health(self) -> dict[str, Any]:
                return {"status": "healthy"}

        assert isinstance(GoodExecutor(), Executor)

    def test_mock_executor_passes_isinstance_check(self):
        """_fake_executor() mocks satisfy the Protocol."""
        wf = _workflow_executor()
        assert isinstance(wf, Executor)
