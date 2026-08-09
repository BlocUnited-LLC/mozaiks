from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest


class _Collection:
    def __init__(self) -> None:
        self.docs: dict[str, dict] = {}

    async def find_one(self, query, *_args, **_kwargs):
        if "_id" in query:
            doc = self.docs.get(query["_id"])
            return deepcopy(doc) if doc is not None else None
        for doc in self.docs.values():
            if all(doc.get(key) == value for key, value in query.items()):
                return deepcopy(doc)
        return None

    async def update_one(self, query, update, upsert=False):
        doc_id = query["_id"]
        exists = doc_id in self.docs
        if not exists and not upsert:
            return None

        doc = deepcopy(self.docs.get(doc_id, {"_id": doc_id}))
        if not exists:
            doc.update(deepcopy(update.get("$setOnInsert", {})))
        doc.update(deepcopy(update.get("$set", {})))
        doc["_id"] = doc_id
        self.docs[doc_id] = doc
        return None


@pytest.mark.asyncio
async def test_platform_profile_contract_uses_host_defaults_for_local_dev(monkeypatch):
    from mozaiksai.core.auth.dependencies import UserPrincipal
    from mozaiksai.hosts import platform as platform_app

    profiles = _Collection()

    async def _fake_profiles():
        return profiles

    monkeypatch.setattr(platform_app, "_account_profile_collection", _fake_profiles)

    principal = UserPrincipal(
        user_id="anonymous",
        email=None,
        name=None,
        roles=[],
        scopes=[],
        raw_claims={},
    )

    result = await platform_app.get_current_user_profile(app_id=None, principal=principal)

    assert result["app_id"] == platform_app._resolve_default_app_id()
    assert result["user_id"] == platform_app._DEFAULT_PROFILE_USER_ID
    assert result["username"] == platform_app._DEFAULT_PROFILE_USER_ID
    assert result["display_name"] == platform_app._DEFAULT_PROFILE_USER_ID


@pytest.mark.asyncio
async def test_platform_profile_contract_persists_display_name(monkeypatch):
    from mozaiksai.core.auth.dependencies import UserPrincipal
    from mozaiksai.hosts import platform as platform_app

    profiles = _Collection()

    async def _fake_profiles():
        return profiles

    monkeypatch.setattr(platform_app, "_account_profile_collection", _fake_profiles)

    principal = UserPrincipal(
        user_id="user_1",
        email="user@example.com",
        name="User Example",
        roles=["admin"],
        scopes=[],
        raw_claims={},
        app_id="app_1",
    )

    result = await platform_app.update_current_user_profile(
        body=platform_app.ProfileUpdateRequest(display_name="Builder"),
        app_id=None,
        principal=principal,
    )

    assert result["app_id"] == "app_1"
    assert result["user_id"] == "user_1"
    assert result["display_name"] == "Builder"
    assert result["roles"] == ["admin"]


@pytest.mark.asyncio
async def test_public_user_profile_resolves_by_username_without_private_fields(monkeypatch):
    from mozaiksai.core.auth.dependencies import UserPrincipal
    from mozaiksai.hosts import platform as platform_app

    profiles = _Collection()
    profiles.docs["app_1:user_2"] = {
        "_id": "app_1:user_2",
        "app_id": "app_1",
        "user_id": "user_2",
        "username": "alice",
        "display_name": "Alice",
        "email": "alice@example.com",
        "roles": ["admin"],
        "bio": "Profile bio",
        "avatar_url": "https://cdn.example/avatar.png",
    }

    async def _fake_profiles():
        return profiles

    monkeypatch.setattr(platform_app, "_account_profile_collection", _fake_profiles)

    principal = UserPrincipal(
        user_id="viewer",
        email="viewer@example.com",
        name="Viewer",
        roles=["member"],
        scopes=[],
        raw_claims={},
        app_id="app_1",
    )

    result = await platform_app.get_public_user_profile(username="alice", app_id=None, principal=principal)

    assert result["app_id"] == "app_1"
    assert result["user_id"] == "user_2"
    assert result["username"] == "alice"
    assert result["display_name"] == "Alice"
    assert result["bio"] == "Profile bio"
    assert "email" not in result
    assert "roles" not in result


