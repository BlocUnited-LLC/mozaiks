from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from mozaiksai.core.auth.adapters import AuthError, UserClaims, get_auth_adapter, register_adapter
from mozaiksai.core.auth.adapters.base import BaseAuthAdapter
from mozaiksai.core.auth.adapters.registry import reset_auth_adapter

ROOT = Path(__file__).resolve().parents[1]
PAGES = ROOT / "factory_app" / "app" / "admin" / "pages"
ONBOARDING_INSTALLER = ROOT / "factory_app" / "app" / "ui" / "installOnboardingTour.jsx"


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


def test_app_studio_data_uses_authenticated_fetch_for_studio_and_admin_routes() -> None:
    source = _read(PAGES / "useAppStudioData.js")

    assert "import { studioFetch } from './studioApi.js'" in source
    assert "studioFetch(`/api/studio/overview?app_id=${encodeURIComponent(appId)}`)" in source
    assert "studioFetch(`/api/admin/stats?app_id=${encodeURIComponent(appId)}`)" in source
    assert "studioFetch(`/api/studio/apps/${encodeURIComponent(appId)}/context`)" in source
    assert "fetch(`${API_BASE}/api/studio/" not in source
    assert "fetch(`${API_BASE}/api/admin/" not in source


def test_workspace_users_uses_authenticated_fetch_for_admin_users() -> None:
    source = _read(PAGES / "WorkspaceUsersPage.jsx")

    assert "import { studioFetch } from './studioApi.js'" in source
    assert "studioFetch('/api/admin/users', { signal: controller.signal })" in source
    assert "fetch(`${API_BASE}/api/admin/users`" not in source


def test_app_build_review_uses_authenticated_fetch_for_artifact_routes() -> None:
    source = _read(PAGES / "AppBuildReviewPage.jsx")

    assert "import { studioFetch } from './studioApi.js'" in source
    assert "studioFetch(\n    `/api/studio/build/artifacts/${encodeURIComponent(artifactVersionId)}/${endpoint}?app_id=${encodeURIComponent(appId)}`" in source
    assert "studioFetch(\n          `/api/studio/build/artifacts/${encodeURIComponent(artifactVersionId)}/review?app_id=${encodeURIComponent(appId)}`" in source
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
    assert "studioFetch(`/api/studio/build/artifacts/${encodeURIComponent(artifactVersionId)}/review`)" in source
    assert "studioFetch(`/api/studio/build/artifacts/${encodeURIComponent(artifactVersionId)}/${action}`" in source
    assert "fetch(`/api/studio/build/artifacts/${encodeURIComponent(artifactVersionId)}/review`)" not in source
    assert "fetch(`/api/studio/build/artifacts/${encodeURIComponent(artifactVersionId)}/${action}`" not in source


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
