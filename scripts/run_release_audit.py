"""Pre-release audit script — run this locally before tagging any release.

Chains the following checks:

1. Governance guardrails (source-level)
2. Build wheel + sdist
3. Package content guard (artifact-level)
4. Twine metadata check
5. Smoke-install the wheel into a clean venv
6. Verify Factory resources resolve from the install
7. First run from the install: ``mozaiks init`` -> ``mozaiks serve`` (platform
   and studio hosts) -> ``/api/health/ready`` -> ``/api/shell-config``, in a
   scrubbed environment against a throwaway MongoDB server
8. Offline functional acceptance tests

Returns 0 when all checks pass.  Returns non-zero on the first failure.

Usage::

    docker run --rm -d --name mozaiks-release-audit-mongo -p 127.0.0.1:27018:27017 mongo:7
    python scripts/run_release_audit.py --mongo-uri mongodb://127.0.0.1:27018

Options:
    --skip-build   Re-use an existing dist/ directory instead of rebuilding.
                   Useful when iterating on content guard failures.
    --mongo-uri    URI of a throwaway MongoDB server, on a non-default port, for
                   the first-run smoke (or set MOZAIKS_RELEASE_AUDIT_MONGO_URI).
                   The runtime ignores the database name in the URI and uses
                   fixed database names (mozaiksai, mozaiks_apps) on whatever
                   server it points to, so never point it at the server you
                   develop against. Required unless --skip-first-run-smoke is
                   given.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import venv
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DIST_DIR = REPO_ROOT / "dist"


def _run(args: list[str], *, cwd: Path | None = None, env: dict | None = None, check: bool = True) -> int:
    print(f"\n>>> {' '.join(str(a) for a in args)}")
    result = subprocess.run(args, cwd=cwd or REPO_ROOT, env=env, check=False)
    if check and result.returncode != 0:
        print(f"FAIL (exit {result.returncode})", file=sys.stderr)
        raise SystemExit(result.returncode)
    return result.returncode


def step_governance() -> None:
    print("\n=== 1. Governance guardrails ===")
    _run([sys.executable, "scripts/governance_guardrails.py", "--all", "--errors-only"])


def step_build(skip_build: bool) -> list[Path]:
    print("\n=== 2. Build distributions ===")
    if not skip_build:
        if DIST_DIR.exists():
            shutil.rmtree(DIST_DIR)
        _run([sys.executable, "-m", "build"])
    else:
        print("  --skip-build: re-using existing dist/")

    wheels = sorted(DIST_DIR.glob("*.whl"))
    sdists = sorted(DIST_DIR.glob("*.tar.gz"))
    artifacts = [*wheels, *sdists]
    if not artifacts:
        print("ERROR: no artifacts found in dist/", file=sys.stderr)
        raise SystemExit(1)
    print(f"  artifacts: {[a.name for a in artifacts]}")
    return artifacts


def step_content_guard(artifacts: list[Path]) -> None:
    print("\n=== 3. Package content guard ===")
    _run([sys.executable, "scripts/package_content_guard.py", *[str(a) for a in artifacts]])


def step_twine_check(artifacts: list[Path]) -> None:
    print("\n=== 4. Twine metadata check ===")
    _run([sys.executable, "-m", "twine", "check", *[str(a) for a in artifacts]])


def step_smoke_install(wheels: list[Path]) -> Path:
    print("\n=== 5. Smoke install into clean venv ===")
    venv_dir = Path(tempfile.mkdtemp(prefix="mozaiks-release-audit-"))
    print(f"  venv: {venv_dir}")
    venv.create(str(venv_dir), with_pip=True, clear=True)

    pip = venv_dir / "Scripts" / "pip.exe" if sys.platform == "win32" else venv_dir / "bin" / "pip"
    python = venv_dir / "Scripts" / "python.exe" if sys.platform == "win32" else venv_dir / "bin" / "python"

    # Run from outside the repository so the smoke test cannot import the
    # checkout ahead of the wheel under test via the current working directory.
    _run([str(pip), "install", "--quiet", str(wheels[0])], cwd=venv_dir)

    # CLI sanity check.
    _run([str(python), "-m", "mozaiks", "--version"], cwd=venv_dir)

    return python


def step_verify_resources(python: Path) -> None:
    print("\n=== 6. Verify Factory resources resolve from installed package ===")
    verify_script = """
from pathlib import Path
from mozaiksai.resources import (
    resolve_factory_workflows_root,
    resolve_factory_brand_root,
    resolve_web_shell_root,
    resolve_chat_ui_src_root,
)

checks = {
    "factory_workflows": resolve_factory_workflows_root(),
    "factory_brand": resolve_factory_brand_root(),
    "web_shell": resolve_web_shell_root(),
    "chat_ui_src": resolve_chat_ui_src_root(),
}

for name, path in checks.items():
    assert path is not None, f"{name} did not resolve"
    p = Path(path)
    assert p.exists(), f"{name} path missing: {p}"
    assert "site-packages" in str(p), f"{name} resolved to non-installed path: {p}"
    print(f"  OK {name}: {p}")

