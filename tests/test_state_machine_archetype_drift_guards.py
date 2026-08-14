"""Drift guards for the state_machine module archetype and cron tick contract.

These tests protect against regressions in:
- The cron_tick.py template that InfraScaffoldAgent emits for state_machine modules
- The state_machine archetype spec in module_archetypes.yaml
- The infra pack context.yaml that declares cron tick template variables
- The runtime behaviour of _MinimalCtx and the run() exit-code logic
- The call contract for get_mongo_client() and close_mongo_client() (no args,
  close_mongo_client is synchronous — both regressions were caught by this suite)

No network calls or real MongoDB are used. Mongo client and service calls are
patched with unittest.mock to keep each test deterministic and fast.
"""

from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import yaml

WORKSPACE = Path(__file__).resolve().parents[1]
INFRA_DIR = WORKSPACE / "factory_app" / "build_context" / "infra"
CRON_TICK_TEMPLATE = INFRA_DIR / "templates" / "scripts" / "cron_tick.py"
INFRA_CONTEXT = INFRA_DIR / "context.yaml"
MODULE_ARCHETYPES = (
    WORKSPACE / "factory_app" / "build_context" / "AppGenerator" / "module_archetypes.yaml"
)

# Template variables expected in cron_tick.py
_EXPECTED_TEMPLATE_VARS = {
    "{{ACTION_LABEL}}",
    "{{ACTION_DESCRIPTION}}",
    "{{CRON_INTERVAL_MINUTES}}",
    "{{CRON_SCRIPT_NAME}}",
    "{{MODULE_ID}}",
    "{{SERVICE_CLASS}}",
    "{{TICK_METHOD}}",
    "{{ACTION_ID}}",
}


def _read_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. Cron tick template — file contract
# ---------------------------------------------------------------------------


def test_cron_tick_template_file_exists() -> None:
    """The cron_tick.py template must exist at the canonical infra scripts path."""
    assert CRON_TICK_TEMPLATE.exists(), (
        f"Missing cron tick template at {CRON_TICK_TEMPLATE}. "
        "InfraScaffoldAgent emits one copy per state_machine module."
    )


def test_cron_tick_template_contains_minimal_ctx_class() -> None:
    """Template must define _MinimalCtx with an async emit method."""
    source = _read_text(CRON_TICK_TEMPLATE)
    assert "class _MinimalCtx:" in source, "Template must define class _MinimalCtx"
    assert "async def emit(" in source, "_MinimalCtx must have an async emit() method"


def test_cron_tick_template_imports_get_mongo_client() -> None:
    """Template must import get_mongo_client from mozaiksai.core.core_config."""
    source = _read_text(CRON_TICK_TEMPLATE)
    assert "get_mongo_client" in source, (
        "Template must import and call get_mongo_client from mozaiksai.core.core_config"
    )


def test_cron_tick_template_imports_close_mongo_client() -> None:
    """Template must import close_mongo_client for cleanup in the finally block."""
    source = _read_text(CRON_TICK_TEMPLATE)
    assert "close_mongo_client" in source, (
        "Template must import and call close_mongo_client to release the connection"
    )


def test_cron_tick_template_calls_get_mongo_client_with_no_args() -> None:
    """get_mongo_client() takes no arguments — template must not pass kwargs.

    Regression guard: the original template called get_mongo_client(mongo_uri=mongo_uri)
    which raises TypeError at runtime because the function signature has no parameters.
    The --mongo-uri CLI value is propagated via os.environ.setdefault("MONGO_URI", ...).
    """
    source = _read_text(CRON_TICK_TEMPLATE)
    # Must call the no-arg form
    assert "get_mongo_client()" in source, (
        "Template must call get_mongo_client() with no arguments. "
        "Pass --mongo-uri via os.environ.setdefault('MONGO_URI', ...) instead."
    )
    # Must NOT pass mongo_uri as a kwarg
    assert "get_mongo_client(mongo_uri=" not in source, (
        "Template must not pass mongo_uri= to get_mongo_client(); the function takes no args."
    )


