"""
Tests for runtime security hardening: security headers middleware and
path ID format validation.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request
from starlette.responses import JSONResponse

from mozaiksai.core.auth.dependencies import validate_path_id
from mozaiksai.core.transport.security_headers import SecurityHeadersMiddleware


# ---------------------------------------------------------------------------
# validate_path_id unit tests
# ---------------------------------------------------------------------------

class TestValidatePathId:
    def test_valid_uuid(self):
        value = "550e8400-e29b-41d4-a716-446655440000"
        assert validate_path_id(value, "app_id") == value

    def test_valid_slug(self):
        assert validate_path_id("my-app_01", "app_id") == "my-app_01"

    def test_valid_short(self):
        assert validate_path_id("abc", "user_id") == "abc"

    def test_valid_alphanumeric(self):
        assert validate_path_id("user123", "user_id") == "user123"

    def test_valid_with_dots(self):
        assert validate_path_id("workflow.v2", "workflow_name") == "workflow.v2"

    def test_rejects_empty_string(self):
        with pytest.raises(HTTPException) as exc_info:
            validate_path_id("", "app_id")
        assert exc_info.value.status_code == 400

    def test_rejects_path_traversal(self):
        with pytest.raises(HTTPException) as exc_info:
            validate_path_id("../../etc/passwd", "app_id")
        assert exc_info.value.status_code == 400

    def test_rejects_dotdot_alone(self):
        with pytest.raises(HTTPException) as exc_info:
            validate_path_id("..", "app_id")
        assert exc_info.value.status_code == 400

    def test_rejects_shell_metachar_semicolon(self):
        with pytest.raises(HTTPException) as exc_info:
            validate_path_id("app;rm -rf /", "app_id")
        assert exc_info.value.status_code == 400

    def test_rejects_shell_metachar_backtick(self):
        with pytest.raises(HTTPException) as exc_info:
            validate_path_id("app`whoami`", "app_id")
        assert exc_info.value.status_code == 400

    def test_rejects_dollar_sign(self):
        with pytest.raises(HTTPException) as exc_info:
            validate_path_id("app$HOME", "app_id")
        assert exc_info.value.status_code == 400

    def test_rejects_newline(self):
        with pytest.raises(HTTPException) as exc_info:
            validate_path_id("app\nid", "app_id")
        assert exc_info.value.status_code == 400

    def test_rejects_space(self):
        with pytest.raises(HTTPException) as exc_info:
            validate_path_id("app id", "app_id")
        assert exc_info.value.status_code == 400

    def test_rejects_too_long(self):
        with pytest.raises(HTTPException) as exc_info:
            validate_path_id("a" * 129, "app_id")
        assert exc_info.value.status_code == 400

    def test_accepts_max_length(self):
        value = "a" * 128
        assert validate_path_id(value, "app_id") == value

    def test_error_message_includes_field_name(self):
        with pytest.raises(HTTPException) as exc_info:
            validate_path_id("bad value", "chat_id")
        assert "chat_id" in exc_info.value.detail


# ---------------------------------------------------------------------------
# SecurityHeadersMiddleware integration tests
# ---------------------------------------------------------------------------

def _make_app(enabled: bool = True) -> tuple[FastAPI, TestClient]:
    test_app = FastAPI()

    @test_app.get("/test")
    async def endpoint():
        return {"ok": True}

    @test_app.get("/already-has-csp")
    async def endpoint_with_csp(request: Request):
        resp = JSONResponse({"ok": True})
        resp.headers["Content-Security-Policy"] = "default-src 'none'"
        return resp

    import os
    os.environ["SECURITY_HEADERS_ENABLED"] = "true" if enabled else "false"
    test_app.add_middleware(SecurityHeadersMiddleware)
    return test_app, TestClient(test_app)


class TestSecurityHeadersMiddleware:
    def setup_method(self):
        import os
        os.environ.pop("SECURITY_HEADERS_ENABLED", None)

    def test_x_content_type_options_nosniff(self):
        _, client = _make_app(enabled=True)
        resp = client.get("/test")
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"

    def test_x_frame_options_deny(self):
        _, client = _make_app(enabled=True)
        resp = client.get("/test")
        assert resp.headers.get("X-Frame-Options") == "DENY"

    def test_x_permitted_cross_domain_policies_none(self):
        _, client = _make_app(enabled=True)
        resp = client.get("/test")
        assert resp.headers.get("X-Permitted-Cross-Domain-Policies") == "none"

    def test_referrer_policy_set(self):
        _, client = _make_app(enabled=True)
        resp = client.get("/test")
        assert "Referrer-Policy" in resp.headers

    def test_permissions_policy_set(self):
        _, client = _make_app(enabled=True)
        resp = client.get("/test")
        assert "Permissions-Policy" in resp.headers

    def test_content_security_policy_set(self):
        _, client = _make_app(enabled=True)
        resp = client.get("/test")
        assert "Content-Security-Policy" in resp.headers

    def test_csp_includes_frame_ancestors_none(self):
        _, client = _make_app(enabled=True)
        resp = client.get("/test")
        assert "frame-ancestors 'none'" in resp.headers.get("Content-Security-Policy", "")

    def test_does_not_override_existing_csp(self):
        _, client = _make_app(enabled=True)
        resp = client.get("/already-has-csp")
        # The route set its own CSP — middleware must not overwrite it
        assert resp.headers.get("Content-Security-Policy") == "default-src 'none'"

    def test_disabled_adds_no_headers(self):
        _, client = _make_app(enabled=False)
        resp = client.get("/test")
        assert "X-Content-Type-Options" not in resp.headers
        assert "X-Frame-Options" not in resp.headers
        assert "Content-Security-Policy" not in resp.headers

    def test_no_hsts_on_http(self):
        # TestClient uses http:// — HSTS must not be added
        _, client = _make_app(enabled=True)
        resp = client.get("/test")
        assert "Strict-Transport-Security" not in resp.headers


# ---------------------------------------------------------------------------
# RequestBodySizeLimitMiddleware tests
# ---------------------------------------------------------------------------

def _make_body_limit_app(max_bytes: int = 100, excluded: str = "/api/upload") -> TestClient:
    import os
    os.environ["MAX_REQUEST_BODY_BYTES"] = str(max_bytes)
    os.environ["MAX_REQUEST_BODY_EXCLUDED_PATHS"] = excluded

    from mozaiksai.core.transport.request_middleware import RequestBodySizeLimitMiddleware

    body_app = FastAPI()

    @body_app.post("/api/data")
    async def endpoint():
        return {"ok": True}

    @body_app.post("/api/upload/file")
    async def upload():
        return {"ok": True}

    body_app.add_middleware(RequestBodySizeLimitMiddleware)
    return TestClient(body_app)


class TestRequestBodySizeLimitMiddleware:
    def setup_method(self):
        import os
        os.environ.pop("MAX_REQUEST_BODY_BYTES", None)
        os.environ.pop("MAX_REQUEST_BODY_EXCLUDED_PATHS", None)

    def test_allows_small_body(self):
        client = _make_body_limit_app(max_bytes=1000)
        resp = client.post("/api/data", json={"key": "val"})
        assert resp.status_code == 200

    def test_rejects_oversized_body(self):
        client = _make_body_limit_app(max_bytes=10)
        large_payload = {"key": "a" * 200}
        resp = client.post("/api/data", json=large_payload)
        assert resp.status_code == 413

    def test_413_has_informative_message(self):
        client = _make_body_limit_app(max_bytes=10)
        resp = client.post("/api/data", json={"key": "a" * 200})
        assert "too large" in resp.json()["detail"].lower()

    def test_excluded_path_not_limited(self):
        client = _make_body_limit_app(max_bytes=10, excluded="/api/upload")
        # Even with a body that exceeds the limit, excluded path passes
        resp = client.post("/api/upload/file", content=b"x" * 200, headers={"Content-Length": "200"})
        assert resp.status_code == 200

    def test_get_request_not_limited(self):
        import os
        os.environ["MAX_REQUEST_BODY_BYTES"] = "1"
        from mozaiksai.core.transport.request_middleware import RequestBodySizeLimitMiddleware
        get_app = FastAPI()

        @get_app.get("/api/data")
        async def endpoint():
            return {"ok": True}

        get_app.add_middleware(RequestBodySizeLimitMiddleware)
        client = TestClient(get_app)
        resp = client.get("/api/data")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# RequestIDMiddleware tests
# ---------------------------------------------------------------------------

def _make_request_id_app() -> TestClient:
    from mozaiksai.core.transport.request_middleware import RequestIDMiddleware

    rid_app = FastAPI()

    @rid_app.get("/test")
    async def endpoint(request: Request):
        return {"request_id": request.state.request_id}

    rid_app.add_middleware(RequestIDMiddleware)
    return TestClient(rid_app)


class TestRequestIDMiddleware:
    def test_generates_request_id_when_absent(self):
        client = _make_request_id_app()
        resp = client.get("/test")
        assert "X-Request-ID" in resp.headers
        assert len(resp.headers["X-Request-ID"]) > 0

    def test_echoes_client_supplied_request_id(self):
        client = _make_request_id_app()
        resp = client.get("/test", headers={"X-Request-ID": "my-trace-abc-123"})
        assert resp.headers["X-Request-ID"] == "my-trace-abc-123"

    def test_ignores_invalid_client_request_id_and_generates_new(self):
        client = _make_request_id_app()
        # Contains a space — invalid
        resp = client.get("/test", headers={"X-Request-ID": "bad id here"})
        returned_id = resp.headers["X-Request-ID"]
        assert returned_id != "bad id here"
        assert len(returned_id) > 0

    def test_request_id_bound_to_request_state(self):
        client = _make_request_id_app()
        resp = client.get("/test", headers={"X-Request-ID": "trace-xyz"})
        assert resp.json()["request_id"] == "trace-xyz"

    def test_generated_id_is_unique(self):
        client = _make_request_id_app()
        id1 = client.get("/test").headers["X-Request-ID"]
        id2 = client.get("/test").headers["X-Request-ID"]
        assert id1 != id2
