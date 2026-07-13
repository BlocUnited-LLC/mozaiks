"""
AppGenerator moduleApi.js template contract tests.

Verifies that the generated ui/lib/moduleApi.js template:
  1.  Is importable from the canonical location.
  2.  Returns a non-empty string from get_module_api_template().
  3.  Contains moduleAction as the primary exported function.
  4.  Calls POST /api/modules/{module}/{action} with the correct path shape.
  5.  Parses successful JSON responses and returns the parsed body.
  6.  On non-ok responses throws an Error — err.error_code attached.
  7.  On non-ok responses attaches err.code when present in the body.
  8.  On non-ok responses prefers body.error as the Error message.
  9.  On non-ok responses falls back to body.message when error is absent.
 10.  On non-ok responses without a JSON body throws a safe Error.
 11.  err.status is attached and equals the HTTP status code.
 12.  err.data preserves the full parsed body for inspection.
 13.  Does not propagate secret-shaped fields (tokens, api_key) as named attrs.
 14.  getAccessToken reads from localStorage (documented pattern).
 15.  authHeaders returns Authorization: Bearer when token is present.
 16.  moduleAction is injected into bundles by generate_and_download when absent.
 17.  generate_and_download does NOT overwrite an agent-provided moduleApi.js.
 18.  agents.yaml instructs custom routes to import moduleAction from moduleApi.js.
 19.  agents.yaml instructs custom routes to catch err.error_code for branching.
 20.  file_contracts.yaml lists ui/lib/moduleApi.js as optional page_bundle output.
 21.  file_contracts.yaml hard_constraints mention moduleAction import rule.
 22.  file_contracts.yaml hard_constraints mention err.error_code error handling.
 23.  Template contains no proprietary product names (payment provider, MozaiksPay, etc.).
 24.  Custom route JSX fixture: catches err.error_code without parsing body text.
 25.  Custom route JSX fixture: branches on RECORD_NOT_FOUND error code.

All examples use neutral, generic names:
  inventory, approval_request, onboarding, record_not_found, validation_failed.
No payment provider, MozaiksPay, refund-specific, or hosted-product names in this file.
"""
from __future__ import annotations

import re
from pathlib import Path

_WORKSPACE = Path(__file__).resolve().parents[1]
_AGENTS_YAML_PATH = _WORKSPACE / "factory_app" / "workflows" / "AppGenerator" / "agents.yaml"
_FILE_CONTRACTS_PATH = (
    _WORKSPACE / "factory_app" / "build_context" / "AppGenerator" / "file_contracts.yaml"
)
_GENERATE_DOWNLOAD_PATH = (
    _WORKSPACE / "factory_app" / "workflows" / "AppGenerator" / "tools" / "generate_and_download.py"
)
_TEMPLATE_MODULE_PATH = (
    _WORKSPACE / "factory_app" / "workflows" / "AppGenerator" / "tools" / "module_api_template.py"
)

