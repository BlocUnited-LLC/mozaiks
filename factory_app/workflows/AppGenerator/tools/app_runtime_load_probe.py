"""AppLoader diagnostics for one generated bundle inside a disposable Docker worker.

The parent Studio process must never call ``probe_app_root`` on generated files.
Its result helps route repairs; the separate external runtime smoke is required
to establish acceptance.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any


def _package_markers(app_root: Path) -> None:
    """Match the regular-package layout expected by generated service imports."""
    for directory in sorted(app_root.rglob("*"), reverse=True):
        if not directory.is_dir() or directory == app_root:
            continue
        marker = directory / "__init__.py"
        if marker.exists():
            continue
        has_python = any(child.suffix == ".py" for child in directory.iterdir() if child.is_file())
        has_package_child = any(
            (child / "__init__.py").exists() for child in directory.iterdir() if child.is_dir()
        )
        if has_python or has_package_child:
            marker.write_text("", encoding="utf-8")


async def probe_app_root(app_root: Path) -> dict[str, Any]:
    """Return repair diagnostics for a bundle staged in the contained worker."""
    from mozaiksai.core.runtime.app.loader import AppLoader

    details: dict[str, Any] = {
        "app_name": None,
        "module_names": [],
        "failed_module_names": [],
        "page_names": [],
        "workflow_names": [],
        "subscriptions_loaded": False,
    }
    failed_tests: list[dict[str, Any]] = []
    if not app_root.is_dir() or not (app_root / "app.json").is_file():
        failed_tests.append({
            "test": "app_runtime_load",
            "error": "No generated files were available for runtime app loading.",
            "fix_suggestion": "Assemble a complete app bundle with app.json before validation or export.",
        })
    else:
        _package_markers(app_root)
        modules_before = dict(sys.modules)
        try:
            loaded = await AppLoader.load(str(app_root))
            details = {
                "app_name": loaded.definition.name,
                "module_names": [module.name for module in loaded.modules],
                "failed_module_names": list(loaded.failed_module_names),
                "page_names": [page.name for page in loaded.definition.pages],
                "workflow_names": [workflow.name for workflow in loaded.definition.workflows],
                "subscriptions_loaded": loaded.subscriptions_config is not None,
            }
            for module_name in loaded.failed_module_names:
                error = (
                    loaded.module_load_errors.get(module_name) or "AppLoader could not load module."
                ).replace(str(app_root), "app")
                undeclared_event = (
                    error.startswith("module.yaml action ") and " emits undeclared event " in error
                )
                failed_tests.append({
                    "test": "app_runtime_module_load",
                    "module": module_name,
                    "path": (
                        f"modules/{module_name}/contracts/events.yaml" if undeclared_event
                        else f"modules/{module_name}/backend/handler.py"
                    ),
                    "error": error,
                    "fix_suggestion": (
                        "Declare the action's custom event in module_contract.events_yaml under its exact "
                        "domain.-prefixed type, with version, producer, and payload contract, and emit that "
                        "type. Canonical create/update/delete events are declared and emitted by code."
                        if undeclared_event else
                        "Fix the module contract, companion manifests, handler entrypoint, "
                        "or app-owned service imports so AppLoader.load() can load every module."
                    ),
                })
        except Exception as exc:
            failed_tests.append({
                "test": "app_runtime_load",
                "error": f"AppLoader.load() failed: {exc}",
                "fix_suggestion": (
                    "Ensure app.json, modules/*/module.yaml, contracts/*.yaml, "
                    "backend handlers, and app-level service imports are loadable."
                ),
            })
        finally:
            # A Docker worker runs this once. Keep direct trusted-fixture tests
            # isolated as well without removing concurrent workflow imports.
            roots = {str(app_root.resolve()), str(app_root.parent.resolve())}
            sys.path[:] = [entry for entry in sys.path if entry not in roots]
            for key, value in list(sys.modules.items()):
                filename = getattr(value, "__file__", None)
                if isinstance(filename, str) and Path(filename).is_relative_to(app_root):
                    if key in modules_before:
                        sys.modules[key] = modules_before[key]
                    else:
                        sys.modules.pop(key, None)
            for key, value in modules_before.items():
                if key not in sys.modules and (
                    key == "services" or key.startswith(("services.", "mozaiks_runtime_module_"))
                ):
                    sys.modules[key] = value

    passed = not failed_tests
    return {
        "contract_version": "1.0",
        "passed": passed,
        "checks": [{
            "id": "app_runtime_load",
            "passed": passed,
            "message": (
                "Generated app bundle loads through AppLoader."
                if passed else f"{len(failed_tests)} app runtime load issue(s) found."
            ),
            "details": {**details, "failed_test_count": len(failed_tests)},
        }],
        "failed_tests": failed_tests,
        "warnings": [],
        "details": details,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(probe_app_root(args.app_root))
    args.output.write_text(json.dumps(result, sort_keys=True), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
