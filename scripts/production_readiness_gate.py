"""Run the offline Mozaiks production-readiness gate.

This gate is intentionally deterministic by default: it runs contract, generator,
host, and frontend build checks that should not require live LLM calls, browser
drivers, or third-party services.
"""

from __future__ import annotations

import argparse
import os
import re
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
    "tests/test_appgenerator_managed_capability_smoke.py",
    "tests/test_managed_capability_template_expansion.py",
    "tests/test_managed_capability_artifact_replay.py",
    "tests/test_mozaikspay_managed_capability_contract.py",
    "tests/test_production_readiness_gate.py",
    "tests/test_app_loader.py",
    "tests/test_appgenerator_ui_quality_gate.py",
    "tests/test_structured_output_runtime_contracts.py",
    "tests/test_ui_surface_taxonomy.py",
    "tests/test_mobile_surface_contracts.py",
    "tests/test_studio_host_smoke.py",
    # Runtime, platform, and security contracts
    "tests/test_startup_validation.py",
    "tests/test_profile_contract.py",
    "tests/test_module_executor_permission_enforcement.py",
    "tests/test_platform_hook_registry.py",
    "tests/test_offline_generated_build_acceptance.py",
    "tests/test_generated_bundle_scanner.py",
    "tests/test_workflow_declarative_contracts.py",
    "tests/test_workflow_entry_point_config.py",
    "tests/test_factory_workflow_integration_contracts.py",
    "tests/test_security_hardening.py",
    "tests/test_rate_limit_helpers.py",
    "tests/test_rate_limit_middleware.py",
]

QUICK_PYTEST_TARGETS = [
    "tests/test_appgenerator_module_contracts.py",
    "tests/test_appgenerator_save_app_schema.py",
    "tests/test_managed_capability_template_expansion.py",
    "tests/test_managed_capability_artifact_replay.py",
    "tests/test_mozaikspay_managed_capability_contract.py",
    "tests/test_production_readiness_gate.py",
    "tests/test_app_loader.py",
    "tests/test_studio_host_smoke.py",
    "tests/test_startup_validation.py",
    "tests/test_profile_contract.py",
    "tests/test_module_executor_permission_enforcement.py",
    "tests/test_offline_generated_build_acceptance.py",
    "tests/test_security_hardening.py",
    "tests/test_rate_limit_helpers.py",
    "tests/test_rate_limit_middleware.py",
]

SOURCE_HYGIENE_SUFFIXES = {
    ".env",
    ".example",
    ".js",
    ".jsx",
    ".json",
    ".md",
    ".py",
    ".ts",
    ".tsx",
    ".yaml",
    ".yml",
}
SOURCE_HYGIENE_EXCLUDED_DIRS = {
    ".git",
    ".mkdocs-tmp",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "site",
    "venv",
}


def _source_hygiene_pattern(*parts: str, flags: int = 0) -> re.Pattern[str]:
    return re.compile("".join(parts), flags)


SOURCE_HYGIENE_FORBIDDEN = [
    _source_hygiene_pattern(r"\bleg", r"acy\b", flags=re.IGNORECASE),
    _source_hygiene_pattern(r"\bback", r"wards?\b", flags=re.IGNORECASE),
    _source_hygiene_pattern(r"\bdepre", r"cated\b", flags=re.IGNORECASE),
    _source_hygiene_pattern(r"compati", r"bility only", flags=re.IGNORECASE),
    _source_hygiene_pattern("MOZAIKS_WORKFLOW", "_ROOTS"),
    _source_hygiene_pattern("MOZAIKS_WORKSPACE", "_APP_PATH"),
    _source_hygiene_pattern("MOZAIKS_ENABLE", "_ADAPTERS"),
    _source_hygiene_pattern("MOZAIKS_ENABLED", "_ADAPTERS"),
    _source_hygiene_pattern("MOZAIKS_CONTEXT", "_PLACEHOLDERS_FILE"),
    _source_hygiene_pattern("resolve_platform", "_path"),
    _source_hygiene_pattern("resolve_platform", "_root"),
    _source_hygiene_pattern("normalize_workflow", "_roots"),
    _source_hygiene_pattern("workflow_base", "_paths"),
    _source_hygiene_pattern("seed-placeholder", "-apps"),
    _source_hygiene_pattern("placeholder ", "fallback", flags=re.IGNORECASE),
    _source_hygiene_pattern("placeholder ", "pack", flags=re.IGNORECASE),
    _source_hygiene_pattern("placeholder ", "key", flags=re.IGNORECASE),
]
SOURCE_HYGIENE_ALLOWED_SNIPPETS: dict[str, tuple[str, ...]] = {
    "docs/yc-alignment.md": (
        "The next generation will replace legacy SaaS with AI-native software.",
    ),
    # Test files that intentionally reference forbidden terms to verify the system rejects them.
    "tests/test_factory_build_context_contract.py": (
        "prose-oriented legacy fields",
        "deprecated fields",
    ),
    "tests/test_oss_prompt_hygiene.py": (
        "legacy rule that must not render",
    ),
}


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
    env["OPENAI_API_KEY"] = "sk-test-key"
    env["MONGO_URI"] = "mongodb://localhost:27017/test_mozaiks"
    return env


def _source_hygiene_files() -> list[Path]:
    files: list[Path] = []
    for path in REPO_ROOT.rglob("*"):
        if any(part in SOURCE_HYGIENE_EXCLUDED_DIRS for part in path.parts):
            continue
        if not path.is_file():
            continue
        if path.relative_to(REPO_ROOT).as_posix() == "scripts/production_readiness_gate.py":
            continue
        if path.name == ".env.example" or path.suffix in SOURCE_HYGIENE_SUFFIXES:
            files.append(path)
    return files


def run_source_hygiene_scan() -> list[str]:
    violations: list[str] = []
    for path in _source_hygiene_files():
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        relative = path.relative_to(REPO_ROOT).as_posix()
        allowed_snippets = SOURCE_HYGIENE_ALLOWED_SNIPPETS.get(relative, ())
        for line_number, line in enumerate(text.splitlines(), start=1):
            if allowed_snippets and any(snippet in line for snippet in allowed_snippets):
                continue
            for pattern in SOURCE_HYGIENE_FORBIDDEN:
                if pattern.search(line):
                    violations.append(f"{relative}:{line_number}: {line.strip()}")
                    break
    return violations


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
        print("source hygiene scan: internal")
        for step in steps:
            print(f"{step.name}: {' '.join(step.command)}")
        return 0

    violations = run_source_hygiene_scan()
    if violations:
        print("\nSource hygiene scan failed:", file=sys.stderr)
        for violation in violations[:50]:
            print(f"  {violation}", file=sys.stderr)
        if len(violations) > 50:
            print(f"  ... {len(violations) - 50} more", file=sys.stderr)
        return 1

    for step in steps:
        rc = _run_step(step)
        if rc != 0:
            print(f"\nProduction-readiness gate failed at: {step.name}", file=sys.stderr)
            return rc

    print("\nProduction-readiness gate passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
