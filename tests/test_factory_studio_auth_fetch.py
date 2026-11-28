from __future__ import annotations

import re
from pathlib import Path

from fastapi.testclient import TestClient

from mozaiksai.core.auth.adapters import AuthError, UserClaims, get_auth_adapter, register_adapter
from mozaiksai.core.auth.adapters.base import BaseAuthAdapter
from mozaiksai.core.auth.adapters.registry import reset_auth_adapter

ROOT = Path(__file__).resolve().parents[1]
PAGES = ROOT / "factory_app" / "app" / "admin" / "pages"
ONBOARDING_INSTALLER = ROOT / "factory_app" / "app" / "ui" / "installOnboardingTour.jsx"
# A call to the browser's own fetch: not studioFetch(, refetch(, or obj.fetch(.
_BARE_FETCH = re.compile(r"(?<![\w.])fetch\(")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_studio_api_exports_canonical_authenticated_fetch_helpers() -> None:
    source = _read(PAGES / "studioApi.js")

    assert "export function getStudioAccessToken()" in source
    assert "window.mozaiksAuth?.getAccessToken" in source
    assert "sessionStorage.getItem('mozaiks_access_token')" in source
    assert "export function studioAuthHeaders" in source
    assert "Authorization: `Bearer ${token}`" in source
    assert "export function studioFetch" in source
    assert "export async function studioModuleAction" in source


def test_workspace_apps_uses_authenticated_studio_fetch_for_studio_routes() -> None:
    source = _read(PAGES / "useWorkspaceApps.js")

    assert "import { studioFetch } from './studioApi.js'" in source
    assert "studioFetch('/api/studio/apps')" in source
    assert "studioFetch(`/api/studio/apps/${encodeURIComponent(buildRegistryId)}`" in source
    assert "fetch(`${API_BASE}/api/studio/apps`)" not in source


def test_workspace_dashboard_uses_authenticated_studio_fetch() -> None:
    source = _read(PAGES / "dashboardRoutes.js")

    assert "import { studioFetch } from './studioApi.js'" in source
    assert "studioFetch(`/api/studio/dashboard${suffix}`" in source
    assert "fetch(`${API_BASE}/api/studio/dashboard${suffix}`" not in source


def test_workspace_studio_data_uses_authenticated_fetch_for_apps_endpoint() -> None:
    source = _read(PAGES / "useWorkspaceStudioData.js")

    assert "import { studioFetch } from './studioApi.js'" in source
    assert "studioFetch('/api/studio/apps')" in source
    assert "studioFetch('/api/admin/stats')" in source
    assert "studioFetch('/api/admin/runs?limit=48')" in source
    assert "studioFetch('/api/admin/usage?limit=1000')" in source
    assert "fetch(`${API_BASE}/api/studio/apps`)" not in source


def test_support_pages_use_authenticated_fetch_for_profile_and_module_actions() -> None:
    for page_name in ("UserSupportPage.jsx", "AppSupportPage.jsx"):
        source = _read(PAGES / page_name)
        assert "import { studioFetch } from './studioApi.js'" in source
        assert "studioFetch('/api/me')" in source
        actions = ("list_support_requests",)
        if page_name == "AppSupportPage.jsx":
            actions += ("add_support_message", "update_support_request_status", "delete_support_request")
        for action in actions:
            assert f"studioFetch('/api/modules/workspace_support/{action}'" in source
        assert "fetch(" not in source.replace("studioFetch(", "")


def test_workspace_support_overview_surfaces_queue_failures() -> None:
    source = _read(PAGES / "UserSupportPage.jsx")
    assert "setSupportError(err?.message || 'Support chats could not be loaded.')" in source
    assert 'message={error || supportError}' in source
    assert "fetchSupportRequests({ scope: 'app', appId }).catch(() => null)" not in source


