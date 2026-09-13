"""Session contracts must be importable without warming the adapter package."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

_PROBE = """
import sys

def deny_network(event, args):
    if event in {"socket.connect", "socket.getaddrinfo"}:
        raise AssertionError("Cold imports must not connect to external services")

sys.addaudithook(deny_network)
"""


@pytest.mark.parametrize("code", [
    pytest.param("""
from mozaiksai.core.usage.context import AuxiliaryUsageContext
from mozaiksai.core.session.build_binding import RunBuildBinding
context = AuxiliaryUsageContext(app_id="host", user_id="owner", run_build_binding={
    "build_registry_id": "registry", "target_app_id": "target",
    "build_id": "build", "phase": "refinement",
})
assert type(context.run_build_binding) is RunBuildBinding
""", id="context-first"),
    pytest.param("""
from mozaiksai.core.session.build_binding import RunBuildBinding
assert "mozaiksai.core.session.launcher" not in sys.modules
assert "mozaiksai.core.workflow.task_batches" not in sys.modules
assert "mozaiksai.core.adapters.ag2_agent_runner" not in sys.modules
from mozaiksai.core.usage.context import AuxiliaryUsageContext
assert AuxiliaryUsageContext.model_fields["run_build_binding"].annotation == RunBuildBinding | None
""", id="binding-first"),
    pytest.param("""
from mozaiksai.core.adapters.ag2_agent_runner import AG2StructuredAgentRunner
from mozaiksai.core.usage.context import AuxiliaryUsageContext
from mozaiksai.core.session.build_binding import RunBuildBinding
assert AG2StructuredAgentRunner.__module__ == "mozaiksai.core.adapters.ag2_agent_runner"
assert AuxiliaryUsageContext.model_fields["run_build_binding"].annotation == RunBuildBinding | None
""", id="adapter-first"),
    pytest.param("""
import mozaiksai.core.session as session
assert "mozaiksai.core.session.launcher" not in sys.modules
names = (
    "PreparedWorkflowLaunch", "TransitionLaunchResult", "WorkflowLaunchResult",
    "apply_launch_context_provider", "create_routed_chat_session",
    "emit_workflow_launch_navigation", "launch_prepared_workflow",
    "launch_routed_workflow", "launch_transition", "prepare_routed_workflow_launch",
    "validate_context_for_workflow",
)
for name in names:
    exported = getattr(session, name)
    canonical = getattr(sys.modules["mozaiksai.core.session.launcher"], name)
    assert exported is canonical
    assert getattr(session, name) is canonical
    namespace = {}
    exec("from mozaiksai.core.session import " + name, namespace)
    assert namespace[name] is canonical
assert set(session.__all__) == set(names) | {
    "BuildTargetReference", "RunBuildBinding", "JourneyAdvanceDecision",
    "PendingDecisionAction", "PendingHarnessDecision", "RevisionEntry",
    "RoutingDecision", "SequenceStatus", "SessionLifecycle", "SessionRouter",
    "SessionState", "SessionStateStore", "TransitionResolution", "TriggerInput",
    "UnmetDependency", "BuildContextError", "configure_session_router",
    "get_session_router", "get_session_router_for_chat", "merge_build_context",
    "resolve_build_context_root",
}
""", id="public-launcher-identity"),
    pytest.param("""
import mozaiksai.core.session as session
assert "mozaiksai.core.session.launcher" not in sys.modules
try:
    session.nonexistent_session_export
except AttributeError as exc:
    assert exc.args == ("nonexistent_session_export",)
else:
    raise AssertionError("Unknown session attributes must raise AttributeError")
assert "mozaiksai.core.session.launcher" not in sys.modules
""", id="unknown-attribute"),
])
def test_session_cold_import(code, tmp_path):
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("COV_CORE_") and key != "PYTHONPATH"}
    env.update(PYTHON_DOTENV_DISABLED="1", PYTHONDONTWRITEBYTECODE="1", LOGS_BASE_DIR=str(tmp_path))
    result = subprocess.run(
        [sys.executable, "-B", "-c", _PROBE + code], cwd=ROOT, env=env,
        text=True, capture_output=True, timeout=60, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
