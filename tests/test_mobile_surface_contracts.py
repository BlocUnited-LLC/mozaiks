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


def test_data_table_uses_stable_empty_array_defaults() -> None:
    source = _read("chat-ui/src/ui/primitives/DataTable.jsx")

    assert "const EMPTY_ARRAY = Object.freeze([]);" in source
    assert "columns = EMPTY_ARRAY" in source
    assert "data: initialData = EMPTY_ARRAY" in source
    assert "actions = EMPTY_ARRAY" in source


def test_summary_strip_compacts_on_mobile() -> None:
    source = _read("chat-ui/src/ui/primitives/SummaryStrip.jsx")

    assert 'grid grid-cols-2 gap-px bg-border/35 md:grid-cols-4' in source
    assert 'min-h-[5.75rem]' in source
    assert 'text-xl font-semibold' in source


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
    assert "Open Studio navigation" in layout_source
    assert "Studio navigation" in layout_source
    assert "bottom-[calc(env(safe-area-inset-bottom,0px)+5.5rem)]" in layout_source
    assert "max-h-[82dvh]" in layout_source
    assert "top-24 w-[min" not in layout_source

    assert 'fixed right-0 bottom-6 z-50 widget-safe-bottom' in widget_source
    assert 'rounded-l-2xl border border-r-0 border-border/50' in widget_source
    assert 'w-[26rem] max-w-[calc(100vw-2.5rem)] h-[50vh] md:h-[70vh] min-h-[360px]' in widget_source


def test_support_escalation_uses_profile_support_tab() -> None:
    widget_source = _read("chat-ui/src/components/chat/PersistentChatWidget.jsx")
    chat_page_source = _read("chat-ui/src/pages/ChatPage.js")
    support_links_source = _read("chat-ui/src/utils/supportLinks.js")
    profile_source = _read("chat-ui/src/pages/ProfilePage.jsx")
    profile_panel_source = _read("factory_app/app/admin/pages/UserSupportPanel.jsx")
    platform_source = _read("mozaiksai/hosts/platform.py")

    assert "buildUserSupportPath" in widget_source
    assert "buildUserSupportPath" in chat_page_source
    assert "SUPPORT_PROFILE_TAB_ID = 'support-tickets'" in support_links_source
    assert "return `/me?${params.toString()}`;" in support_links_source
    assert "buildSupportRequestPayload" in widget_source
    assert "buildSupportRequestPayload" in chat_page_source
    assert "supportError" in widget_source
    assert "page_title:" not in widget_source
    assert "page_url:" not in widget_source
    assert "subjectParams.set('username', username)" in profile_source
    assert "const subjectSuffix = subjectParams.toString() ? `?${subjectParams}` : '';" in profile_source
    assert "fetchWithAuth(`${backendUrl}/api/me/profile-pages${subjectSuffix}`, {}, auth)" in profile_source
    assert "fetchWithAuth(`${backendUrl}/api/me/profile-panels" not in profile_source
    assert "fetchWithAuth(`${backendUrl}/api/me/profile-tabs" not in profile_source
    assert "supportTrace('support_request:create:start'" in widget_source
    assert "supportTrace('support_thread:open'" in widget_source
    assert "supportPanelTrace('data:received'" in profile_panel_source
    assert "resolved_app_id, viewer_user_id = _resolve_profile_scope(principal, app_id=None)" in platform_source
    assert "requested_subject_user_id = user_id" in platform_source
    assert "action_params = _profile_action_params(" in platform_source
    assert 'if app_id and "app_id" in properties:' in platform_source
    assert 'if subject_user_id and "user_id" in properties:' in platform_source
    assert "queryRequestId" in profile_panel_source
    assert "urlTabParam && allPages.some(p => p.id === urlTabParam)" in profile_source
    assert "return urlTabParam;" in profile_source
    escalation_source = _read("chat-ui/src/core/ui/EscalationCard.js")
    assert "const handleEscalate = async () =>" in escalation_source
    assert "await onResponse({ action: 'open_support' })" in escalation_source
    chat_interface_source = _read("chat-ui/src/components/chat/ChatInterface.jsx")
    event_dispatcher_source = _read("chat-ui/src/core/eventDispatcher.js")
    assert "return onAgentAction(action);" in chat_interface_source
    assert "return handleAgentAction({" in chat_interface_source
    assert "const responseHandler = async (response) =>" in event_dispatcher_source
    assert "return await onResponse(response);" in event_dispatcher_source