def test_app_studio_data_uses_authenticated_fetch_for_studio_and_admin_routes() -> None:
    source = _read(PAGES / "useAppStudioData.js")

    assert "import { studioFetch } from './studioApi.js'" in source
    assert "studioFetch(`/api/studio/overview?${buildScope}`, requestOptions)" in source
    assert "detailFetch(`/api/studio/build?${buildScope}`)" in source
    assert "overviewOnly ? Promise.resolve(null) : studioFetch(path, requestOptions)" in source
    assert "`build_registry_id=${encodeURIComponent(buildRegistryId)}`" in source
    assert "detailFetch(`/api/admin/stats?app_id=${encodeURIComponent(appId)}`)" in source
    assert "detailFetch(`/api/studio/apps/${encodeURIComponent(appId)}/context`)" in source
    assert "fetch(`${API_BASE}/api/studio/" not in source
    assert "fetch(`${API_BASE}/api/admin/" not in source


def test_workspace_integrations_uses_authenticated_fetch_for_module_actions() -> None:
    """A bare fetch sends no session token, so every action failed with 401 once sign-in was on."""
    source = _read(PAGES / "WorkspaceIntegrationsPage.jsx")

    assert "import { studioFetch } from './studioApi.js'" in source
    assert "studioFetch(`/api/modules/workspace_integrations/${action}`" in source
    assert not _BARE_FETCH.search(source)
    assert "API_BASE" not in source
    for action in (
        "list_integrations",
        "list_workspace_connectors",
        "save_workspace_connector",
        "set_integration_note",
        "delete_workspace_connector",
        "check_workspace_connector_health",
    ):
        assert f"integrationsAction('{action}'" in source


def test_workspace_integrations_links_get_api_key_to_the_provider() -> None:
    """"Get API key" opens the provider's page, never the catalog's Studio path.

    ``setup_url`` on a catalog row is ``/integrations/{id}``, which is not a
    route. The server sends the provider's own page as ``provider_setup_url``.
    """
    source = _read(PAGES / "WorkspaceIntegrationsPage.jsx")

    assert "const setupUrl = item.provider_setup_url || null" in source
    assert "item.setup_url" not in source
    # The link opens in a new tab without handing the page a reference back.
    link = source[source.index("{setupUrl && (") :]
    link = link[: link.index("</a>")]
    assert "href={setupUrl}" in link
    assert 'target="_blank"' in link and 'rel="noopener noreferrer"' in link
    assert "Get API key" in link


def test_no_studio_page_calls_the_backend_without_the_session() -> None:
    """Every Studio page goes through studioFetch; none calls fetch on its own."""
    offenders = []
    for path in sorted(PAGES.glob("*.js*")):
        if path.name == "studioApi.js":
            continue
        if _BARE_FETCH.search(_read(path)):
            offenders.append(path.name)
    assert offenders == []


def test_studio_pages_call_only_admin_endpoints_the_server_has() -> None:
    """A page that reads an endpoint with no handler shows zeros forever."""
    from mozaiksai.core.admin.router import router as admin_router

    served = {route.path for route in admin_router.routes}
    called: dict[str, set[str]] = {}
    for path in sorted(PAGES.glob("*.js*")):
        for endpoint in re.findall(r"/api/admin/[a-z_/]+", _read(path)):
            called.setdefault(endpoint.rstrip("/"), set()).add(path.name)
    missing = {endpoint: sorted(files) for endpoint, files in called.items() if endpoint not in served}
    assert missing == {}


def test_app_build_review_uses_authenticated_fetch_for_artifact_routes() -> None:
    source = _read(PAGES / "AppBuildReviewPage.jsx")

    assert "import { studioFetch } from './studioApi.js'" in source
    assert "studioFetch(\n          `/api/studio/build/artifacts/${encodeURIComponent(artifactVersionId)}/bundle?build_registry_id=${encodeURIComponent(buildRegistryId)}`" in source
    assert "UIToolRenderer" in source
    assert "fetch(\n    `${API_BASE}/api/studio/build/artifacts/" not in source
    assert "fetch(\n          `${API_BASE}/api/studio/build/artifacts/" not in source


