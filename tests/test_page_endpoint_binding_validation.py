"""Raw page endpoint validation reports malformed metadata without hashing it."""
from __future__ import annotations

import pytest

from mozaiksai.core.runtime.app.page_schema import validate_page_endpoint_bindings


def _check(document, contract):
    return validate_page_endpoint_bindings(
        document, action_index={"inventory": frozenset({"list"})},
        action_contracts={"inventory/list": contract},
    )


def _public_page():
    return {"sections": [{"config": {"api_endpoint": "/api/modules/inventory/list"}}]}


@pytest.mark.parametrize("contract", [{}, {"api_surface": None}, {"api_surface": "public"},
                                      {"api_surface": "public_readonly"}])
def test_public_binding_preserves_default_and_explicit_public_contracts(contract):
    assert _check(_public_page(), contract) == []


@pytest.mark.parametrize("surface", [[], ["public"], {}, {"surface": "public"}, False, 7,
                                    "", "unknown", "internal", "admin_internal"])
def test_public_binding_rejects_nonpublic_or_malformed_surfaces(surface):
    diagnostics = _check(_public_page(), {"api_surface": surface})
    assert [item.code for item in diagnostics] == ["page_schema.ineligible_action_binding"]
    assert diagnostics[0].location == "$.sections[0].config.api_endpoint"


@pytest.mark.parametrize("action_type,eligible", [
    ("submit", True), ("delete", True), (None, False), (False, False), (7, False),
    ([], False), (["submit"], False), ({}, False), ({"type": "delete"}, False),
    ("", False), ("navigate", False),
])
def test_explicit_admin_binding_handles_raw_action_types(action_type, eligible):
    page = {"sections": [{"config": {"actions": [{
        "action_type": action_type, "href": "/api/admin/modules/inventory/list",
    }]}}]}
    diagnostics = _check(page, {"api_surface": "admin_internal"})
    assert [item.code for item in diagnostics] == ([] if eligible else ["page_schema.ineligible_action_binding"])
