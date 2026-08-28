"""Regression proofs that historical test-order hazards stay fixed.

Two order hazards existed on main before this suite was added:

1. ``import mozaiksai.hosts.studio`` wrote ``PLATFORM_PATH`` and
   ``MOZAIKS_WORKFLOWS_PATH`` into ``os.environ`` at import time, so
   workspace-gated tests ran or skipped depending on whether an earlier test
   in the same process had imported the Studio host.
2. Studio tests called ``sys.modules.pop("factory_app", None)`` before
   importing the host. When those tests ran before
   ``tests/test_check_workspace_integrations_tool.py`` (the inversion of the
   alphabetical shard order), the pop broke that file's dotted-path
   ``monkeypatch.setattr("factory_app....")`` calls.

Each proof runs pytest in a subprocess with a CI-like environment (no
``PLATFORM_PATH`` / ``MOZAIKS_WORKFLOWS_PATH`` / ``MOZAIKS_APP_WORKSPACE_PATH``)
so the result is what a reordered CI shard would see, on both Windows and
Linux environment semantics. These are deliberately heavier than unit tests;
they are the executable form of the order-independence contract.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

_HOST_ENV_KEYS = (
    "PLATFORM_PATH",
    "MOZAIKS_WORKFLOWS_PATH",
    "MOZAIKS_APP_WORKSPACE_PATH",
    "MOZAIKS_FACTORY_APP_PATH",
)

_POLLUTER_TEST = (
    "tests/test_studio_workflow_trigger.py"
    "::test_studio_trigger_endpoint_accepts_refinement_trigger_payload"
)
_DOTTED_PATCH_VICTIM = "tests/test_check_workspace_integrations_tool.py"
_WORKSPACE_GATED_VICTIM = "tests/test_config_consolidation.py"


def _run_pytest(args: list[str]) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k not in _HOST_ENV_KEYS}
    env.setdefault("ENV", "test")
    env.setdefault("AUTH_ENABLED", "false")
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "--no-cov",
            "-p",
            "no:cacheprovider",
            "-q",
            *args,
        ],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=600,
    )


def _summary_line(result: subprocess.CompletedProcess[str]) -> str:
    lines = [line for line in result.stdout.strip().splitlines() if line.strip()]
    return lines[-1] if lines else result.stdout


def test_dotted_path_monkeypatch_survives_studio_import_in_either_order() -> None:
    """The historical polluter/victim pair passes in both file orders."""
    historical = _run_pytest([_DOTTED_PATCH_VICTIM, _POLLUTER_TEST])
    assert historical.returncode == 0, (
        f"historical order failed:\n{historical.stdout}\n{historical.stderr}"
    )

    inverted = _run_pytest([_POLLUTER_TEST, _DOTTED_PATCH_VICTIM])
    assert inverted.returncode == 0, (
        f"inverted order failed (dotted-path monkeypatch broke after a Studio "
        f"host import):\n{inverted.stdout}\n{inverted.stderr}"
    )


def test_dotted_path_monkeypatch_pair_passes_repeatedly_in_inverted_order() -> None:
    """The previously failing order stays green across repeated runs."""
    for attempt in range(2):
        result = _run_pytest([_POLLUTER_TEST, _DOTTED_PATCH_VICTIM])
        assert result.returncode == 0, (
            f"inverted order failed on repeat run {attempt + 1}:\n"
            f"{result.stdout}\n{result.stderr}"
        )


def test_workspace_gated_tests_run_without_a_prior_host_import() -> None:
    """Workspace-gated tests must not skip just because no host was imported.

    On main, ``tests/test_config_consolidation.py`` alone skipped its 30
    workspace-gated tests, but ran all of them whenever any earlier test
    imported ``mozaiksai.hosts.studio``. Resolution is now explicit and
    order-independent, so a run with no prior host import must produce zero
    skips.
    """
    result = _run_pytest([_WORKSPACE_GATED_VICTIM])
    summary = _summary_line(result)
    assert result.returncode == 0, f"victim run failed:\n{result.stdout}\n{result.stderr}"
    assert "skipped" not in summary, (
        f"workspace-gated tests skipped without a prior host import — "
        f"resolution is order-dependent again: {summary}"
    )


def test_workspace_resolution_identical_before_and_after_host_import() -> None:
    """The resolved workspace root must not change when a host import precedes it."""
    probe = (
        "import json, sys\n"
        "sys.path.insert(0, 'tests')\n"
        "from conftest import _resolve_active_app_root\n"
        "before = _resolve_active_app_root()\n"
        "import mozaiksai.hosts.studio  # noqa: F401\n"
        "after = _resolve_active_app_root()\n"
        "print(json.dumps({'before': str(before), 'after': str(after)}))\n"
    )
    env = {k: v for k, v in os.environ.items() if k not in _HOST_ENV_KEYS}
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
        check=True,
    )
    import json

    report = json.loads(result.stdout.strip().splitlines()[-1])
    assert report["before"] == report["after"], report
    assert report["before"].endswith("app"), report