def test_cron_tick_template_calls_close_mongo_client_synchronously() -> None:
    """close_mongo_client() is synchronous — template must not await it.

    Regression guard: the original template called `await close_mongo_client(client)`
    which raises TypeError (not a coroutine) and TypeError (unexpected arg) at runtime.
    """
    source = _read_text(CRON_TICK_TEMPLATE)
    assert "await close_mongo_client" not in source, (
        "Template must not await close_mongo_client(); it is a synchronous function."
    )
    assert "close_mongo_client()" in source, (
        "Template must call close_mongo_client() with no arguments."
    )


def test_cron_tick_template_sets_mongo_uri_env_from_arg() -> None:
    """Template must propagate --mongo-uri to MONGO_URI env var via os.environ.

    Because get_mongo_client() reads MONGO_URI from the environment, the template
    must set os.environ when --mongo-uri is provided on the command line.
    """
    source = _read_text(CRON_TICK_TEMPLATE)
    assert "os.environ" in source, (
        "Template must use os.environ to propagate --mongo-uri to MONGO_URI env var."
    )
    assert "import os" in source, (
        "Template must import os to use os.environ."
    )


def test_cron_tick_template_uses_argparse_with_mongo_uri_arg() -> None:
    """Template must use argparse and expose --mongo-uri argument."""
    source = _read_text(CRON_TICK_TEMPLATE)
    assert "import argparse" in source, "Template must import argparse"
    assert "--mongo-uri" in source, "Template must declare a --mongo-uri argument"


def test_cron_tick_template_exits_nonzero_on_errors() -> None:
    """Template run() must return 1 when errors list is non-empty, 0 otherwise."""
    source = _read_text(CRON_TICK_TEMPLATE)
    assert "return 1 if errors else 0" in source, (
        "Template must return 1 when the tick result has errors, 0 on success"
    )


def test_cron_tick_template_documents_mongo_uri_env_var() -> None:
    """Template docstring must document the MONGO_URI required env var."""
    source = _read_text(CRON_TICK_TEMPLATE)
    assert "MONGO_URI" in source, (
        "Template module docstring must document MONGO_URI as a required env var"
    )


def test_cron_tick_template_uses_finally_for_close() -> None:
    """Template must call close_mongo_client in a finally block."""
    source = _read_text(CRON_TICK_TEMPLATE)
    assert "finally:" in source, "Template must use a try/finally to guarantee cleanup"
    finally_pos = source.index("finally:")
    close_pos = source.rfind("close_mongo_client")
    assert close_pos > finally_pos, (
        "close_mongo_client must be called inside the finally block (after 'finally:'). "
        f"finally: at {finally_pos}, last close_mongo_client at {close_pos}"
    )


def test_cron_tick_template_contains_all_expected_template_vars() -> None:
    """All required {{...}} template variables must appear in the template source."""
    source = _read_text(CRON_TICK_TEMPLATE)
    missing = [var for var in _EXPECTED_TEMPLATE_VARS if var not in source]
    assert not missing, (
        f"Missing template variable(s) in cron_tick.py: {missing}. "
        "InfraScaffoldAgent substitutes these at generation time."
    )


def test_cron_tick_template_errors_key_from_tick_result() -> None:
    """Template must read the 'errors' key from the tick result dict."""
    source = _read_text(CRON_TICK_TEMPLATE)
    assert 'result.get("errors"' in source or "result.get('errors'" in source, (
        "Template must extract the 'errors' key from the tick result via result.get(\"errors\", [])"
    )


# ---------------------------------------------------------------------------
# 2. State machine archetype contract — module_archetypes.yaml
# ---------------------------------------------------------------------------

# NOTE: state_machine archetype tests were removed when the state_machine module
# type was dropped from structured_outputs.yaml and module_archetypes.yaml.
# The runtime ModuleIdentity.type Literal never accepted state_machine, so the
# archetype was a false promise.  The cron tick template and infra pack tests
# below remain — they test infrastructure that other archetypes may use.


# ---------------------------------------------------------------------------
# 3. Infra pack context.yaml — template variable declarations
# ---------------------------------------------------------------------------


def test_infra_context_declares_cron_tick_template_vars() -> None:
    """infra/context.yaml must declare the cron tick template variable set."""
    context_text = _read_text(INFRA_CONTEXT)
    for var in _EXPECTED_TEMPLATE_VARS:
        var_name = var.strip("{}")
        assert var_name in context_text, (
            f"infra/context.yaml must document cron tick template variable '{var_name}'"
        )