@pytest.mark.asyncio
async def test_platform_profile_preferences_are_app_scoped(monkeypatch):
    from mozaiksai.core.auth.dependencies import UserPrincipal
    from mozaiksai.hosts import platform as platform_app

    preferences = _Collection()

    async def _fake_preferences():
        return preferences

    monkeypatch.setattr(platform_app, "_account_preferences_collection", _fake_preferences)

    principal = UserPrincipal(
        user_id="user_7",
        email="user7@example.com",
        name="User Seven",
        roles=["member"],
        scopes=[],
        raw_claims={},
        app_id="app_market",
    )

    saved = await platform_app.update_current_user_preferences(
        body=platform_app.ProfilePreferencesUpdateRequest(settings={"theme": "dark", "density": "compact"}),
        app_id=None,
        principal=principal,
    )
    loaded = await platform_app.get_current_user_preferences(app_id=None, principal=principal)

    assert saved["app_id"] == "app_market"
    assert saved["user_id"] == "user_7"
    assert saved["settings"] == {"theme": "dark", "density": "compact"}
    assert loaded == saved


@pytest.mark.asyncio
async def test_platform_shell_config_injects_profile_route():
    from mozaiksai.hosts import platform as platform_app

    shell_config = await platform_app.build_shell_config(surface="studio")
    pages = {page.get("path"): page for page in shell_config.get("pages", [])}
    header_paths = {
        page.get("path")
        for page in (shell_config.get("header") or {}).get("pages", [])
        if isinstance(page, dict)
    }

    assert "/me" in pages
    assert pages["/me"]["component"] == "ProfilePage"
    assert pages["/me"]["meta"]["requiresAuth"] is True
    assert "/me" not in header_paths


def test_profile_page_edits_preferences_through_host_contract() -> None:
    source = (
        Path(__file__).resolve().parents[1] / "chat-ui" / "src" / "pages" / "ProfilePage.jsx"
    ).read_text(encoding="utf-8")

    assert "method: 'PUT'" in source
    assert "api && typeof api.getHttpBaseUrl === 'function'" in source
    assert "VITE_API_URL" in source


# ---------------------------------------------------------------------------
# Profile panel discovery
# ---------------------------------------------------------------------------

def test_profile_panel_discovery_returns_empty_for_no_modules(tmp_path: Path) -> None:
    from mozaiksai.core.profile.discovery import load_profile_panels

    assert load_profile_panels(tmp_path) == []
    (tmp_path / "modules").mkdir()
    assert load_profile_panels(tmp_path) == []


def test_profile_panel_discovery_loads_valid_panel(tmp_path: Path) -> None:
    from mozaiksai.core.profile.discovery import load_profile_panels

    module_dir = tmp_path / "modules" / "wallet"
    (module_dir / "contracts").mkdir(parents=True)
    (module_dir / "contracts" / "profile.yaml").write_text(
        """
schema_version: mozaiks.profile.v1
panels:
  - id: wallet-summary
    title: Wallet
    description: Balance info.
    order: 20
    kind: metrics
    action: get_wallet_summary
    fields:
      - { id: balance, label: Balance, type: currency }
      - { id: pending, label: Pending, type: currency }
""".lstrip(),
        encoding="utf-8",
    )

    panels = load_profile_panels(tmp_path)
    assert len(panels) == 1
    panel = panels[0]
    assert panel["id"] == "wallet-summary"
    assert panel["module_id"] == "wallet"
    assert panel["order"] == 20
    assert panel["kind"] == "metrics"
    assert panel["action"] == "get_wallet_summary"
    assert len(panel["fields"]) == 2
    assert panel["fields"][0]["type"] == "currency"


def test_profile_panel_discovery_sorts_by_order(tmp_path: Path) -> None:
    from mozaiksai.core.profile.discovery import load_profile_panels

    for module_id, order in [("billing", 50), ("wallet", 20), ("notifications", 80)]:
        module_dir = tmp_path / "modules" / module_id
        (module_dir / "contracts").mkdir(parents=True)
        (module_dir / "contracts" / "profile.yaml").write_text(
            f"""
schema_version: mozaiks.profile.v1
panels:
  - id: {module_id}-panel
    title: {module_id.title()}
    order: {order}
    kind: metrics
    action: get_summary
    fields:
      - {{ id: value, label: Value, type: string }}
""".lstrip(),
            encoding="utf-8",
        )

    panels = load_profile_panels(tmp_path)
    assert [p["order"] for p in panels] == [20, 50, 80]