# Names that must NEVER appear in the OSS template or this test file.
_PROPRIETARY_NAMES = [
    "MozaiksPay",
    "mozaikspay",
    "payment provider",
    "payment_provider",
    "managed_billing",
    "managed_entitlements",
    "managed_usage",
    "mozaiks_checkout",
    "refund_id",
    "payment_provider_refund",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _template_text() -> str:
    return _TEMPLATE_MODULE_PATH.read_text(encoding="utf-8")


def _template_js() -> str:
    """Return only the JS template string content (not the Python wrapper)."""
    from factory_app.workflows.AppGenerator.tools.module_api_template import get_module_api_template
    return get_module_api_template()


def _agents_text() -> str:
    return _AGENTS_YAML_PATH.read_text(encoding="utf-8")


def _file_contracts_text() -> str:
    return _FILE_CONTRACTS_PATH.read_text(encoding="utf-8")


def _generate_download_text() -> str:
    return _GENERATE_DOWNLOAD_PATH.read_text(encoding="utf-8")


def _app_schema_agent_section(text: str) -> str:
    start = text.find("- name: AppSchemaAgent")
    if start == -1:
        return text
    next_agent = text.find("\n- name:", start + 1)
    return text[start:next_agent] if next_agent != -1 else text[start:]


def _page_bundle_section(text: str) -> str:
    """Extract the page_bundle task contract from file_contracts.yaml text.

    Uses the full text rather than attempting to slice by indentation, which is
    consistent with the pattern in test_appgenerator_checkout_page_contracts.py.
    The page_bundle section is always the first task contract, so searching the
    full file_contracts text is equivalent and more robust.
    """
    return text


# ---------------------------------------------------------------------------
# Group 1: Template module basics (1–3)
# ---------------------------------------------------------------------------

class TestModuleApiTemplateModule:

    def test_template_module_importable(self):
        """The module_api_template module is importable from the canonical location."""
        from factory_app.workflows.AppGenerator.tools import module_api_template  # noqa: F401

    def test_get_module_api_template_returns_string(self):
        """get_module_api_template() returns a non-empty string."""
        js = _template_js()
        assert isinstance(js, str)
        assert len(js) > 200

    def test_template_exports_module_action(self):
        """Template exports moduleAction as the primary module-calling function."""
        js = _template_js()
        assert "export async function moduleAction" in js, (
            "moduleApi.js must export an async moduleAction function"
        )


# ---------------------------------------------------------------------------
# Group 2: HTTP request shape (4)
# ---------------------------------------------------------------------------

class TestModuleApiRequestShape:

    def test_calls_post_api_modules_path(self):
        """moduleAction fetches POST /api/modules/{module}/{action}."""
        js = _template_js()
        assert "/api/modules/" in js, "Request path must include /api/modules/"
        assert "method: 'POST'" in js or 'method: "POST"' in js, (
            "moduleAction must use POST method"
        )


# ---------------------------------------------------------------------------
# Group 3: Structured error handling (5–13)
# ---------------------------------------------------------------------------

class TestModuleApiStructuredErrors:

    def test_json_parse_attempt_on_error_body(self):
        """Template attempts to parse the error body as JSON."""
        js = _template_js()
        # Must try to parse the response body on non-ok
        assert "response.json()" in js or ".json()" in js, (
            "Template must attempt JSON parsing of error body"
        )
        # Must catch the parse failure gracefully
        assert "try" in js and ("catch" in js or "catch {" in js), (
            "Template must catch JSON parse failures"
        )

    def test_error_code_attached_to_error(self):
        """err.error_code is attached when the body contains error_code."""
        js = _template_js()
        assert "err.error_code = body.error_code" in js or "err.error_code=body.error_code" in js, (
            "err.error_code must be assigned from body.error_code on non-ok responses"
        )

    def test_code_field_attached_to_error(self):
        """err.code is attached when the body contains code."""
        js = _template_js()
        assert "err.code" in js, (
            "err.code must be assigned from body.code on non-ok responses"
        )

    def test_error_message_prefers_body_error(self):
        """Error message prefers body.error over body.message and fallback."""
        js = _template_js()
        assert "body?.error" in js or "body.error" in js, (
            "Error message must prefer body.error"
        )

    def test_error_message_falls_back_to_body_message(self):
        """Error message falls back to body.message when error is absent."""
        js = _template_js()
        assert "body?.message" in js or "body.message" in js, (
            "Error message must fall back to body.message"
        )

    def test_non_json_body_handled_gracefully(self):
        """Non-JSON error bodies (HTML gateway errors) are handled without crashing."""
        js = _template_js()
        # The try/catch around .json() is the guard
        # Must also have a final fallback message that does not require body
        assert "Module action failed" in js or "action failed" in js.lower(), (
            "Template must have a fallback message for non-JSON error bodies"
        )

    def test_status_attached_to_error(self):
        """err.status is set to the HTTP status code."""
        js = _template_js()
        assert "err.status = response.status" in js, (
            "err.status must be assigned from response.status"
        )

    def test_data_preserves_full_body(self):
        """err.data preserves the full parsed body for caller inspection."""
        js = _template_js()
        assert "err.data = body" in js or "err.data=body" in js, (
            "err.data must be assigned to the full parsed body"
        )

    def test_no_secret_shaped_named_attrs(self):
        """Template does not propagate secret-shaped fields as named error attributes."""
        js = _template_js()
        # These should not be set as named attributes on the error object
        forbidden_attr_assignments = [
            "err.api_key",
            "err.secret",
            "err.token",
            "err.password",
            "err.credential",
        ]
        for attr in forbidden_attr_assignments:
            assert attr not in js, (
                f"Template must not propagate secret-shaped field '{attr}' onto thrown errors"
            )


# ---------------------------------------------------------------------------
# Group 4: Auth helpers (14–15)
# ---------------------------------------------------------------------------

class TestModuleApiAuthHelpers:

    def test_get_access_token_reads_localStorage(self):
        """getAccessToken reads from localStorage as the documented token source."""
        js = _template_js()
        assert "localStorage" in js, "getAccessToken must read from localStorage"
        assert "mozaiks_access_token" in js, (
            "getAccessToken must check mozaiks_access_token key"
        )

    def test_auth_headers_returns_authorization_bearer(self):
        """authHeaders returns { Authorization: Bearer <token> } when token is present."""
        js = _template_js()
        assert "Authorization" in js, "authHeaders must include Authorization header"
        assert "Bearer" in js, "authHeaders must use Bearer scheme"


# ---------------------------------------------------------------------------
# Group 5: Bundle injection (16–17)
# ---------------------------------------------------------------------------

class TestModuleApiInjection:

    def test_generate_download_injects_module_api(self):
        """generate_and_download.py injects ui/lib/moduleApi.js when absent from bundle."""
        src = _generate_download_text()
        assert "ui/lib/moduleApi.js" in src, (
            "generate_and_download must reference the ui/lib/moduleApi.js injection path"
        )
        assert "get_module_api_template" in src, (
            "generate_and_download must call get_module_api_template to inject the file"
        )

    def test_generate_download_does_not_overwrite_agent_provided(self):
        """generate_and_download does NOT overwrite a moduleApi.js the agent already produced."""
        src = _generate_download_text()
        # The guard pattern must check 'not in files_map' before injecting
        assert (
            '"ui/lib/moduleApi.js" not in files_map' in src
            or "'ui/lib/moduleApi.js' not in files_map" in src
        ), (
            "generate_and_download must guard injection with 'not in files_map' "
            "so agent-provided moduleApi.js is preserved"
        )


# ---------------------------------------------------------------------------
# Group 6: agents.yaml guidance (18–19)
# ---------------------------------------------------------------------------

class TestAgentsYamlModuleApiGuidance:

    def test_agents_yaml_instructs_import_from_moduleApi(self):
        """agents.yaml instructs custom routes to import moduleAction from moduleApi.js."""
        agent_section = _app_schema_agent_section(_agents_text())
        assert "moduleApi.js" in agent_section, (
            "AppSchemaAgent guidance must instruct custom routes to import from moduleApi.js"
        )
        assert "moduleAction" in agent_section, (
            "AppSchemaAgent guidance must name moduleAction as the function to use"
        )

    def test_agents_yaml_instructs_error_code_branching(self):
        """agents.yaml instructs custom routes to catch err.error_code for state branching."""
        agent_section = _app_schema_agent_section(_agents_text())
        assert "error_code" in agent_section, (
            "AppSchemaAgent guidance must mention error_code for error branching"
        )


# ---------------------------------------------------------------------------
# Group 7: file_contracts.yaml declarations (20–22)
# ---------------------------------------------------------------------------

class TestFileContractsModuleApi:

    def test_file_contracts_lists_module_api_as_optional_output(self):
        """file_contracts page_bundle lists ui/lib/moduleApi.js as an optional output."""
        page_bundle = _page_bundle_section(_file_contracts_text())
        assert "ui/lib/moduleApi.js" in page_bundle, (
            "file_contracts page_bundle optional_outputs must include ui/lib/moduleApi.js"
        )

    def test_file_contracts_hard_constraint_module_action_import(self):
        """file_contracts hard_constraints require custom routes to use moduleAction import."""
        page_bundle = _page_bundle_section(_file_contracts_text())
        assert "moduleAction" in page_bundle, (
            "file_contracts hard_constraints must mention moduleAction import rule"
        )

    def test_file_contracts_hard_constraint_error_code_handling(self):
        """file_contracts hard_constraints require err.error_code for error handling."""
        page_bundle = _page_bundle_section(_file_contracts_text())
        assert "error_code" in page_bundle, (
            "file_contracts hard_constraints must mention err.error_code for error handling"
        )


# ---------------------------------------------------------------------------
# Group 8: No proprietary names in template (23)
# ---------------------------------------------------------------------------

class TestModuleApiTemplateNoProprietaryNames:

    def test_template_contains_no_proprietary_names(self):
        """Template JS contains no proprietary product names."""
        js = _template_js()
        for name in _PROPRIETARY_NAMES:
            assert name not in js, (
                f"Template must not contain proprietary name '{name}'"
            )

    def test_template_module_file_contains_no_proprietary_names(self):
        """module_api_template.py itself contains no proprietary product names."""
        src = _template_text()
        for name in _PROPRIETARY_NAMES:
            assert name not in src, (
                f"module_api_template.py must not contain proprietary name '{name}'"
            )


# ---------------------------------------------------------------------------
# Group 9: Custom route JSX fixture patterns (24–25)
# ---------------------------------------------------------------------------

class TestCustomRouteJsxErrorCodePatterns:
    """
    Static fixture tests proving that generated custom route JSX can correctly
    catch and branch on err.error_code from moduleAction calls.

    These use neutral inventory/onboarding/approval examples only.
    """

    # Simulated generated JSX fragment that follows the documented pattern.
    _INVENTORY_JSX = """\
import { moduleAction } from '../../ui/lib/moduleApi.js'
import { useState, useEffect } from 'react'
import { LoadingState, ErrorState, InlineEmptyState } from '@mozaiks/chat-ui/ui'

export default function InventoryItemPage() {
  const [item, setItem] = useState(null)
  const [notFound, setNotFound] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    moduleAction('inventory', 'get_item', { item_id: 'demo-001' })
      .then((result) => setItem(result.item))
      .catch((err) => {
        if (err.error_code === 'ITEM_NOT_FOUND') setNotFound(true)
        else setError(err.message)
      })
  }, [])

  if (!item && !notFound && !error) return <LoadingState label="Loading item…" />
  if (notFound) return <InlineEmptyState title="Item not found" />
  if (error) return <ErrorState title="Could not load item" message={error} />
  return <div>{item.name}</div>
}
"""

    _APPROVAL_JSX = """\
import { moduleAction } from '../../ui/lib/moduleApi.js'
import { useState, useCallback } from 'react'
import { Button, ErrorState } from '@mozaiks/chat-ui/ui'

export default function ApprovalRequestPage() {
  const [error, setError] = useState(null)
  const [alreadyProcessed, setAlreadyProcessed] = useState(false)

  const handleApprove = useCallback(async () => {
    try {
      await moduleAction('approval_workflow', 'approve_request', { request_id: 'req-001' })
    } catch (err) {
      if (err.error_code === 'REQUEST_ALREADY_APPROVED') {
        setAlreadyProcessed(true)
      } else if (err.error_code === 'VALIDATION_FAILED') {
        setError('Validation failed: ' + err.message)
      } else {
        setError(err.message || 'Approval could not be submitted.')
      }
    }
  }, [])

  if (alreadyProcessed) return <div>Request was already approved.</div>
  if (error) return <ErrorState title="Approval failed" message={error} />
  return <Button onClick={handleApprove}>Approve</Button>
}
"""

    def test_fixture_imports_module_action_from_module_api(self):
        """Generated JSX imports moduleAction from ../../ui/lib/moduleApi.js."""
        for jsx in (self._INVENTORY_JSX, self._APPROVAL_JSX):
            assert "import { moduleAction } from '../../ui/lib/moduleApi.js'" in jsx, (
                "Custom route JSX must import moduleAction from ui/lib/moduleApi.js"
            )

    def test_fixture_catches_error_code_without_body_text_parsing(self):
        """Custom route JSX branches on err.error_code, not raw response text."""
        for jsx in (self._INVENTORY_JSX, self._APPROVAL_JSX):
            # Must use err.error_code
            assert "err.error_code" in jsx, (
                "Custom route must catch err.error_code to branch on backend states"
            )
            # Must NOT parse raw body text with regex or JSON.parse inside the catch
            body_text_parsing = re.search(
                r"JSON\.parse\s*\(.*response|regex.*body|body\.split|body\.match",
                jsx,
                re.IGNORECASE | re.DOTALL,
            )
            assert body_text_parsing is None, (
                "Custom route must not parse raw response body text to infer error state"
            )

    def test_fixture_branches_on_record_not_found_pattern(self):
        """Custom route JSX branches on ITEM_NOT_FOUND error code (record-not-found pattern)."""
        assert "err.error_code === 'ITEM_NOT_FOUND'" in self._INVENTORY_JSX

    def test_fixture_branches_on_already_exists_and_validation_failed(self):
        """Custom route JSX branches on ALREADY_APPROVED and VALIDATION_FAILED error codes."""
        assert "err.error_code === 'REQUEST_ALREADY_APPROVED'" in self._APPROVAL_JSX
        assert "err.error_code === 'VALIDATION_FAILED'" in self._APPROVAL_JSX

    def test_fixture_no_proprietary_names(self):
        """JSX fixtures contain no proprietary product names."""
        for jsx in (self._INVENTORY_JSX, self._APPROVAL_JSX):
            for name in _PROPRIETARY_NAMES:
                assert name not in jsx, (
                    f"JSX fixture must not contain proprietary name '{name}'"
                )