def test_infra_context_declares_azure_container_apps_job_guidance() -> None:
    """infra/context.yaml must describe the ACA job resource requirement for state_machine modules."""
    context_text = _read_text(INFRA_CONTEXT)
    assert "Microsoft.App/jobs" in context_text or "azure_container_apps" in context_text, (
        "infra/context.yaml must mention Microsoft.App/jobs or azure_container_apps "
        "for state_machine cron integration"
    )


def test_infra_context_references_cron_tick_template_path() -> None:
    """infra/context.yaml must reference templates/scripts/cron_tick.py."""
    context_text = _read_text(INFRA_CONTEXT)
    assert "cron_tick.py" in context_text, (
        "infra/context.yaml must reference templates/scripts/cron_tick.py"
    )


def test_infra_context_template_vars_consistent_with_template_file() -> None:
    """Template variables declared in context.yaml must all appear in cron_tick.py."""
    context_text = _read_text(INFRA_CONTEXT)
    template_text = _read_text(CRON_TICK_TEMPLATE)
    for var in _EXPECTED_TEMPLATE_VARS:
        var_name = var.strip("{}")
        assert var_name in context_text, (
            f"context.yaml missing variable declaration: {var_name}"
        )
        assert var in template_text, (
            f"cron_tick.py template missing variable usage: {var}"
        )


# ---------------------------------------------------------------------------
# 4. _MinimalCtx behavioural test — using substituted source
# ---------------------------------------------------------------------------

def _build_runnable_module() -> types.ModuleType:
    """Return a module object compiled from cron_tick.py with all template vars substituted.

    We replace every {{VAR}} with a dummy value so the source is valid Python,
    then compile and exec it into a fresh module namespace. The module imports
    are mocked so no real MongoDB or service packages are needed.
    """
    source = _read_text(CRON_TICK_TEMPLATE)

    substitutions = {
        "{{ACTION_LABEL}}": "Test Lifecycle",
        "{{ACTION_DESCRIPTION}}": "Runs the test lifecycle tick.",
        "{{CRON_INTERVAL_MINUTES}}": "5",
        "{{CRON_SCRIPT_NAME}}": "cron_test_lifecycle",
        "{{MODULE_ID}}": "test_module",
        "{{SERVICE_CLASS}}": "TestService",
        "{{TICK_METHOD}}": "run_test_tick",
        "{{ACTION_ID}}": "test_lifecycle",
    }
    for var, value in substitutions.items():
        source = source.replace(var, value)

    fake_module = types.ModuleType("cron_test_lifecycle")
    fake_module.__file__ = str(CRON_TICK_TEMPLATE)

    # Dummy get_mongo_client / close_mongo_client — matching the fixed signatures
    fake_get_mongo = MagicMock(return_value=MagicMock())
    fake_close_mongo = MagicMock()  # synchronous, no args

    class _DummyService:
        async def run_test_tick(self, ctx: Any) -> dict:  # noqa: ANN001
            return {"errors": []}

    _installed: list[str] = []
    for mod_name in [
        "app",
        "app.modules",
        "app.modules.test_module",
        "app.modules.test_module.backend",
        "app.modules.test_module.backend.service",
        "mozaiksai",
        "mozaiksai.core",
        "mozaiksai.core.core_config",
    ]:
        if mod_name not in sys.modules:
            fake = types.ModuleType(mod_name)
            sys.modules[mod_name] = fake
            _installed.append(mod_name)

    sys.modules["app.modules.test_module.backend.service"].TestService = _DummyService  # type: ignore[attr-defined]
    sys.modules["mozaiksai.core.core_config"].get_mongo_client = fake_get_mongo  # type: ignore[attr-defined]
    sys.modules["mozaiksai.core.core_config"].close_mongo_client = fake_close_mongo  # type: ignore[attr-defined]

    try:
        code = compile(source, str(CRON_TICK_TEMPLATE), "exec")
        exec(code, fake_module.__dict__)  # noqa: S102
    finally:
        for mod_name in _installed:
            sys.modules.pop(mod_name, None)

    return fake_module


def test_minimal_ctx_emitted_starts_empty() -> None:
    """_MinimalCtx._emitted must be an empty list on construction."""
    mod = _build_runnable_module()
    ctx = mod._MinimalCtx()
    assert ctx._emitted == [], "_MinimalCtx._emitted must start as an empty list"