def test_profile_panel_discovery_skips_bad_yaml(tmp_path: Path) -> None:
    from mozaiksai.core.profile.discovery import load_profile_panels

    good_dir = tmp_path / "modules" / "wallet"
    bad_dir = tmp_path / "modules" / "broken"
    (good_dir / "contracts").mkdir(parents=True)
    (bad_dir / "contracts").mkdir(parents=True)

    (good_dir / "contracts" / "profile.yaml").write_text(
        """
schema_version: mozaiks.profile.v1
panels:
  - id: balance
    title: Balance
    order: 10
    kind: metrics
    fields:
      - { id: amount, label: Amount, type: currency }
""".lstrip(),
        encoding="utf-8",
    )
    # invalid: component kind without component field
    (bad_dir / "contracts" / "profile.yaml").write_text(
        """
schema_version: mozaiks.profile.v1
panels:
  - id: bad-panel
    title: Bad
    kind: component
""".lstrip(),
        encoding="utf-8",
    )

    panels = load_profile_panels(tmp_path)
    assert len(panels) == 1
    assert panels[0]["module_id"] == "wallet"


def test_profile_panel_discovery_sorts_by_module_id_on_order_tie(tmp_path: Path) -> None:
    from mozaiksai.core.profile.discovery import load_profile_panels

    for module_id in ["zeta", "alpha"]:
        module_dir = tmp_path / "modules" / module_id
        (module_dir / "contracts").mkdir(parents=True)
        (module_dir / "contracts" / "profile.yaml").write_text(
            """
schema_version: mozaiks.profile.v1
panels:
  - id: summary
    title: Summary
    order: 50
    kind: metrics
    fields:
      - { id: value, label: Value, type: string }
""".lstrip(),
            encoding="utf-8",
        )

    panels = load_profile_panels(tmp_path)
    assert len(panels) == 2
    # tie on order — should sort alphabetically by module_id
    assert panels[0]["module_id"] == "alpha"
    assert panels[1]["module_id"] == "zeta"


def test_profile_panel_discovery_component_kind(tmp_path: Path) -> None:
    from mozaiksai.core.profile.discovery import load_profile_panels

    module_dir = tmp_path / "modules" / "billing"
    (module_dir / "contracts").mkdir(parents=True)
    (module_dir / "contracts" / "profile.yaml").write_text(
        """
schema_version: mozaiks.profile.v1
panels:
  - id: billing-detail
    title: Billing
    order: 30
    kind: component
    component: BillingProfilePanel
    action: get_billing_summary
""".lstrip(),
        encoding="utf-8",
    )

    panels = load_profile_panels(tmp_path)
    assert len(panels) == 1
    assert panels[0]["kind"] == "component"
    assert panels[0]["component"] == "BillingProfilePanel"
    assert panels[0]["action"] == "get_billing_summary"


# ---------------------------------------------------------------------------
# ModuleProfileManifest validation
# ---------------------------------------------------------------------------

def test_module_profile_manifest_rejects_duplicate_panel_ids() -> None:
    import pytest
    from pydantic import ValidationError

    from mozaiksai.core.runtime.app.module_loader import ModuleProfileManifest

    with pytest.raises(ValidationError, match="unique id"):
        ModuleProfileManifest.model_validate({
            "schema_version": "mozaiks.profile.v1",
            "panels": [
                {"id": "dup", "title": "A", "kind": "metrics",
                 "fields": [{"id": "x", "label": "X", "type": "string"}]},
                {"id": "dup", "title": "B", "kind": "metrics",
                 "fields": [{"id": "y", "label": "Y", "type": "string"}]},
            ],
        })


