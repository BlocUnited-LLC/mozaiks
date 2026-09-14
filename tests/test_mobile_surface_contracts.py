from __future__ import annotations

import json
from pathlib import Path

from mozaiksai.core.runtime.app.auth_contract import APP_AUTH_COMPONENTS


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


def test_mobile_table_and_navigation_follow_app_theme() -> None:
    table = _read("chat-ui/src/ui/primitives/DataTable.jsx")
    css = _read("chat-ui/src/components/layout/header-styles.css")
    bar = css.split(".shell-mobile-bottom-bar {", 1)[1].split("}", 1)[0]
    assert "background: var(--color-surface)" in bar
    assert "color: var(--color-text-primary)" in bar
    assert "box-shadow" not in bar
    assert "overflow-wrap:anywhere" in table
    assert 'aria-label="Select record"' in table


def test_summary_strip_fits_its_container_without_clipping_labels() -> None:
    source = _read("chat-ui/src/ui/primitives/SummaryStrip.jsx")

    assert 'grid-cols-[repeat(auto-fit,minmax(min(100%,10rem),1fr))]' in source
    assert 'whitespace-normal' in source
    assert 'overflow-wrap:anywhere' in source
    assert 'truncate' not in source
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
    # Mobile nav trigger is a sticky top-of-content control (admin-layout
    # pattern), never a floating pill overlapping the shell bottom bar.
    assert "sticky top-[calc(env(safe-area-inset-top,0px)+4.5rem)]" in layout_source
    assert "fixed bottom-[calc(env(safe-area-inset-bottom,0px)" not in layout_source
    assert "max-h-[82dvh]" in layout_source
    assert "top-24 w-[min" not in layout_source

    assert 'fixed right-0 bottom-6 z-50 widget-safe-bottom' in widget_source
    # The collapsed toggle is the only entry point to the assistant on a
    # non-chat route, so it must stay high-contrast against the page: an opaque
    # card surface with a solid primary edge, never a faint translucent tab.
    assert 'rounded-l-2xl border-2 border-r-0 border-primary/70 bg-card' in widget_source
    # The mark scales at the same 768px boundary the responsive smoke asserts
    # against (<=52px wide under it, <=64px at or above), so the desktop toggle
    # stays prominent without crowding a phone's screen edge.
    assert 'h-7 w-7 transition-transform group-hover:scale-110 md:h-9 md:w-9' in widget_source
    assert 'px-2 py-5' in widget_source and 'md:px-2.5' in widget_source
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
    assert "payload.subject_app_id = cleanAppId" in support_links_source
    assert "payload.app_id = cleanAppId" not in support_links_source
    assert "payload.user_id = cleanUserId" not in support_links_source
    assert "Authorization: `Bearer ${supportToken}`" in widget_source
    assert "Authorization: `Bearer ${supportToken}`" in chat_page_source
    assert "getSupportApiBaseUrl(api, config)" in widget_source
    assert "getSupportApiBaseUrl(api, config)" in chat_page_source
    assert "appId: resolvedAppId || supportScope.appId" in widget_source
    assert "appId: currentAppId || supportScope.appId" in chat_page_source
    assert "Your support request could not be sent. Please try again." in chat_page_source
    assert "navigate(buildUserSupportPath({ appId: currentAppId }))" not in chat_page_source
    assert "getAccessToken?.()" in profile_source
    assert "window.location.origin" in profile_source
    assert "getToken?.()" not in profile_source
    assert "studioModuleAction('workspace_support'" in profile_panel_source
    assert "if (page?.error)" in profile_panel_source
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

    assert 'fixed inset-x-0 bottom-0 z-50 grid' in dialog_source
    assert 'max-h-[calc(100dvh-1rem)] w-full' in dialog_source
    assert 'overflow-y-auto' in dialog_source
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
    auth_smoke_source = _read("web_shell/playwright/auth.spec.js")
    console_components = {
        path.stem
        for path in (_workspace() / "factory_app" / "app" / "admin" / "pages").glob("*.jsx")
    }

    smoke_titles_by_component = {
        "AppsPage": "apps route stays responsive across desktop and mobile widths",
        "WorkspacePerformancePage": "workspace performance route stays responsive across desktop and mobile widths",
        "WorkspaceUsagePage": "workspace usage route stays responsive across desktop and mobile widths",
        "WorkspaceUsersPage": "workspace users route stays responsive across desktop and mobile widths",
        "WorkspaceIntegrationsPage": "workspace integrations route stays responsive across desktop and mobile widths",
        "UserSupportPage": "workspace support route stays responsive across desktop and mobile widths",
        "StudioPage": "app Studio root redirects to manifest default portal",
        "AppOverviewPage": "app overview route stays responsive across desktop and mobile widths",
        "AppRevenuePage": "app revenue route stays responsive across desktop and mobile widths",
        "AppUsersPage": "app users analytics route stays responsive across desktop and mobile widths",
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

    assert route_components == set(smoke_titles_by_component) | APP_AUTH_COMPONENTS
    for title in smoke_titles_by_component.values():
        assert title in smoke_source
    assert "Factory login follows discovery and PKCE callback, restores a protected route, and exposes the exchanged token" in auth_smoke_source
    assert "a forged callback shows failure without authenticated identity" in auth_smoke_source
    assert "page.goto('/auth/callback?code=unsolicited&state=unknown')" in auth_smoke_source
    assert "testMatch: 'auth.spec.js'" in _read("web_shell/playwright.auth.config.js")

    assert console_components == (route_components - APP_AUTH_COMPONENTS) | {
        "AppStudioChrome",
        "CreateAppRedirectPage",
        "RefinementControls",
        "UserSupportPanel",
        # Sub-components used by route-backed pages (not directly route-backed)
        "CarryForwardReportSummary",
        "CarryForwardReportPanel",
        "PricingHealthPanel",
        "MetricDetailPanel",
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
    _non_admin_page_components = {"ProfilePage"} | APP_AUTH_COMPONENTS
    route_backed_files = {
        f"factory_app/app/admin/pages/{page['component']}.jsx"
        for page in manifest["pages"]
        if page.get("component")
        and page["component"] != "AdminPortal"
        and page["component"] != "CreateAppRedirectPage"
        and page["component"] not in _non_admin_page_components
    }
    support_files = {
        "factory_app/workflows/AgentGenerator/ui/WorkflowPlanReview.jsx",
        "factory_app/app/admin/pages/AppStudioChrome.jsx",
        "factory_app/app/admin/pages/CreateAppRedirectPage.jsx",
        "factory_app/app/admin/pages/RefinementControls.jsx",
        "factory_app/app/admin/pages/UserSupportPanel.jsx",
        # Carry-forward display sub-components (used by route-backed pages)
        "factory_app/app/admin/pages/CarryForwardReportSummary.jsx",
        "factory_app/app/admin/pages/CarryForwardReportPanel.jsx",
        "factory_app/app/admin/pages/PricingHealthPanel.jsx",
        # Universal metric drill-down drawer, opened from analytics surfaces
        "factory_app/app/admin/pages/MetricDetailPanel.jsx",
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
    shared_auth_source = _read("chat-ui/src/auth/AuthPages.jsx")
    for component in APP_AUTH_COMPONENTS:
        assert f"export function {component}(" in shared_auth_source



def test_widget_always_offers_workflow_access() -> None:
    """The widget is ask-only, so its workspace button is the user's only route
    back into a running build from a non-chat route. It must never be hidden
    behind an active-session check, and it must reach *any* running session —
    not only the one this browser last touched."""
    widget_source = _read("chat-ui/src/components/chat/PersistentChatWidget.jsx")

    # Rendered unconditionally (support mode swaps the panel, not the button).
    assert "{!inSupportMode && (" in widget_source
    assert "hasActiveWorkflow && !inSupportMode" not in widget_source

    # Server-side session list, not just this browser's stored pointers.
    assert "/api/sessions/list/" in widget_source
    assert "const [workflowSessions, setWorkflowSessions] = useState([]);" in widget_source

    # One session resumes directly, several open a picker, none starts one.
    assert "const handleWorkflowAccess = () => {" in widget_source
    assert "if (workflowSessions.length > 1) {" in widget_source
    assert "handleBackToWorkspace(workflowSessions[0]);" in widget_source
    assert "navigate('/chat?mode=workflow');" in widget_source

    # An explicit pick must win over the stored per-browser chat id.
    assert "const handleBackToWorkspace = (target = null) => {" in widget_source
    assert "target?.chat_id" in widget_source


def test_page_surfaces_share_one_content_measure() -> None:
    """Page routes align to the themeable content measure instead of each
    picking a max-width, which is what left wide viewports with uneven gutters."""
    tokens_source = _read("chat-ui/src/ui/theme/tokens.js")
    shell_css_source = _read("web_shell/styles.css")
    profile_source = _read("chat-ui/src/pages/ProfilePage.jsx")

    assert "const contentWidthScale = {" in tokens_source
    assert "'--mz-content-max'" in tokens_source
    assert "content_width = 'wide'" in tokens_source
    # The shell is Tailwind v4 and CSS-first: tailwind.config.js is vestigial
    # here, and the --container-* namespace is what actually produces the
    # max-w-* utility. Declaring the measure anywhere else compiles to nothing.
    assert "--container-content: var(--mz-content-max, 96rem);" in shell_css_source
    assert "max-w-content" in profile_source
    assert "max-w-3xl" not in profile_source.split("const containerClass")[1].split("\n")[0]


def test_widget_session_reads_carry_credentials() -> None:
    """Both widget session reads must send the bearer token. A bare fetch works
    only while auth is disabled and silently 401s the moment an app enables it,
    which would quietly strip the widget's workflow access."""
    widget_source = _read("chat-ui/src/components/chat/PersistentChatWidget.jsx")

    assert "import { authFetch } from '../../adapters/api';" in widget_source
    assert "authFetch(`/api/session/state?" in widget_source
    assert "authFetch(`/api/sessions/list/" in widget_source
    assert "fetch(`/api/session/state?" not in widget_source.replace("authFetch(`/api/session/state?", "")
    assert "fetch(`/api/sessions/list/" not in widget_source.replace("authFetch(`/api/sessions/list/", "")


def test_widget_workflow_button_announces_its_state() -> None:
    """The brand mark inside the button would otherwise supply a static
    accessible name, so assistive tech announced "Go to workflows" even with
    several builds running. Verified with Playwright against the built app:
    the title updated while the accessible name did not."""
    widget_source = _read("chat-ui/src/components/chat/PersistentChatWidget.jsx")

    # One derived label feeds both the tooltip and the accessible name, so the
    # two cannot drift apart.
    assert "const workflowAccessLabel = workflowSessions.length > 1" in widget_source
    assert "title={workflowAccessLabel}" in widget_source
    assert "aria-label={workflowAccessLabel}" in widget_source
    # The decorative mark must not contribute a competing name.
    assert 'alt=""\n                    aria-hidden="true"' in widget_source
    assert 'alt="Go to workflows"' not in widget_source


def test_widget_no_sessions_routes_to_declared_fresh_start_entrypoint() -> None:
    """With no running workflow the widget must open the app's own declared
    start-a-build surface, not bare workflow mode.

    Routing to /chat?mode=workflow resolved a workflow from stored client
    state, so a user with no sessions landed in whichever workflow this browser
    last touched — observed live as ExistingAppDiscovery (the brownfield
    adoption flow) instead of the create-app selector.
    """
    wrapper_source = _read("chat-ui/src/widget/GlobalChatWidgetWrapper.jsx")
    widget_source = _read("chat-ui/src/components/chat/PersistentChatWidget.jsx")

    # Discovered from shell config, never hardcoded, so any app's own
    # entrypoint declaration is honored.
    assert "p?.meta?.freshStart" in wrapper_source
    assert "freshStartPath={freshStartPath}" in wrapper_source
    assert "freshStartPath = null," in widget_source
    assert "navigate(freshStartPath);" in widget_source

    # The guessed-workflow route stays only as a last resort for an app that
    # declares no entrypoint at all.
    access_block = widget_source.split("const handleWorkflowAccess")[1].split("};")[0]
    assert access_block.index("navigate(freshStartPath);") < access_block.index(
        "navigate('/chat?mode=workflow');"
    )


def test_factory_declares_a_fresh_start_entrypoint() -> None:
    """The widget's fresh-start routing depends on this declaration existing."""
    registry = json.loads(
        _read("factory_app/workflows/extended_orchestration/extension_registry.json")
    )
    fresh = [
        entry for entry in registry.get("entrypoints", [])
        if (entry.get("meta") or {}).get("freshStart")
    ]
    assert fresh, "no entrypoint declares meta.freshStart"
    assert fresh[0]["path"] == "/create"
    assert fresh[0]["transition"] == "app_type_selector"