def test_workflow_review_summary_uses_authenticated_fetch_for_promote() -> None:
    source = _read(ROOT / "factory_app" / "workflows" / "AppReview" / "ui" / "AppReview" / "AppReviewSummary.jsx")

    assert "import { studioFetch } from '../../../../app/admin/pages/studioApi.js';" in source
    assert "studioFetch(`/api/studio/build/artifacts/${encodeURIComponent(payload.artifact_version_id)}/promote${appIdQuery}`" in source
    assert "window.__MOZAIKS_ACCESS_TOKEN__" not in source


def test_app_workbench_uses_authenticated_fetch_for_artifact_review_actions() -> None:
    source = _read(ROOT / "factory_app" / "workflows" / "AppGenerator" / "ui" / "AppWorkbench.js")

    assert "import { studioFetch } from '../../../app/admin/pages/studioApi.js';" in source
    assert "studioFetch(`/api/studio/build/artifacts/${encodeURIComponent(reviewArtifactVersionId)}/review${artifactQuery}`)" in source
    assert "studioFetch(`/api/studio/build/artifacts/${encodeURIComponent(reviewArtifactVersionId)}/${action}${artifactQuery}`" in source
    assert "fetch(`/api/studio/build/artifacts/${encodeURIComponent(reviewArtifactVersionId)}/review`)" not in source
    assert "fetch(`/api/studio/build/artifacts/${encodeURIComponent(reviewArtifactVersionId)}/${action}`" not in source


def test_factory_onboarding_uses_canonical_studio_module_action() -> None:
    source = _read(ONBOARDING_INSTALLER)

    assert "import { studioModuleAction } from '../admin/pages/studioApi.js'" in source
    assert "return studioModuleAction(moduleName, actionName, input)" in source
    assert "sessionStorage.getItem('mozaiks_access_token')" not in source
    assert "localStorage.getItem('chatui_token')" not in source


def test_studio_apps_endpoint_rejects_missing_bearer_and_accepts_authenticated_user(monkeypatch) -> None:
    class _TestStudioAuthAdapter(BaseAuthAdapter):
        name = "studio-test"

        async def validate_token(self, token: str) -> UserClaims:
            if token != "valid-studio-token":
                raise AuthError("Invalid test token", status_code=401, provider=self.name)
            return UserClaims(
                user_id="studio-user",
                email="studio@example.test",
                roles=["admin"],
                scopes=["access_as_user"],
                raw_claims={"sub": "studio-user"},
                provider=self.name,
            )

        def is_enabled(self) -> bool:
            return True

    class _AppRegistryService:
        async def list_apps(self, **kwargs):
            assert kwargs["owner_user_id"] == "studio-user"
            return {"apps": []}

    monkeypatch.setenv("AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTH_PROVIDER", "studio-test")
    monkeypatch.setenv("RATE_LIMIT_ENABLED", "false")
    reset_auth_adapter()
    get_auth_adapter(force_provider="none")
    register_adapter("studio-test", _TestStudioAuthAdapter)

    from tests.import_utils import active_app_root

    # Importing the Studio host is environment-inert; bind the workspace
    # explicitly instead of relying on an import-time side effect.
    monkeypatch.setenv("PLATFORM_PATH", str(active_app_root()))

    from mozaiksai.hosts import studio as studio_app

    monkeypatch.setattr(studio_app, "_get_app_registry_service", lambda: _AppRegistryService())

    client = TestClient(studio_app.app)
    try:
        missing_bearer = client.get("/api/studio/apps")
        assert missing_bearer.status_code == 401
        assert missing_bearer.json()["detail"] == "Missing authorization token"

        authenticated = client.get(
            "/api/studio/apps",
            headers={"Authorization": "Bearer valid-studio-token"},
        )
        assert authenticated.status_code == 200
        assert authenticated.json()["apps"] == []
    finally:
        reset_auth_adapter()