def test_widget_ask_waits_for_persisted_general_mode_before_flushing() -> None:
    widget_source = _read("chat-ui/src/components/chat/PersistentChatWidget.jsx")
    widget_ws_source = _read("chat-ui/src/hooks/useWidgetAskWS.js")

    assert "generalModeReady" in widget_source
    assert "wsStatus !== 'connected' || !generalModeReady" in widget_source
    assert "wsStatus === 'connected' && generalModeReady" in widget_source
    assert "const [generalModeReady, setGeneralModeReady] = useState(false);" in widget_ws_source
    assert "setGeneralModeReady(true);" in widget_ws_source
    assert "if (!wsRef.current || !generalModeReady) return false;" in widget_ws_source


def test_dialog_and_overlay_primitives_use_mobile_sheet_layout() -> None:
    dialog_source = _read("chat-ui/src/ui/base/components/dialog.jsx")
    transition_source = _read("chat-ui/src/ui/screens/TransitionOverlayFrame.jsx")
    surface_source = _read("chat-ui/src/ui/primitives/Surface.jsx")

    assert 'fixed inset-x-0 bottom-0 z-50 grid w-full' in dialog_source
    assert 'rounded-t-[1.75rem] border-b-0' in dialog_source
    assert 'sm:left-[50%] sm:top-[50%]' in dialog_source

    assert 'fixed inset-x-0 bottom-0 z-[90] w-full' in transition_source
    assert 'sm:left-1/2 sm:top-1/2' in transition_source

    assert 'items-end justify-center' in surface_source
    assert 'rounded-t-xl' in surface_source


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
    assert "workspace users route stays responsive across desktop and mobile widths" in smoke_source
    assert "workspace integrations route stays responsive across desktop and mobile widths" in smoke_source
    assert "workspace support route stays responsive across desktop and mobile widths" in smoke_source
    assert "create app transition overlay can return to Apps" in smoke_source
    assert "workspace billing route stays responsive across desktop and mobile widths" not in smoke_source
    assert "workspace hosting route stays responsive across desktop and mobile widths" not in smoke_source
    assert "app Studio root redirects to manifest default portal" in smoke_source
    assert "app overview route stays responsive across desktop and mobile widths" in smoke_source
    assert "app building route stays responsive across desktop and mobile widths" in smoke_source
    assert "app health route stays responsive across desktop and mobile widths" in smoke_source
    assert "app integrations route stays responsive across desktop and mobile widths" in smoke_source
    assert "app usage route stays responsive across desktop and mobile widths" in smoke_source
    assert "app support route stays responsive across desktop and mobile widths" in smoke_source
    assert "app billing route stays responsive across desktop and mobile widths" not in smoke_source
    assert "app access route stays responsive across desktop and mobile widths" in smoke_source
    assert "app build review route stays responsive across desktop and mobile widths" in smoke_source
    assert "app hosting route stays responsive across desktop and mobile widths" not in smoke_source
    assert "mobile app Studio navigation keeps route transitions stable" in smoke_source
    assert "mobile workspace Studio navigation keeps route transitions stable" in smoke_source
    assert "/api/studio/overview" in smoke_source
    assert "npx playwright install --with-deps chromium" in ci_source
    assert "npm run test:responsive-smoke" in ci_source


