"""Bundle validation resolves module-owned pages the way the web shell registers them.

``web_shell/moduleUi.js`` finds ``modules/<module_id>/ui/index.js`` (or ``.jsx``)
of every module with a manifest; ``web_shell/moduleUiRegistration.js`` registers
each exported component as ``<module_id>:<Export>`` and as ``<Export>``, after
the app's own barrel.
"""

from __future__ import annotations

import json

import pytest

from factory_app.workflows._shared.generated_ui_contract import (
    audit_app_ui_bundle_integrity,
)
from factory_app.workflows.AppGenerator.tools import generated_bundle_scanner
from mozaiksai.core.runtime.app.layout_validation import (
    LayoutClassificationStatus,
    classify_layout_path,
)
from mozaiksai.core.validation import functional_generated_app
from mozaiksai.core.workflow.generator_support.ui_registry_composition import (
    compose_ui_registry,
    module_ui_components,
    module_ui_registrations,
)

PAGE = "export default function StockPage() { return null; }\n"
BARREL = "export { default as StockPage } from './StockPage.jsx';\n"
INDEX = "modules/stock/ui/index.js"


def _files(component: str = "StockPage", **changes: str | None) -> dict[str, str]:
    files: dict[str, str | None] = {
        "ui/route_manifest.json": json.dumps({"pages": [{"id": "stock", "path": "/stock", "component": component}]}),
        "modules/stock/module.yaml": "module:\n  id: stock\n",
        INDEX: BARREL,
        "modules/stock/ui/StockPage.jsx": PAGE,
    }
    files.update(changes)
    return {path: content for path, content in files.items() if content is not None}


def _verdicts(files: dict[str, str]) -> dict[str, bool]:
    """Whether each consumer of route components accepts the bundle's routes."""
    diagnostics: list = []
    functional_generated_app._validate_routes(files, diagnostics)
    roots = [path for path in ("ui/index.js", "ui/route_manifest.json") if path in files]
    try:
        compose_ui_registry(
            [{"owner": "app", "files": [{"filename": path, "content": files[path]} for path in roots]}], files,
        )
        composed = True
    except ValueError:
        composed = False
    return {
        "scanner": not generated_bundle_scanner._scan_route_manifest_component_files(files),
        "audit": not audit_app_ui_bundle_integrity([{"filename": path, "content": text} for path, text in files.items()]),
        "functional": not diagnostics,
        "composer": composed,
    }


def _all(value: bool) -> dict[str, bool]:
    return dict.fromkeys(("scanner", "audit", "functional", "composer"), value)


@pytest.mark.parametrize("component", ["StockPage", "stock:StockPage"])
def test_a_route_may_name_a_component_a_module_barrel_exports(component: str) -> None:
    assert _verdicts(_files(component)) == _all(True)


@pytest.mark.parametrize(
    "barrel",
    [
        "import StockPage from './StockPage.jsx';\nexport { StockPage };\n",
        "export * from './pages.jsx';\n",
        "export function StockPage() { return null; }\n",
        "export const StockPage = () => null;\n",
        "import { memo } from 'react';\nimport Page from './StockPage';\nexport const StockPage = memo(Page);\n",
    ],
    ids=["imported", "star", "function", "const", "wrapped"],
)
def test_every_static_export_form_the_shell_accepts_resolves(barrel: str) -> None:
    files = _files(**{INDEX: barrel, "modules/stock/ui/pages.jsx": BARREL})
    assert _verdicts(files) == _all(True)


def test_a_jsx_barrel_resolves() -> None:
    assert _verdicts(_files(**{INDEX: None, "modules/stock/ui/index.jsx": BARREL})) == _all(True)


@pytest.mark.parametrize(
    "changes",
    [
        {INDEX: None},
        {"modules/stock/module.yaml": None},
        {INDEX: "export { default as OtherPage } from './StockPage.jsx';\n"},
        {INDEX: "export { default } from './StockPage.jsx';\n"},
        {INDEX: "// export { default as StockPage } from './StockPage.jsx';\n"},
        {INDEX: "export const StockPage = 'not a component';\n"},
        {INDEX: "export { Missing as StockPage } from './StockPage.jsx';\n"},
        {"modules/stock/ui/StockPage.jsx": None},
        {"modules/stock/ui/index.jsx": BARREL},
    ],
    ids=[
        "no-barrel", "no-manifest", "other-export", "default-only", "commented-out", "string-export",
        "missing-source-export", "missing-source-file", "two-barrels",
    ],
)
def test_a_component_no_module_barrel_exports_stays_unresolved(changes: dict[str, str | None]) -> None:
    assert _verdicts(_files(**changes)) == _all(False)


def test_a_module_barrel_cannot_supply_shell_owned_names() -> None:
    components, errors = module_ui_components(_files(**{
        INDEX: "export const ChatPage = () => null;\nexport const LoginPage = () => null;\n",
    }))
    assert errors == {}
    assert set(module_ui_registrations(components)) == {"stock:ChatPage", "stock:LoginPage"}


def test_an_ambiguous_plain_name_is_refused_unless_the_app_owns_it() -> None:
    other = {
        "modules/depot/module.yaml": "module:\n  id: depot\n",
        "modules/depot/ui/index.js": "export const StockPage = () => null;\n",
    }
    files = _files(**other)
    assert _verdicts(files) == _all(False)
    [error] = generated_bundle_scanner._scan_route_manifest_component_files(files)
    assert "exported by modules ['depot', 'stock']" in error
    assert _verdicts(_files("stock:StockPage", **other)) == _all(True)
    files["ui/index.js"] = (
        "import StockPage from './pages/custom/stock.jsx';\n\n"
        "export function register(registerComponent) {\n  registerComponent('StockPage', StockPage);\n}\n"
    )
    files["ui/pages/custom/stock.jsx"] = PAGE
    assert _verdicts(files) == _all(True)


@pytest.mark.parametrize(
    ("path", "registered"),
    [
        ("modules/stock/ui/index.js", True),
        ("modules/stock/ui/index.jsx", True),
        ("modules/stock/ui/StockPage.jsx", True),
        ("modules/stock/ui/StockPage.tsx", False),
        ("modules/stock/ui/helpers.js", False),
        ("modules/stock/ui/nested/deep/StockPage.jsx", False),
        ("ui/StockPage.jsx", False),
    ],
)
def test_module_page_sources_are_canonical_app_paths(path: str, registered: bool) -> None:
    status = classify_layout_path(path).status
    assert (status is LayoutClassificationStatus.REGISTERED) is registered
