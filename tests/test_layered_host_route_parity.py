from __future__ import annotations

from importlib import import_module

CANONICAL_STUDIO_ROUTES = {
    ("GET", "/api/health"),
    ("GET", "/api/events/metrics"),
    ("GET", "/health/active-runs"),
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
    ("GET", "/api/studio/overview"),
    ("GET", "/api/studio/apps"),
    ("POST", "/api/studio/apps"),
    ("PUT", "/api/studio/apps/{build_registry_id}/status"),
    ("GET", "/api/studio/build"),
    ("PUT", "/api/studio/build"),
    ("WS", "/ws/{workflow_name}/{app_id}/{chat_id}/{user_id}"),
}

REMOVED_RUNTIME_ROUTES = {
    ("GET", "/"),
    ("GET", "/favicon.ico"),
    ("GET", "/.well-known/appspecific/com.chrome.devtools.json"),
    ("GET", "/api/download/workflow-file"),
    ("GET", "/metrics/perf/aggregate"),
    ("GET", "/metrics/perf/chats"),
}


def _collect_routes(routes, prefix: str = "") -> set[tuple[str, str]]:
    """Recursively collect public routes, handling FastAPI 0.116+ _IncludedRouter."""
    result: set[tuple[str, str]] = set()
    for route in routes:
        path = getattr(route, "path", None)
        if path is not None:
            full_path = prefix + path
            if not full_path or full_path.startswith(("/openapi", "/docs", "/redoc")):
                continue
            methods = getattr(route, "methods", None)
            if methods:
                for method in methods:
                    if method not in {"HEAD", "OPTIONS"}:
                        result.add((method, full_path))
            else:
                result.add(("WS", full_path))
        elif hasattr(route, "router"):
            # FastAPI 0.116+: _IncludedRouter — recurse into the sub-router
            sub_prefix = prefix + getattr(route, "prefix", "")
            result.update(_collect_routes(route.router.routes, sub_prefix))
    return result


def _public_routes(app) -> set[tuple[str, str]]:
    return _collect_routes(app.routes)


def test_studio_host_contains_canonical_routes():
    studio_routes = _public_routes(import_module("mozaiksai.hosts.studio").app)

    assert CANONICAL_STUDIO_ROUTES <= studio_routes


def test_studio_host_does_not_restore_removed_routes():
    studio_routes = _public_routes(import_module("mozaiksai.hosts.studio").app)

    assert not (REMOVED_RUNTIME_ROUTES & studio_routes)