def test_module_profile_manifest_rejects_component_without_component_field() -> None:
    import pytest
    from pydantic import ValidationError

    from mozaiksai.core.runtime.app.module_loader import ModuleProfileManifest

    with pytest.raises(ValidationError, match="component"):
        ModuleProfileManifest.model_validate({
            "schema_version": "mozaiks.profile.v1",
            "panels": [{"id": "p", "title": "P", "kind": "component"}],
        })


def test_module_profile_manifest_rejects_metrics_without_fields() -> None:
    import pytest
    from pydantic import ValidationError

    from mozaiksai.core.runtime.app.module_loader import ModuleProfileManifest

    with pytest.raises(ValidationError, match="fields"):
        ModuleProfileManifest.model_validate({
            "schema_version": "mozaiks.profile.v1",
            "panels": [{"id": "p", "title": "P", "kind": "metrics", "fields": []}],
        })


def test_module_profile_manifest_rejects_unknown_field_type() -> None:
    import pytest
    from pydantic import ValidationError

    from mozaiksai.core.runtime.app.module_loader import ModuleProfileManifest

    with pytest.raises(ValidationError):
        ModuleProfileManifest.model_validate({
            "schema_version": "mozaiks.profile.v1",
            "panels": [{
                "id": "p", "title": "P", "kind": "metrics",
                "fields": [{"id": "x", "label": "X", "type": "freeform_html"}],
            }],
        })


def test_module_profile_manifest_rejects_wrong_schema_version() -> None:
    import pytest
    from pydantic import ValidationError

    from mozaiksai.core.runtime.app.module_loader import ModuleProfileManifest

    with pytest.raises(ValidationError):
        ModuleProfileManifest.model_validate({
            "schema_version": "mozaiks.profile.v99",
            "panels": [],
        })


def test_module_profile_manifest_rejects_list_without_fields() -> None:
    import pytest
    from pydantic import ValidationError

    from mozaiksai.core.runtime.app.module_loader import ModuleProfileManifest

    with pytest.raises(ValidationError, match="fields"):
        ModuleProfileManifest.model_validate({
            "schema_version": "mozaiks.profile.v1",
            "panels": [{"id": "p", "title": "P", "kind": "list", "fields": []}],
        })


def test_module_profile_manifest_rejects_form_kind() -> None:
    """form kind is reserved and not yet implemented; validator must reject it."""
    import pytest
    from pydantic import ValidationError

    from mozaiksai.core.runtime.app.module_loader import ModuleProfileManifest

    with pytest.raises(ValidationError, match="kind"):
        ModuleProfileManifest.model_validate({
            "schema_version": "mozaiks.profile.v1",
            "panels": [{
                "id": "p", "title": "P", "kind": "form",
                "fields": [{"id": "x", "label": "X", "type": "string"}],
            }],
        })


# ---------------------------------------------------------------------------
# GET /api/me/profile-panels endpoint
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_profile_panels_endpoint_returns_empty_when_no_modules(monkeypatch, tmp_path: Path) -> None:
    from mozaiksai.core.auth.dependencies import UserPrincipal
    from mozaiksai.hosts import platform as platform_app

    (tmp_path / "app.json").write_text('{"appName": "Test"}', encoding="utf-8")
    monkeypatch.setenv("PLATFORM_PATH", str(tmp_path))

    principal = UserPrincipal(
        user_id="u1", email="u@example.com", name="User", roles=[],
        scopes=[], raw_claims={},
    )

    result = await platform_app.get_profile_panels(app_id=None, principal=principal)
    assert result == {"panels": []}