print("All Factory resources resolve from site-packages.")
"""
    _run([str(python), "-c", verify_script], cwd=python.parent)


def step_first_run_smoke(python: Path, mongo_uri: str) -> None:
    print("\n=== 7. First run from the installed package (init -> serve -> ready -> shell-config) ===")
    # Run outside the checkout so nothing can import the source tree instead of
    # the wheel under test; the script asserts where every package came from.
    # Keep inherited package paths and credentials out of the smoke process.
    # Pass the URI through its environment so it stays out of command output.
    smoke_env = {
        key: os.environ[key]
        for key in (
            "PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP",
            "TMPDIR", "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA",
            "HOMEDRIVE", "HOMEPATH", "LANG", "LC_ALL",
        )
        if key in os.environ
    }
    smoke_env.update({
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUNBUFFERED": "1",
        "MOZAIKS_FIRST_RUN_SMOKE_MONGO_URI": mongo_uri,
    })
    work_dir = Path(tempfile.mkdtemp(prefix="mozaiks-release-first-run-"))
    _run(
        [
            str(python),
            str(REPO_ROOT / "scripts" / "smoke_installed_first_run.py"),
            "--work-dir",
            str(work_dir),
        ],
        cwd=work_dir,
        env=smoke_env,
    )


def step_offline_acceptance() -> None:
    """Run offline functional acceptance tests against the source tree.

    These tests validate:
    - Package content guard policy (artifact-level)
    - Governance guardrail rules (source-level)
    - Build context / capability pack contract integrity (no LLM required)
    - Module loader, entitlement, and module dispatch contracts
    - Generated app scanner and bundle validation logic

    All tests run against source fixtures, not live LLM APIs.
    A subset of these also runs in CI on every PR.
    """
    print("\n=== 8. Offline functional acceptance tests ===")

    # Core offline test suites — no cloud, no LLM required.
    offline_test_markers = [
        # Distribution hardening
        "tests/test_package_content_guard.py",
        "tests/test_governance_guardrails.py",
        # Build context and capability pack contracts
        "tests/test_build_context_mozaikspay_pack.py",
        # Module loader and action dispatch
        "tests/test_app_loader.py",
        # Entitlement gate
        "tests/test_entitlement_drift.py",
        # Bundle scanner
        "tests/test_generated_bundle_scanner.py",
        # MozaiksPay pack safety
        "tests/test_app_planner_managed_capability_safety.py",
    ]

    # Run only tests that exist (some may not be present in all build states).
    existing = [t for t in offline_test_markers if (REPO_ROOT / t).exists()]
    missing = [t for t in offline_test_markers if t not in existing]
    if missing:
        print(f"  WARNING: {len(missing)} test file(s) not found — skipping:")
        for m in missing:
            print(f"    {m}")

    if not existing:
        print("  ERROR: No offline acceptance test files found.", file=sys.stderr)
        raise SystemExit(1)

    _run(
        [
            sys.executable,
            "-m",
            "pytest",
            *existing,
            "--no-cov",
            "-q",
            "--tb=short",
        ]
    )
    print(f"  Offline acceptance: {len(existing)} test suite(s) passed.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-build", action="store_true", help="re-use existing dist/ directory")
    parser.add_argument(
        "--skip-acceptance",
        action="store_true",
        help="skip offline acceptance tests (not recommended)",
    )
    parser.add_argument(
        "--mongo-uri",
        default=os.environ.get("MOZAIKS_RELEASE_AUDIT_MONGO_URI", ""),
        help=(
            "URI of a throwaway MongoDB server, on a non-default port, for the first-run "
            "smoke (default: $MOZAIKS_RELEASE_AUDIT_MONGO_URI). The runtime ignores the "
            "database name in the URI and uses fixed database names on that server."
        ),
    )
    parser.add_argument(
        "--skip-first-run-smoke",
        action="store_true",
        help="skip starting the installed app (not recommended: the audit then never boots it)",
    )
    args = parser.parse_args(argv)
    if not args.skip_first_run_smoke and not args.mongo_uri:
        parser.error(
            "the first-run smoke needs a throwaway MongoDB server: pass --mongo-uri "
            "(or set MOZAIKS_RELEASE_AUDIT_MONGO_URI), or --skip-first-run-smoke"
        )

    try:
        step_governance()
        artifacts = step_build(args.skip_build)
        wheels = [a for a in artifacts if a.suffix == ".whl"]
        step_content_guard(artifacts)
        step_twine_check(artifacts)
        python = step_smoke_install(wheels)
        step_verify_resources(python)
        if args.skip_first_run_smoke:
            print("\n=== 7. First-run smoke SKIPPED (--skip-first-run-smoke): the installed app was never started ===")
        else:
            step_first_run_smoke(python, args.mongo_uri)
        if not args.skip_acceptance:
            step_offline_acceptance()
    except SystemExit as exc:
        code = int(exc.code) if exc.code is not None else 1
        print(f"\nRelease audit FAILED (exit {code}).", file=sys.stderr)
        return code

    print("\n=== Release audit checks PASSED. Complete the release checklist before tagging. ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
