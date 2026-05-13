"""Run the offline Mozaiks production-readiness gate.

This gate is intentionally deterministic by default: it runs contract, generator,
host, and frontend build checks that should not require live LLM calls, browser
drivers, or third-party services.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]

PYTEST_GATE_TARGETS = [
    "tests/test_appgenerator_canonical_generation.py",
    "tests/test_appgenerator_module_contracts.py",
    "tests/test_appgenerator_save_app_schema.py",
    "tests/test_appgenerator_hosted_pack_smoke.py",
    "tests/test_hosted_pack_template_expansion.py",
    "tests/test_mozaikspay_hosted_pack_contract.py",
    "tests/test_production_readiness_gate.py",
    "tests/test_appgenerator_ui_quality_gate.py",
    "tests/test_structured_output_runtime_contracts.py",
    "tests/test_ui_surface_taxonomy.py",
    "tests/test_mobile_surface_contracts.py",
    "tests/test_mozaiks_host_smoke.py",
]

QUICK_PYTEST_TARGETS = [
    "tests/test_appgenerator_module_contracts.py",
    "tests/test_appgenerator_save_app_schema.py",
    "tests/test_hosted_pack_template_expansion.py",
    "tests/test_mozaikspay_hosted_pack_contract.py",
    "tests/test_production_readiness_gate.py",
    "tests/test_mozaiks_host_smoke.py",
]


@dataclass(frozen=True)
class GateStep:
    name: str
    command: list[str]
    env: dict[str, str] | None = None


def _base_env() -> dict[str, str]:
    env = os.environ.copy()
    env["ENV"] = "test"
    env["AUTH_ENABLED"] = "false"
    env["RATE_LIMIT_ENABLED"] = "false"
    env["OPENAI_API_KEY"] = "sk-test-placeholder"
    env["MONGO_URI"] = "mongodb://localhost:27017/test_mozaiks"
    return env


def _build_steps(args: argparse.Namespace) -> list[GateStep]:
    pytest_targets = QUICK_PYTEST_TARGETS if args.quick else PYTEST_GATE_TARGETS
    pytest_command = [sys.executable, "-m", "pytest", "-q", *pytest_targets]

    steps = [
        GateStep(
            name="offline contract and smoke tests",
            command=pytest_command,
            env=_base_env(),
        )
    ]

    if not args.skip_frontend:
        npm = shutil.which("npm.cmd") or shutil.which("npm")
        if not npm:
            raise RuntimeError("npm was not found on PATH; install Node.js or use --skip-frontend.")
        steps.append(
            GateStep(
                name="web shell production build",
                command=[npm, "--prefix", "web_shell", "run", "build"],
                env={
                    **_base_env(),
                    "VITE_MOCK_MODE": "true",
                    "MOZAIKS_FACTORY_APP_PATH": str(REPO_ROOT / "factory_app"),
                    "MOZAIKS_CHAT_UI_PATH": str(REPO_ROOT / "chat-ui"),
                },
            )
        )

    return steps


def _run_step(step: GateStep) -> int:
    print(f"\n==> {step.name}")
    print("    " + " ".join(step.command))
    result = subprocess.run(
        step.command,
        cwd=REPO_ROOT,
        env=step.env,
        text=True,
    )
    return int(result.returncode)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the offline Mozaiks production-readiness gate.")
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Run the smaller local gate subset for fast iteration.",
    )
    parser.add_argument(
        "--skip-frontend",
        action="store_true",
        help="Skip the web_shell production build.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="Print the commands that would run and exit.",
    )
    args = parser.parse_args(argv)

    steps = _build_steps(args)
    if args.list:
        for step in steps:
            print(f"{step.name}: {' '.join(step.command)}")
        return 0

    for step in steps:
        rc = _run_step(step)
        if rc != 0:
            print(f"\nProduction-readiness gate failed at: {step.name}", file=sys.stderr)
            return rc

    print("\nProduction-readiness gate passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