def test_factory_app_surface_routes_are_all_covered_by_smoke() -> None:
    manifest = json.loads(_read("factory_app/app/ui/route_manifest.json"))
    smoke_source = _read("web_shell/playwright/apps.responsive.smoke.spec.js")
    console_components = {
        path.stem
        for path in (_workspace() / "factory_app" / "app" / "admin" / "pages").glob("*.jsx")
    }

    smoke_titles_by_component = {
        "AppsPage": "apps route stays responsive across desktop and mobile widths",
        "WorkspaceUsagePage": "workspace usage route stays responsive across desktop and mobile widths",
        "WorkspaceUsersPage": "workspace users route stays responsive across desktop and mobile widths",
        "WorkspaceIntegrationsPage": "workspace integrations route stays responsive across desktop and mobile widths",
        "UserSupportPage": "workspace support route stays responsive across desktop and mobile widths",
        "StudioPage": "app Studio root redirects to manifest default portal",
        "AppOverviewPage": "app overview route stays responsive across desktop and mobile widths",
        "DashboardPortalPage": "app building route stays responsive across desktop and mobile widths",
        "AppHealthPage": "app health route stays responsive across desktop and mobile widths",
        "AppAccessPage": "app access route stays responsive across desktop and mobile widths",
        "AppUsagePage": "app usage route stays responsive across desktop and mobile widths",
        "AppIntegrationsPage": "app integrations route stays responsive across desktop and mobile widths",
        "AppSupportPage": "app support route stays responsive across desktop and mobile widths",
        "AppBuildReviewPage": "app build review route stays responsive across desktop and mobile widths",
    }
    # Components served from chat-ui or custom pages — not admin console pages
    # covered by the Playwright studio smoke suite.
    _chat_ui_components = {"ProfilePage"}
    route_components = {
        page["component"]
        for page in manifest["pages"]
        if page.get("component")
        and page["component"] != "AdminPortal"
        and page["component"] != "CreateAppRedirectPage"
        and page["component"] not in _chat_ui_components
    }

    assert route_components == set(smoke_titles_by_component)
    for title in smoke_titles_by_component.values():
        assert title in smoke_source

    assert console_components == route_components | {
        "AppStudioChrome",
        "CreateAppRedirectPage",
        "RefinementControls",
        "UserSupportPanel",
        # Sub-components used by route-backed pages (not directly route-backed)
        "CarryForwardReportSummary",
        "CarryForwardReportPanel",
        "PricingHealthPanel",
    }


def test_factory_app_react_files_are_classified() -> None:
    manifest = json.loads(_read("factory_app/app/ui/route_manifest.json"))
    react_files = {
        relative
        for path in (_workspace() / "factory_app").rglob("*.jsx")
        for relative in [path.relative_to(_workspace()).as_posix()]
        if not relative.startswith("factory_app/build_context/")
    }
    # Components registered from chat-ui or custom pages/ (not factory_app/admin/pages/)
    _non_admin_page_components = {"ProfilePage"}
    route_backed_files = {
        f"factory_app/app/admin/pages/{page['component']}.jsx"
        for page in manifest["pages"]
        if page.get("component")
        and page["component"] != "AdminPortal"
        and page["component"] != "CreateAppRedirectPage"
        and page["component"] not in _non_admin_page_components
    }
    support_files = {
        "factory_app/app/admin/pages/AppStudioChrome.jsx",
        "factory_app/app/admin/pages/CreateAppRedirectPage.jsx",
        "factory_app/app/admin/pages/RefinementControls.jsx",
        "factory_app/app/admin/pages/UserSupportPanel.jsx",
        # Carry-forward display sub-components (used by route-backed pages)
        "factory_app/app/admin/pages/CarryForwardReportSummary.jsx",
        "factory_app/app/admin/pages/CarryForwardReportPanel.jsx",
        "factory_app/app/admin/pages/PricingHealthPanel.jsx",
        "factory_app/app/ui/components/StudioShared.jsx",
        "factory_app/app/ui/components/HarnessDecisionCard.jsx",
        "factory_app/app/ui/components/OnboardingTour.jsx",
        "factory_app/app/ui/installOnboardingTour.jsx",
        # ExistingAppDiscovery workflow-owned inline and artifact surfaces
        "factory_app/workflows/ExistingAppDiscovery/ui/AppIntelligenceOverviewCard.jsx",
        "factory_app/workflows/ExistingAppDiscovery/ui/AppIntelligenceProgressCard.jsx",
        "factory_app/workflows/ExistingAppDiscovery/ui/RepoAccessRecoveryCard.jsx",
        # AppReview workflow agentic UI artifact — emitted by present_review_summary
        "factory_app/workflows/AppReview/ui/AppReview/AppReviewSummary.jsx",
        # SubscriptionContractDesigner agentic UI artifact — approval card for contract review
        "factory_app/workflows/SubscriptionContractDesigner/ui/SubscriptionContractDesigner/SubscriptionContractReview.jsx",
    }

    assert react_files == route_backed_files | support_files