@pytest.mark.asyncio
async def test_profile_panels_endpoint_hydrates_panel_without_action(monkeypatch, tmp_path: Path) -> None:
    from mozaiksai.core.auth.dependencies import UserPrincipal
    from mozaiksai.hosts import platform as platform_app

    module_dir = tmp_path / "modules" / "wallet"
    (module_dir / "contracts").mkdir(parents=True)
    (tmp_path / "app.json").write_text('{"appName": "Test"}', encoding="utf-8")
    (module_dir / "contracts" / "profile.yaml").write_text(
        """
schema_version: mozaiks.profile.v1
panels:
  - id: balance
    title: Wallet Balance
    order: 20
    kind: metrics
    fields:
      - { id: amount, label: Amount, type: currency }
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("PLATFORM_PATH", str(tmp_path))

    principal = UserPrincipal(
        user_id="u1", email="u@example.com", name="User", roles=[],
        scopes=[], raw_claims={},
    )

    result = await platform_app.get_profile_panels(app_id=None, principal=principal)
    panels = result["panels"]
    assert len(panels) == 1
    assert panels[0]["id"] == "balance"
    assert panels[0]["module_id"] == "wallet"
    assert panels[0]["data"] is None
    assert panels[0]["error"] is None


@pytest.mark.asyncio
async def test_profile_panels_endpoint_hydrates_action_on_success(monkeypatch, tmp_path: Path) -> None:
    from mozaiksai.core.auth.dependencies import UserPrincipal
    from mozaiksai.core.runtime.composition.module_executor import ModuleResult
    from mozaiksai.hosts import platform as platform_app

    module_dir = tmp_path / "modules" / "activity"
    (module_dir / "contracts").mkdir(parents=True)
    (tmp_path / "app.json").write_text('{"appName": "Test"}', encoding="utf-8")
    (module_dir / "contracts" / "profile.yaml").write_text(
        """
schema_version: mozaiks.profile.v1
panels:
  - id: activity-summary
    title: Activity
    order: 10
    kind: metrics
    action: get_activity_summary
    fields:
      - { id: total, label: Total, type: number }
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("PLATFORM_PATH", str(tmp_path))

    class _FakeExecutor:
        async def execute(self, req, context):
            return ModuleResult(success=True, data={"total": 42})

    class _FakeRegistry:
        @property
        def module_executor(self):
            return _FakeExecutor()

    monkeypatch.setattr(platform_app, "executor_registry", _FakeRegistry())

    principal = UserPrincipal(
        user_id="u1", email="u@example.com", name="User", roles=[],
        scopes=[], raw_claims={},
    )

    result = await platform_app.get_profile_panels(app_id=None, principal=principal)
    panels = result["panels"]
    assert len(panels) == 1
    assert panels[0]["id"] == "activity-summary"
    assert panels[0]["data"] == {"total": 42}
    assert panels[0]["error"] is None


@pytest.mark.asyncio
async def test_profile_panels_endpoint_action_failure_returns_safe_error(monkeypatch, tmp_path: Path) -> None:
    from mozaiksai.core.auth.dependencies import UserPrincipal
    from mozaiksai.core.runtime.composition.module_executor import ModuleResult
    from mozaiksai.hosts import platform as platform_app

    module_dir = tmp_path / "modules" / "usage"
    (module_dir / "contracts").mkdir(parents=True)
    (tmp_path / "app.json").write_text('{"appName": "Test"}', encoding="utf-8")
    (module_dir / "contracts" / "profile.yaml").write_text(
        """
schema_version: mozaiks.profile.v1
panels:
  - id: usage-summary
    title: Usage
    order: 10
    kind: metrics
    action: get_usage_summary
    fields:
      - { id: requests, label: Requests, type: number }
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("PLATFORM_PATH", str(tmp_path))

    class _FailingExecutor:
        async def execute(self, req, context):
            return ModuleResult(success=False, error="service unavailable")

    class _FakeRegistry:
        @property
        def module_executor(self):
            return _FailingExecutor()

    monkeypatch.setattr(platform_app, "executor_registry", _FakeRegistry())

    principal = UserPrincipal(
        user_id="u1", email="u@example.com", name="User", roles=[],
        scopes=[], raw_claims={},
    )

    result = await platform_app.get_profile_panels(app_id=None, principal=principal)
    panels = result["panels"]
    assert len(panels) == 1
    assert panels[0]["data"] is None
    assert "service unavailable" in (panels[0]["error"] or "")


@pytest.mark.asyncio
async def test_profile_tabs_pass_subject_user_id_only_to_actions_that_declare_it(monkeypatch, tmp_path: Path) -> None:
    from mozaiksai.core.auth.dependencies import UserPrincipal
    from mozaiksai.core.runtime.composition.module_executor import ModuleResult
    from mozaiksai.hosts import platform as platform_app

    (tmp_path / "app.json").write_text('{"appName": "Test"}', encoding="utf-8")
    posts_dir = tmp_path / "modules" / "user_posts" / "contracts"
    messages_dir = tmp_path / "modules" / "messages" / "contracts"
    posts_dir.mkdir(parents=True)
    messages_dir.mkdir(parents=True)
    (posts_dir / "profile.yaml").write_text(
        """
schema_version: mozaiks.profile.v1
tabs:
  - id: posts
    label: Posts
    order: 10
    action: list_user_posts
    component: UserPostsTab
""".lstrip(),
        encoding="utf-8",
    )
    (messages_dir / "profile.yaml").write_text(
        """
schema_version: mozaiks.profile.v1
tabs:
  - id: messages
    label: Messages
    order: 20
    action: list_threads
    component: MessagingProfileTab
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("PLATFORM_PATH", str(tmp_path))

    class _FakeExecutor:
        _action_schemas = {
            "user_posts": {
                "list_user_posts": {
                    "input": {
                        "type": "object",
                        "properties": {"user_id": {"type": "string"}},
                    }
                }
            },
            "messages": {
                "list_threads": {
                    "input": {
                        "type": "object",
                        "properties": {"status": {"type": "string"}},
                    }
                }
            },
        }

        def __init__(self) -> None:
            self.requests = []

        async def execute(self, req, context):
            self.requests.append(req)
            return ModuleResult(success=True, data={"params": req.params, "viewer_user_id": req.user_id})

    fake_executor = _FakeExecutor()

    class _FakeRegistry:
        @property
        def module_executor(self):
            return fake_executor

    monkeypatch.setattr(platform_app, "executor_registry", _FakeRegistry())

    principal = UserPrincipal(
        user_id="viewer-user",
        email="viewer@example.com",
        name="Viewer",
        roles=[],
        scopes=[],
        raw_claims={},
        app_id="app_1",
    )

    result = await platform_app.get_profile_tabs(user_id="profile-user", app_id=None, principal=principal)

    assert [tab["id"] for tab in result["tabs"]] == ["posts", "messages"]
    by_module = {request.module: request for request in fake_executor.requests}
    assert by_module["user_posts"].params == {"user_id": "profile-user"}
    assert by_module["user_posts"].user_id == "viewer-user"
    assert by_module["messages"].params == {}
    assert by_module["messages"].user_id == "viewer-user"


@pytest.mark.asyncio
async def test_profile_pages_do_not_inject_my_apps(monkeypatch, tmp_path: Path) -> None:
    from mozaiksai.core.auth.dependencies import UserPrincipal
    from mozaiksai.hosts import platform as platform_app

    (tmp_path / "app.json").write_text('{"appName": "Test"}', encoding="utf-8")
    monkeypatch.setenv("PLATFORM_PATH", str(tmp_path))

    class _FakeExecutor:
        _modules = {"app_registry": object()}

    class _FakeRegistry:
        @property
        def module_executor(self):
            return _FakeExecutor()

    monkeypatch.setattr(platform_app, "executor_registry", _FakeRegistry())

    principal = UserPrincipal(
        user_id="viewer-user",
        email="viewer@example.com",
        name="Viewer",
        roles=[],
        scopes=[],
        raw_claims={},
        app_id="app_1",
    )

    result = await platform_app.get_profile_pages(app_id=None, principal=principal)

    ids = [page["id"] for page in result["pages"]]
    assert "my-apps" not in ids
    assert "overview" in ids
    assert "settings" in ids


# ---------------------------------------------------------------------------
# ProfilePage.jsx — page contract surface check
# ---------------------------------------------------------------------------

def test_profile_page_fetches_profile_pages() -> None:
    source = (
        Path(__file__).resolve().parents[1] / "chat-ui" / "src" / "pages" / "ProfilePage.jsx"
    ).read_text(encoding="utf-8")

    assert "/api/me/profile-pages" in source
    assert "/api/me/profile-tabs" not in source
    assert "/api/me/profile-panels" not in source
    assert "/api/users/" in source
    assert "subjectParams.set('username', username)" in source
    assert "componentRegistry" in source
    assert "!Array.isArray(body.sections)" in source

