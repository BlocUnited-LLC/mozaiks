from __future__ import annotations

import asyncio
import importlib
import importlib.util
import sys
from pathlib import Path

import pytest


def _workspace() -> Path:
    return Path(__file__).resolve().parents[1]


_FACTORY_APP_PATH = str(_workspace() / "factory_app")


@pytest.fixture(autouse=True)
def _clean_factory_app_syspath():
    """Ensure factory_app/ is on sys.path during the test and clean up imported workflow modules after."""
    added = _FACTORY_APP_PATH not in sys.path
    if added:
        sys.path.insert(0, _FACTORY_APP_PATH)

    # Track modules added during the test
    before = set(sys.modules.keys())

    yield

    # Remove only workflow-namespace modules added during the test to avoid cross-test pollution.
    # Do NOT remove mozaiksai.* modules — other tests in the suite depend on them staying cached.
    added_keys = [
        k for k in sys.modules
        if k not in before and (k.startswith("workflows.") or k.startswith("factory_app."))
    ]
    for k in added_keys:
        del sys.modules[k]

    if added:
        try:
            sys.path.remove(_FACTORY_APP_PATH)
        except ValueError:
            pass


def _import_workflow_module(module_name: str):
    """Import a workflow tool file directly from this repo's workflow pack."""
    parts = module_name.split(".")
    if len(parts) < 4 or parts[0] != "workflows":
        return importlib.import_module(module_name)

    workflow_name = parts[1]
    relative_parts = parts[3:]
    file_path = _workspace() / "factory_app" / "workflows" / workflow_name / "tools" / f"{relative_parts[-1]}.py"
    module_name_direct = f"tests.{workflow_name.lower()}_{relative_parts[-1]}_direct"
    spec = importlib.util.spec_from_file_location(module_name_direct, file_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load module spec for {file_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Context:
    def __init__(self, initial: dict | None = None) -> None:
        self._data = dict(initial or {})

    def get(self, key: str, default=None):
        return self._data.get(key, default)

    def set(self, key: str, value) -> None:
        self._data[key] = value


@pytest.mark.parametrize(
    ("status", "integration_passed", "allow_export"),
    [
        ("passed", True, True),
        ("skipped", True, True),
        ("failed", True, False),
        ("pending", True, False),
        (None, True, False),
        ("passed", False, False),
    ],
)
def test_resolve_export_gate_uses_validation_status_and_integration(status, integration_passed, allow_export) -> None:
    module = _import_workflow_module("workflows.AppGenerator.tools.export_app_code")
    context = _Context(
        {
            "app_validation_status": status,
            "app_validation_strategy_used": "local",
            "integration_tests_passed": integration_passed,
        }
    )

    gate = module.resolve_export_gate(context)

    assert gate["allow_export"] is allow_export
    assert gate["app_validation_status"] == (status.lower() if isinstance(status, str) else status)
    assert gate["app_validation_strategy_used"] == "local"
    assert gate["integration_tests_passed"] is integration_passed


def test_validation_strategy_defaults_to_skip_when_e2b_and_local_are_unavailable(monkeypatch) -> None:
    from mozaiksai.core.workflow.generator_support import app_validation_strategy as app_validation_strategy_module

    monkeypatch.delenv("E2B_API_KEY", raising=False)
    monkeypatch.delenv("MOZAIKS_APP_VALIDATION_STRATEGY", raising=False)
    monkeypatch.setattr(app_validation_strategy_module, "local_app_validation_available", lambda: False)

    strategy, reason = app_validation_strategy_module.resolve_app_validation_strategy(requested=None, context_value=None)

    assert strategy == "skip"
    assert "resolved" in reason


def test_validation_strategy_summary_exposes_allowed_values() -> None:
    from mozaiksai.core.workflow.generator_support.app_validation_strategy import (
        build_app_validation_strategy_summary,
    )

    summary = build_app_validation_strategy_summary(env={}, local_available=False)

    assert summary["allowed_values"] == ["e2b", "local", "skip"]
    assert summary["default_value"] == "skip"
    assert summary["options"][0]["value"] == "e2b"
    assert summary["options"][1]["value"] == "local"
    assert summary["options"][2]["value"] == "skip"


def test_validation_strategy_rejects_invalid_explicit_values() -> None:
    from mozaiksai.core.workflow.generator_support.app_validation_strategy import (
        resolve_app_validation_strategy,
    )

    with pytest.raises(ValueError):
        resolve_app_validation_strategy(requested="invalid", context_value=None)


def test_validate_app_build_skip_strategy_persists_context() -> None:
    module = _import_workflow_module("workflows.AppGenerator.tools.app_validation")
    context = _Context({"workflow_name": "AppGenerator", "chat_id": "chat-1", "app_id": "app-1"})

    result = asyncio.run(
        module.validate_app_build(
            files={"package.json": '{"name":"demo","scripts":{"build":"echo build"}}'},
            validation_strategy="skip",
            context_variables=context,
        )
    )

    assert result["success"] is True
    assert result["validation_strategy"] == "skip"
    assert result["validation_status"] == "skipped"
    assert context.get("app_validation_status") == "skipped"
    assert context.get("app_validation_strategy_used") == "skip"
    assert context.get("app_validation_preview_url") is None


def test_validate_wiring_tool_annotations_are_runtime_resolved() -> None:
    from mozaiksai.core.workflow.agents.tools import load_agent_tool_functions

    mapping = load_agent_tool_functions("AppGenerator")
    validate_wiring = next(fn for fn in mapping["IntegrationTestAgent"] if fn.__name__ == "validate_wiring")

    assert validate_wiring.__annotations__["context_variables"] != "Optional[Dict[str, Any]]"
