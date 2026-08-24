"""Cross-language contract for the module-dispatch error envelope.

Three tests in this repo asserted three different shapes for the same
response, and all three were green:

- ``tests/test_generated_app_archetype_matrix.py`` asserted
  ``denied.json()["detail"]["error_code"]`` — correct, and passing.
- the generated-app client template read ``body.error_code`` flat, so
  ``isEntitlementRequiredError`` returned false for every real denial and a
  user saw ``"Module action failed: ... 402"`` instead of an upgrade prompt.
- the Playwright spec mocked a *third* shape — flat body **and** status 403 —
  so the browser test certified a response the backend has never produced.

A mock that encodes the wrong shape is worse than no test: it spends
confidence without buying any. This module makes the wire shape a single
checked-in fixture, proves the fixture matches what FastAPI actually
serializes from the router's own construction, and the Playwright spec reads
that same file rather than hand-rolling a body.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from mozaiksai.core.runtime.composition.module_executor import ModuleResult
from mozaiksai.hosts import platform as platform_host

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PATH = REPO_ROOT / "tests" / "fixtures" / "module_error_envelope.json"


def _fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _entitlement_case() -> dict:
    return _fixture()["entitlement_required"]


def test_platform_router_produces_the_checked_in_entitlement_envelope(monkeypatch) -> None:
    """Exercise the real router and compare its wire response to the fixture."""
    case = _entitlement_case()

    class _DenyingExecutor:
        async def execute(self, request, context=None):
            return ModuleResult(
                success=False,
                error=case["body"]["detail"]["error"],
                error_code="ENTITLEMENT_REQUIRED",
            )

    monkeypatch.setenv("AUTH_ENABLED", "false")
    platform_host.app.state.failed_module_names = []
    platform_host.app.state.module_action_surfaces = {}
    platform_host.app.state.executor_registry = SimpleNamespace(
        module_executor=_DenyingExecutor()
    )
    response = TestClient(
        platform_host.app, raise_server_exceptions=False
    ).post("/api/modules/premium_reports/generate_report", json={})

    assert response.status_code == case["status"]
    assert response.json() == case["body"]
    assert "error_code" not in response.json(), "structured fields are never top-level"


def test_entitlement_required_maps_to_402_in_the_router() -> None:
    """403 would be a plausible guess; the router uses 402.

    The Playwright mock guessed 403, which is why a status-based check would
    have failed there too.
    """
    assert _entitlement_case()["status"] == 402


@pytest.mark.parametrize(
    "path",
    [
        "factory_app/workflows/AppGenerator/tools/module_api_template.py",
        "web_shell/playwright/generated-ui/entitlement-upgrade.spec.js",
    ],
)
def test_js_consumers_do_not_read_structured_fields_off_the_top_level(path: str) -> None:
    """Guard the specific regression: ``body.error_code`` with no unwrap.

    Reading ``body?.error_code`` directly is the defect. The fix reads it from
    an unwrapped payload, so the literal ``body?.error_code`` and
    ``body.error_code`` forms must not reappear.
    """
    source = (REPO_ROOT / path).read_text(encoding="utf-8")
    for bad in ("body?.error_code", "body.error_code", "body?.code"):
        assert bad not in source, (
            f"{path} reads {bad} — FastAPI nests structured fields under "
            "'detail'. Unwrap first: const payload = body?.detail ?? body"
        )


# ---------------------------------------------------------------------------
# The Playwright fixture app carries a hand-maintained copy of the template's
# client. Its header says "Keep in sync with module_api_template.py" — a
# convention nothing enforced, so the browser tests could exercise different
# code than generated apps ship.
# ---------------------------------------------------------------------------

FIXTURE_CLIENT = (
    REPO_ROOT / "web_shell" / "playwright" / "fixtures" / "generated-app"
    / "app" / "ui" / "lib" / "moduleApi.js"
)
TEMPLATE = REPO_ROOT / "factory_app" / "workflows" / "AppGenerator" / "tools" / "module_api_template.py"

# Functions whose behavior the browser tests depend on. Compared body-for-body
# rather than whole-file, because the fixture legitimately omits helpers the
# fixture app does not use.
SHARED_FUNCTIONS = ("parseErrorPayload", "isEntitlementRequiredError", "moduleAction")


def _js_function_body(source: str, name: str) -> str:
    """Extract `export function NAME(...)`/`export async function NAME(...)` body."""
    for prefix in (
        f"export async function {name}(",
        f"export function {name}(",
        f"function {name}(",
    ):
        start = source.find(prefix)
        if start != -1:
            break
    else:  # pragma: no cover - guarded by the test below
        raise AssertionError(f"{name} not found")

    depth, i, started = 0, start, False
    while i < len(source):
        if source[i] == "{":
            depth += 1
            started = True
        elif source[i] == "}":
            depth -= 1
            if started and depth == 0:
                return " ".join(source[start : i + 1].split())
        i += 1
    raise AssertionError(f"unbalanced braces reading {name}")


@pytest.mark.parametrize("function_name", SHARED_FUNCTIONS)
def test_playwright_fixture_client_matches_the_template(function_name: str) -> None:
    """The browser tests must exercise the code generated apps actually ship.

    Without this, the fixture copy silently drifts and Playwright certifies
    behavior no real app has.
    """
    template_body = _js_function_body(TEMPLATE.read_text(encoding="utf-8"), function_name)
    fixture_body = _js_function_body(FIXTURE_CLIENT.read_text(encoding="utf-8"), function_name)
    assert fixture_body == template_body, (
        f"{FIXTURE_CLIENT.relative_to(REPO_ROOT).as_posix()} has drifted from "
        f"module_api_template.py in {function_name}(). The Playwright suite would "
        "test code that no generated app ships. Copy the template's version across."
    )
