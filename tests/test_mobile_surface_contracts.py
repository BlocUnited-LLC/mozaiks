from __future__ import annotations

import json
from pathlib import Path


def _workspace() -> Path:
    return Path(__file__).resolve().parents[1]


def _read(relative_path: str) -> str:
    return (_workspace() / relative_path).read_text(encoding="utf-8")


def test_data_table_uses_mobile_card_layout() -> None:
    source = _read("chat-ui/src/ui/primitives/DataTable.jsx")

    assert "function MobileRowCard(" in source
    assert 'space-y-3 p-4 md:hidden' in source
    assert 'hidden overflow-x-auto md:block' in source
    assert 'actionAlign="start"' in source


def test_summary_strip_compacts_on_mobile() -> None:
    source = _read("chat-ui/src/ui/primitives/SummaryStrip.jsx")

    assert 'grid grid-cols-2 gap-px bg-border/40 md:grid-cols-4' in source
    assert 'text-[1.45rem]' in source


def test_shell_header_and_widget_stay_mobile_tolerant() -> None:
    header_source = _read("chat-ui/src/components/layout/Header.js")
    layout_source = _read("chat-ui/src/workspace/WorkspaceLayout.jsx")
    widget_source = _read("chat-ui/src/components/chat/PersistentChatWidget.jsx")

    assert 'hidden md:flex items-center gap-2' in header_source
    assert 'md:hidden inline-flex items-center justify-center' not in header_source
    assert 'h-6 max-w-[10.5rem] object-contain opacity-90 sm:h-7 sm:max-w-[12rem]' in header_source

    assert 'pb-24 md:pb-10 lg:pb-0' in layout_source
    assert "function WorkspaceMobileNavTrigger" in layout_source
    assert "<AdminTopbar" not in layout_source
    assert "Open console navigation" in layout_source
    assert "Console navigation" in layout_source
    assert "bottom-[calc(env(safe-area-inset-bottom,0px)+5.5rem)]" in layout_source
    assert "max-h-[82dvh]" in layout_source
    assert "top-24 w-[min" not in layout_source

    assert 'h-12 w-12' in widget_source
    assert 'sm:h-20 sm:w-20' in widget_source


def test_dialog_and_overlay_primitives_use_mobile_sheet_layout() -> None:
    dialog_source = _read("chat-ui/src/ui/base/components/dialog.jsx")
    transition_source = _read("chat-ui/src/ui/screens/TransitionOverlayFrame.jsx")
    surface_source = _read("chat-ui/src/ui/primitives/Surface.jsx")

    assert 'fixed inset-x-0 bottom-0 z-50 grid w-full' in dialog_source
    assert 'rounded-t-[1.75rem] border-b-0' in dialog_source
    assert 'sm:left-[50%] sm:top-[50%]' in dialog_source

    assert 'fixed inset-x-0 bottom-0 z-50 w-full' in transition_source
    assert 'sm:left-1/2 sm:top-1/2' in transition_source

    assert 'items-end justify-center' in surface_source
    assert 'rounded-t-[2rem]' in surface_source


def test_modal_and_form_actions_stack_for_mobile() -> None:
    modal_source = _read("chat-ui/src/ui/primitives/Modal.jsx")
    form_source = _read("chat-ui/src/ui/primitives/Form.jsx")

    assert "DialogContent size={size} className={cn('gap-0 p-0', className)}" in modal_source
    assert 'gap-2 border-t border-border/60 bg-background/80 px-4 py-4 backdrop-blur-sm sm:px-6' in modal_source
    assert 'className="w-full sm:w-auto"' in modal_source

    assert 'min-h-[120px]' in form_source
    assert "className={cn(baseClass, 'h-11 rounded-[var(--shell-control-radius,1rem)] px-4')}" in form_source
    assert 'mt-6 flex flex-col-reverse gap-3 border-t border-border/60 pt-4 sm:flex-row sm:items-center sm:justify-end' in form_source


