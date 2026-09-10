"""App contract acceptance for authored workspaces and installed UI composition."""

from __future__ import annotations

import json
from pathlib import Path

from .functional_generated_app import (
    FunctionalGeneratedAppDiagnostic,
    _registered_components,
    scan_functional_generated_app,
)


def _source_files(root: Path) -> dict[str, str]:
    extensions = {".json", ".yaml", ".yml", ".py", ".js", ".jsx", ".ts", ".tsx"}
    return {
        path.relative_to(root).as_posix(): path.read_text(encoding="utf-8")
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.suffix in extensions
        and not any(part.startswith(".") or part == "__pycache__" for part in path.relative_to(root).parts)
    }


def validate_app_workspace(
    app_root: Path,
    *,
    inherited_ui_roots: tuple[Path, ...] = (),
) -> list[FunctionalGeneratedAppDiagnostic]:
    """Check app declarations and static action/route references using shared gates.

    ``app_root`` is the directory containing app.json. Inherited UI roots must
    be the actual registered UI packages in the host's composition. This checks
    contract closure, not provider implementation completeness or deployment
    readiness. Generated output additionally uses validate_generated_app_bundle.
    """
    from factory_app.workflows.AppGenerator.tools.generated_bundle_scanner import scan_app_contracts

    files = _source_files(Path(app_root))
    diagnostics: list[FunctionalGeneratedAppDiagnostic] = []
    try:
        manifest = json.loads(files["app.json"])
        name = manifest.get("appName") if isinstance(manifest, dict) else None
        if not isinstance(name, str) or not name.strip():
            raise ValueError("appName must be a nonempty string")
    except (KeyError, ValueError):
        diagnostics.append(FunctionalGeneratedAppDiagnostic(
            code="INVALID_APP_MANIFEST", message="app.json must declare a nonempty string appName", path="app.json",
        ))
    inherited: set[str] = set()
    for root in inherited_ui_roots:
        if not root.is_dir():
            raise ValueError("Inherited UI root does not exist")
        inherited.update(_registered_components(_source_files(root)))
    diagnostics.extend(
        FunctionalGeneratedAppDiagnostic(code="APP_CONTRACT_INVALID", message=message)
        for message in scan_app_contracts(files)
    )
    diagnostics.extend(scan_functional_generated_app(
        files, inherited_components=frozenset(inherited), check_placeholders=False,
    ))
    return diagnostics
