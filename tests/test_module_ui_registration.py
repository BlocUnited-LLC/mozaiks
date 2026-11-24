"""Bundle validation resolves module-owned pages the way the web shell registers them.

``web_shell/moduleUi.js`` finds ``modules/<module_id>/ui/index.js`` (or ``.jsx``)
of every module with a manifest; ``web_shell/moduleUiRegistration.js`` registers
each exported component as ``<module_id>:<Export>`` and as ``<Export>``, after
the app's own barrel.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

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
    ui_import_error,
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


STAR_BOTH = "export * from './a.jsx';\nexport * from './b.jsx';\n"
LEFT = "export function StockPage() { return null; }\n"
RIGHT = "export const StockPage = () => null;\n"


def _star_files(barrel: str = STAR_BOTH, **sources: str) -> dict[str, str]:
    return _files(**{INDEX: barrel, **{f"modules/stock/ui/{name}.jsx": text for name, text in sources.items()}})


# Each case is (files, whether the module namespace exports StockPage).
STAR_CASES = {
    "two-bindings": (_star_files(a=LEFT, b=RIGHT), False),
    "nested-two-bindings": (_star_files(a="export * from './c.jsx';\n", b="export * from './d.jsx';\n", c=LEFT, d=RIGHT), False),
    "ambiguity-inside-one-source": (
        _star_files("export * from './a.jsx';\n", a="export * from './c.jsx';\nexport * from './d.jsx';\n", c=LEFT, d=RIGHT), False,
    ),
    "a-value-and-a-component": (_star_files(a=LEFT, b="export const StockPage = 'title';\n"), False),
    "two-packages": (
        _star_files(a="export { StockPage } from 'one';\n", b="export { StockPage } from 'two';\n"), False,
    ),
    "same-file-different-binding": (
        _star_files(
            a="export { One as StockPage } from './c.jsx';\n", b="export { Two as StockPage } from './c.jsx';\n",
            c="export const One = () => null;\nexport const Two = () => null;\n",
        ), False,
    ),
    "shadowed-by-a-value": (_star_files(STAR_BOTH + "export const StockPage = 'title';\n", a=LEFT, b=LEFT), False),
    "same-binding": (
        _star_files(a="export { StockPage } from './c.jsx';\n", b="export * from './c.jsx';\n", c=LEFT), True,
    ),
    "same-binding-under-two-names": (
        _star_files(
            a="export { Page as StockPage } from './c.jsx';\n", b="export { StockPage } from './c.jsx';\n",
            c="function Page() { return null; }\nexport { Page, Page as StockPage };\n",
        ), True,
    ),
    "same-binding-through-an-import": (
        _star_files(
            a="import { StockPage } from './c.jsx';\nexport { StockPage };\n", b="export { StockPage } from './c.jsx';\n", c=LEFT,
        ), True,
    ),
    "same-package-binding": (
        _star_files(a="export { StockPage } from 'one';\n", b="export { StockPage } from 'one';\n"), True,
    ),
    "resolved-by-a-named-export": (_star_files(STAR_BOTH + "export { StockPage } from './a.jsx';\n", a=LEFT, b=RIGHT), True),
    "resolved-by-a-local-export": (_star_files(STAR_BOTH + "export const StockPage = () => null;\n", a=LEFT, b=RIGHT), True),
    "one-source-only": (_star_files(a=LEFT, b="export const Other = () => null;\n"), True),
    "default-is-not-forwarded": (_star_files(a="export default function StockPage() { return null; }\n", b="export const Other = 1;\n"), False),
    "cycle": (_star_files(a="export * from './index.js';\n" + LEFT, b="export { StockPage } from './index.js';\n"), True),
}


@pytest.mark.parametrize("case", STAR_CASES)
def test_star_exports_follow_ecmascript_resolution(case: str) -> None:
    files, exported = STAR_CASES[case]
    components, errors = module_ui_components(files)
    assert ("StockPage" in components.get("stock", {})) is exported, (components, errors)
    assert _verdicts(files) == _all(exported)


@pytest.mark.skipif(shutil.which("node") is None, reason="node is required")
def test_star_export_cases_match_what_node_links(tmp_path) -> None:
    """Every case above without a package import agrees with a real ECMAScript loader."""
    for case, (files, exported) in STAR_CASES.items():
        if any("'one'" in text for text in files.values()):
            continue
        root = tmp_path / case
        for path, text in files.items():
            if path.startswith("modules/stock/ui/"):
                target = root / path.removeprefix("modules/stock/ui/").replace(".jsx", ".mjs").replace(".js", ".mjs")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text.replace(".jsx'", ".mjs'").replace(".js'", ".mjs'"), encoding="utf-8")
        script = "import('./index.mjs').then(m => console.log(typeof m.StockPage === 'function'))"
        result = subprocess.run(["node", "-e", script], cwd=root, capture_output=True, text=True, check=False)
        assert result.stdout.strip() == str(exported).lower(), (case, result.stdout, result.stderr)


@pytest.mark.parametrize(
    "barrel",
    [
        "export { Other as Extra } from './StockPage.jsx';\n",
        "import { Other } from './StockPage.jsx';\nexport { Other };\n",
        "export { Undeclared };\n",
        "export * from './extra.jsx';\n",
    ],
    ids=["re-export", "imported", "undeclared", "behind-a-star"],
)
def test_an_export_its_source_does_not_provide_fails_the_whole_barrel(barrel: str) -> None:
    # The module does not link, so every export of the barrel is gone, not only the missing one.
    files = _files(**{INDEX: BARREL + barrel, "modules/stock/ui/extra.jsx": "export { Gone } from './StockPage.jsx';\n"})
    components, errors = module_ui_components(files)
    assert "stock" not in components and "stock" in errors
    assert _verdicts(files) == _all(False)


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


PAGE_PATH = "modules/stock/ui/StockPage.jsx"


def _import_error(page: str, **sources: str) -> str | None:
    """Why the shell could not load a module whose page starts with ``page``."""
    files = _files(**{PAGE_PATH: page + PAGE, **{f"modules/stock/ui/{name}": text for name, text in sources.items()}})
    present = {*files, "modules/stock/ui/logo.png", "modules/stock/ui/icons/box.svg"}
    _components, errors = module_ui_components(files, present.__contains__)
    assert errors.get("stock", "").removeprefix(f"{INDEX}: ") == (ui_import_error(INDEX, files, present.__contains__) or "")
    return errors.get("stock")


@pytest.mark.parametrize(
    ("page", "sources"),
    [
        ("import Gone from './Gone.jsx';\n", {}),
        ("import './gone.css';\n", {}),
        ("import logo from './gone.png';\n", {}),
        ("import raw from './gone.yaml?raw';\n", {}),
        ("import * as all from '../lib/gone';\n", {}),
        ("const Lazy = React.lazy(() => import('./Gone.jsx'));\n", {}),
        ("async function load() { if (ready) { await import('./Gone.jsx'); } }\n", {}),
        ("import { Missing } from './parts.js';\n", {"parts.js": "export const Part = 1;\n"}),
        ("import parts from './parts.js';\n", {"parts.js": "export const Part = 1;\n"}),
        ("import { Both } from './parts.js';\n", {
            "parts.js": "export * from './a.js';\nexport * from './b.js';\n",
            "a.js": "export const Both = 1;\n", "b.js": "export const Both = 2;\n",
        }),
        ("import './deep.js';\n", {"deep.js": "import './deeper.js';\n", "deeper.js": "import './Gone.js';\n"}),
        ("import './panel';\n", {"panel/other.js": ""}),
        ("export { helper } from './Gone.js';\n", {}),
    ],
    ids=[
        "file", "stylesheet", "asset", "raw-asset", "namespace", "lazy", "nested-dynamic", "missing-name", "missing-default",
        "ambiguous-name", "transitive", "directory-without-index", "re-export-from-a-page",
    ],
)
def test_an_import_the_shell_cannot_load_fails_the_barrel(page: str, sources: dict[str, str]) -> None:
    assert _import_error(page, **sources) is not None


@pytest.mark.parametrize(
    ("page", "sources"),
    [
        ("import React, { useState } from 'react';\nimport { Panel } from '@mozaiks/chat-ui/ui';\nimport x from '@mozaiks/gone';\n", {}),
        ("import logo from './logo.png';\nimport box from './icons/box.svg?url';\n", {}),
        ("import { Part, default as Whole } from './parts';\n", {"parts.jsx": "export const Part = 1;\nexport default Part;\n"}),
        ("import { Typed } from './typed';\n", {"typed.ts": "export enum Typed { A }\n"}),
        ("import panel from './panel';\n", {"panel/index.jsx": "export default 1;\n"}),
        ("import data from './data.json';\n", {"data.json": "{}"}),
        ("import { anything } from './bundle.js';\n", {"bundle.js": "export * from 'a-package';\n"}),
        ("import { old } from './common.js';\n", {"common.js": "module.exports = { old: 1 };\n"}),
        ("import { Odd } from './odd.js';\n", {"odd.js": "export const Odd = @decorated class {};;;(\n"}),
        ("import './loop.js';\n", {"loop.js": "import { StockPage } from './index.js';\nexport const use = () => StockPage;\n"}),
        ("const load = (name) => import(`./${name}.jsx`);\n", {}),
        ("const later = () => import(/* @vite-ignore */ './Gone.jsx');\n", {}),
        ("// import Gone from './Gone.jsx';\nconst text = \"import './Gone.jsx'\";\n", {}),
        ("import { value } from './nested.js';\n", {"nested.js": "const { a: { value } } = { a: { value: 1 } };\nexport { value };\n"}),
    ],
    ids=[
        "packages-and-aliases", "assets", "extensionless", "typescript", "directory-index", "json", "package-star", "commonjs",
        "unreadable", "cycle", "computed-dynamic", "vite-ignore", "not-code", "destructured",
    ],
)
def test_an_import_the_shell_loads_does_not_fail_the_barrel(page: str, sources: dict[str, str]) -> None:
    assert _import_error(page, **sources) is None


def test_a_partial_file_set_judges_only_re_exports() -> None:
    # Generation validates bundles before every shared file exists and without binary assets.
    files = _files(**{PAGE_PATH: "import api from '../../../ui/lib/moduleApi.js';\nimport logo from './logo.png';\n" + PAGE})
    assert _verdicts(files) == _all(True)


def test_the_shells_own_sources_import_what_exists() -> None:
    # A large hand-written code base, so a rule that misreads real JavaScript shows up here.
    root = Path(__file__).parents[1] / "chat-ui/src"
    files = {
        path.relative_to(root).as_posix(): path.read_text(encoding="utf-8")
        for path in root.rglob("*") if path.suffix in {".js", ".jsx"}
    }
    assert len(files) > 100

    def exists(relative: str) -> bool:
        return (root / relative).is_file()

    assert {path: error for path in files if (error := ui_import_error(path, files, exists))} == {}