def test_web_shell_has_responsive_smoke_harness() -> None:
    package_json = json.loads(_read("web_shell/package.json"))
    smoke_source = _read("web_shell/playwright/apps.responsive.smoke.spec.js")
    ci_source = _read(".github/workflows/ci.yml")

    assert "test:responsive-smoke" in package_json["scripts"]
    assert "playwright:install" in package_json["scripts"]
    assert "@playwright/test" in package_json["devDependencies"]
    assert (_workspace() / "web_shell" / "playwright.responsive.config.js").exists()
    assert (_workspace() / "web_shell" / "playwright" / "apps.responsive.smoke.spec.js").exists()
    assert "workspace usage route stays responsive across desktop and mobile widths" in smoke_source
    assert "workspace health route stays responsive across desktop and mobile widths" in smoke_source
    assert "workspace billing route stays responsive across desktop and mobile widths" in smoke_source
    assert "workspace hosting route stays responsive across desktop and mobile widths" in smoke_source
    assert "app console root redirects to overview" in smoke_source
    assert "app overview route stays responsive across desktop and mobile widths" in smoke_source
    assert "app health route stays responsive across desktop and mobile widths" in smoke_source
    assert "app integrations route stays responsive across desktop and mobile widths" in smoke_source
    assert "app usage route stays responsive across desktop and mobile widths" in smoke_source
    assert "app billing route stays responsive across desktop and mobile widths" in smoke_source
    assert "app users route stays responsive across desktop and mobile widths" in smoke_source
    assert "app hosting route stays responsive across desktop and mobile widths" in smoke_source
    assert "mobile app console navigation keeps route transitions stable" in smoke_source
    assert "mobile workspace console navigation keeps route transitions stable" in smoke_source
    assert "/api/studio/overview" in smoke_source
    assert "npx playwright install --with-deps chromium" in ci_source
    assert "npm run test:responsive-smoke" in ci_source


def test_factory_app_console_routes_are_all_covered_by_smoke() -> None:
    manifest = json.loads(_read("factory_app/app/ui/route_manifest.json"))
    smoke_source = _read("web_shell/playwright/apps.responsive.smoke.spec.js")
    console_components = {
        path.stem
        for path in (_workspace() / "factory_app" / "app" / "ui" / "pages" / "custom" / "console").glob("*.jsx")
    }

    smoke_titles_by_component = {
        "AppsPage": "apps route stays responsive across desktop and mobile widths",
        "WorkspaceUsagePage": "workspace usage route stays responsive across desktop and mobile widths",
        "WorkspaceHealthPage": "workspace health route stays responsive across desktop and mobile widths",
        "WorkspaceBillingPage": "workspace billing route stays responsive across desktop and mobile widths",
        "WorkspaceHostingPage": "workspace hosting route stays responsive across desktop and mobile widths",
        "ConsolePage": "app console root redirects to overview",
        "AppOverviewPage": "app overview route stays responsive across desktop and mobile widths",
        "AppHealthPage": "app health route stays responsive across desktop and mobile widths",
        "AppUsersPage": "app users route stays responsive across desktop and mobile widths",
        "AppUsagePage": "app usage route stays responsive across desktop and mobile widths",
        "AppBillingPage": "app billing route stays responsive across desktop and mobile widths",
        "AppHostingPage": "app hosting route stays responsive across desktop and mobile widths",
        "AppIntegrationsPage": "app integrations route stays responsive across desktop and mobile widths",
    }
    route_components = {
        page["component"]
        for page in manifest["pages"]
        if page.get("component") and page["component"] != "AdminPortal"
    }

    assert route_components == set(smoke_titles_by_component)
    for title in smoke_titles_by_component.values():
        assert title in smoke_source

    assert console_components == route_components | {"AppConsoleChrome", "CreateAppRedirectPage", "RefinementControls"}


def test_factory_app_react_files_are_classified() -> None:
    manifest = json.loads(_read("factory_app/app/ui/route_manifest.json"))
    react_files = {
        path.relative_to(_workspace()).as_posix()
        for path in (_workspace() / "factory_app").rglob("*.jsx")
    }
    route_backed_files = {
        f"factory_app/app/ui/pages/custom/console/{page['component']}.jsx"
        for page in manifest["pages"]
        if page.get("component") and page["component"] != "AdminPortal"
    }
    support_files = {
        "factory_app/app/ui/pages/custom/console/AppConsoleChrome.jsx",
        "factory_app/app/ui/pages/custom/console/CreateAppRedirectPage.jsx",
        "factory_app/app/ui/pages/custom/console/RefinementControls.jsx",
        "factory_app/app/ui/components/ConsoleShared.jsx",
        "factory_app/app/ui/components/HarnessDecisionCard.jsx",
        "factory_app/workflows/ExistingAppDiscovery/ui/DiscoveryBriefCard.jsx",
    }

    assert react_files == route_backed_files | support_files