def test_minimal_ctx_emit_captures_event() -> None:
    """Calling ctx.emit() must append (event_type, payload) to ctx._emitted."""
    mod = _build_runnable_module()
    ctx = mod._MinimalCtx()
    asyncio.run(ctx.emit("test.event", {"key": "val"}))
    assert len(ctx._emitted) == 1, "emit() must add one entry to _emitted"
    event_type, payload = ctx._emitted[0]
    assert event_type == "test.event"
    assert payload == {"key": "val"}


def test_minimal_ctx_emit_multiple_events() -> None:
    """Each emit() call appends independently; order is preserved."""
    mod = _build_runnable_module()
    ctx = mod._MinimalCtx()
    asyncio.run(ctx.emit("event.one", {"n": 1}))
    asyncio.run(ctx.emit("event.two", {"n": 2}))
    assert len(ctx._emitted) == 2
    assert ctx._emitted[0][0] == "event.one"
    assert ctx._emitted[1][0] == "event.two"


# ---------------------------------------------------------------------------
# 5. run() exit-code and cleanup — mock service + mongo client
# ---------------------------------------------------------------------------


def _make_mock_module_with_service(tick_return: dict) -> types.ModuleType:
    """Build the runnable module and patch the embedded service to return tick_return."""
    mod = _build_runnable_module()

    class _MockService:
        async def run_test_tick(self, ctx: Any) -> dict:  # noqa: ANN001
            return tick_return

    mod.TestService = _MockService  # type: ignore[attr-defined]
    return mod


def test_run_returns_zero_when_no_errors() -> None:
    """run() must return 0 when the tick result contains an empty errors list."""
    mod = _make_mock_module_with_service({"errors": []})
    fake_close = MagicMock()  # synchronous after the fix

    with (
        patch.object(mod, "get_mongo_client", return_value=MagicMock()),
        patch.object(mod, "close_mongo_client", fake_close),
    ):
        result = asyncio.run(mod.run())

    assert result == 0, "run() must return 0 when there are no errors"


def test_run_returns_one_when_errors_present() -> None:
    """run() must return 1 when the tick result contains a non-empty errors list."""
    mod = _make_mock_module_with_service({"errors": ["something failed"]})
    fake_close = MagicMock()

    with (
        patch.object(mod, "get_mongo_client", return_value=MagicMock()),
        patch.object(mod, "close_mongo_client", fake_close),
    ):
        result = asyncio.run(mod.run())

    assert result == 1, "run() must return 1 when the tick result has errors"


def test_close_mongo_client_called_with_no_args() -> None:
    """close_mongo_client() must be called with no arguments.

    Regression guard: the original template called close_mongo_client(client)
    which raises TypeError because the function takes no parameters.
    """
    mod = _make_mock_module_with_service({"errors": []})
    fake_close = MagicMock()

    with (
        patch.object(mod, "get_mongo_client", return_value=MagicMock()),
        patch.object(mod, "close_mongo_client", fake_close),
    ):
        asyncio.run(mod.run())

    fake_close.assert_called_once_with(), (
        "close_mongo_client must be called with no arguments"
    )


def test_close_mongo_client_called_even_on_error() -> None:
    """close_mongo_client must be called in the finally block regardless of errors."""
    mod = _make_mock_module_with_service({"errors": ["tick error"]})
    fake_close = MagicMock()

    with (
        patch.object(mod, "get_mongo_client", return_value=MagicMock()),
        patch.object(mod, "close_mongo_client", fake_close),
    ):
        asyncio.run(mod.run())

    fake_close.assert_called_once(), (
        "close_mongo_client must be called (finally block) even when errors are present"
    )


def test_get_mongo_client_called_with_no_args() -> None:
    """get_mongo_client() must be called with no arguments.

    Regression guard: the original template called get_mongo_client(mongo_uri=mongo_uri)
    which raises TypeError because the function signature has no parameters.
    The mongo_uri is propagated via os.environ instead.
    """
    mod = _make_mock_module_with_service({"errors": []})
    fake_get = MagicMock(return_value=MagicMock())

    with (
        patch.object(mod, "get_mongo_client", fake_get),
        patch.object(mod, "close_mongo_client", MagicMock()),
    ):
        asyncio.run(mod.run())

    fake_get.assert_called_once_with(), (
        "get_mongo_client must be called with no arguments"
    )
