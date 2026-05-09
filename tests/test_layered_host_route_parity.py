from __future__ import annotations

from importlib import import_module


CANONICAL_MOZAIKS_ROUTES = {
    ("GET", "/api/health"),
    ("GET", "/api/events/metrics"),
    ("GET", "/health/active-runs"),
    ("GET", "/metrics/perf/aggregate"),
    ("GET", "/metrics/perf/chats"),
    ("GET", "/api/shell-config"),
    ("GET", "/api/me"),
    ("PUT", "/api/me"),
    ("GET", "/api/me/preferences"),
    ("PUT", "/api/me/preferences"),
    ("GET", "/api/theme-config"),
    ("GET", "/api/pages/{name}"),
    ("GET", "/api/chats/{app_id}/{workflow_name}"),
    ("GET", "/api/chats/exists/{app_id}/{workflow_name}/{chat_id}"),
    ("POST", "/api/chats/{app_id}/{workflow_name}/start"),
    ("GET", "/api/chats/meta/{app_id}/{workflow_name}/{chat_id}"),
    ("POST", "/api/chat/upload"),
    ("POST", "/api/chat/upload/{app_id}/{user_id}"),
    ("GET", "/api/sessions/list/{app_id}/{user_id}"),
    ("GET", "/api/sessions/recent/{app_id}/{user_id}"),
    ("GET", "/api/sessions/oldest/{app_id}/{user_id}"),
    ("DELETE", "/api/sessions/{app_id}/{user_id}"),
    ("GET", "/api/transitions/{transition_id}"),
    ("POST", "/api/transitions/resolve"),
    ("GET", "/api/session/state"),
    ("POST", "/api/session/decisions/pending"),
    ("POST", "/api/session/decisions/resolve"),
    ("GET", "/api/modules/{module_name}/{action_name}"),
    ("POST", "/api/modules/{module_name}/{action_name}"),
    ("GET", "/api/workflows"),
    ("GET", "/api/workflows/config"),
    ("GET", "/api/workflows/{workflow_name}/transport"),
    ("GET", "/api/workflows/{workflow_name}/tools"),
    ("GET", "/api/workflows/{workflow_name}/ui-tools"),
    ("POST", "/api/workflows/{workflow_name}/trigger"),
    ("POST", "/api/workflows/trigger"),
    ("POST", "/chat/{app_id}/{chat_id}/{user_id}/input"),
    ("POST", "/chat/{app_id}/{chat_id}/component_action"),
    ("POST", "/api/tool-call/respond"),
    ("GET", "/api/studio/home"),
    ("GET", "/api/studio/create"),
    ("PUT", "/api/studio/create"),
    ("WS", "/ws/{workflow_name}/{app_id}/{chat_id}/{user_id}"),
}

REMOVED_LEGACY_ROUTES = {
    ("GET", "/"),
    ("GET", "/favicon.ico"),
    ("GET", "/.well-known/appspecific/com.chrome.devtools.json"),
    ("GET", "/api/download/workflow-file"),
}


def _public_routes(app) -> set[tuple[str, str]]:
    routes: set[tuple[str, str]] = set()
    for route in app.routes:
        path = getattr(route, "path", "")
        if not path or path.startswith(("/openapi", "/docs", "/redoc")):
            continue
        methods = getattr(route, "methods", None)
        if methods:
            for method in methods:
                if method not in {"HEAD", "OPTIONS"}:
                    routes.add((method, path))
        else:
            routes.add(("WS", path))
    return routes


def test_mozaiks_host_contains_canonical_routes():
    mozaiks_routes = _public_routes(import_module("mozaiksai.hosts.mozaiks").app)

    assert CANONICAL_MOZAIKS_ROUTES <= mozaiks_routes


def test_mozaiks_host_does_not_restore_removed_legacy_routes():
    mozaiks_routes = _public_routes(import_module("mozaiksai.hosts.mozaiks").app)

    assert not (REMOVED_LEGACY_ROUTES & mozaiks_routes)
