"""Two-tier admin UI contract guardrails.

Verifies that OSS docs and AppGenerator guidance correctly distinguish the two
tiers of admin UI in Mozaiks:

  Tier 1 — AdminPortal schema panels
            admin_registry.yaml + modules/{module}/contracts/admin.yaml
            Panels render inside the framework-owned admin shell.

  Tier 2 — Custom operator React pages
            ui/route_manifest.json + admin/pages/{Page}.jsx + admin/index.js
            Full-page workspace-studio routes outside AdminPortal.

These tests check static content only — no imports, no side effects.
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Product-specific terms that must NOT appear in the new two-tier doc or any
# guidance added for this feature.  These are proprietary hosted-product
# identifiers, not generic OSS examples.
_PRODUCT_TERMS = (
    "MozaiksPay",
    "mozaikspay",
    "Stripe",
    "wallet",
    "investor",
    "entitlement",
    "domain provider",
)

# Neutral example names that ARE acceptable in two-tier guidance.
_NEUTRAL_EXAMPLES = (
    "audit",
    "reporting",
    "support",
    "analytics",
    "operations",
)


def _read(relative_path: str) -> str:
    return (REPO_ROOT / relative_path).read_text(encoding="utf-8")


# ── Tier doc existence and structure ──────────────────────────────────────────


def test_admin_ui_tiers_doc_exists() -> None:
    doc_path = REPO_ROOT / "docs/architecture/app/admin-ui-tiers.md"
    assert doc_path.exists(), (
        "docs/architecture/app/admin-ui-tiers.md must exist — it is the canonical "
        "two-tier admin UI contract reference"
    )


def test_admin_ui_tiers_doc_mentions_tier1_components() -> None:
    doc = _read("docs/architecture/app/admin-ui-tiers.md")
    assert "admin_registry.yaml" in doc, (
        "Tier 1 doc must reference admin_registry.yaml"
    )
    assert "contracts/admin.yaml" in doc, (
        "Tier 1 doc must reference modules/{module}/contracts/admin.yaml"
    )
    # AdminPortal or equivalent phrasing for the unified admin shell
    assert "AdminPortal" in doc or "admin shell" in doc.lower(), (
        "Tier 1 doc must mention AdminPortal or the admin shell"
    )


def test_admin_ui_tiers_doc_mentions_tier2_components() -> None:
    doc = _read("docs/architecture/app/admin-ui-tiers.md")
    assert "route_manifest.json" in doc, (
        "Tier 2 doc must reference ui/route_manifest.json"
    )
    assert "admin/pages/" in doc, (
        "Tier 2 doc must reference admin/pages/ for custom operator React pages"
    )
    assert "admin/index.js" in doc, (
        "Tier 2 doc must reference admin/index.js registration barrel"
    )


def test_admin_ui_tiers_doc_has_decision_rule() -> None:
    doc = _read("docs/architecture/app/admin-ui-tiers.md").lower()
    assert "when to use" in doc or "choosing" in doc, (
        "admin-ui-tiers.md must contain a decision rule for choosing between tiers"
    )


def test_admin_ui_tiers_doc_requires_all_three_tier2_files() -> None:
    """Doc must state that all three Tier 2 files are required together."""
    doc = _read("docs/architecture/app/admin-ui-tiers.md")
    # The three required files for Tier 2
    assert "route_manifest.json" in doc
    assert "admin/pages/" in doc
    assert "admin/index.js" in doc
    # The doc must call out that they work together
    lower = doc.lower()
    assert "required together" in lower or "all three" in lower or "three files" in lower


def test_admin_ui_tiers_doc_states_workspacelayout_for_tier2() -> None:
    doc = _read("docs/architecture/app/admin-ui-tiers.md")
    assert "WorkspaceLayout" in doc, (
        "Tier 2 doc must state that admin/pages/*.jsx must use WorkspaceLayout"
    )
    # And must warn against PageFrame
    assert "PageFrame" in doc, (
        "Tier 2 doc must warn that PageFrame must not be used in admin/pages/"
    )


def test_admin_ui_tiers_doc_uses_neutral_examples() -> None:
    doc = _read("docs/architecture/app/admin-ui-tiers.md")
    for term in _PRODUCT_TERMS:
        assert term not in doc, (
            f"admin-ui-tiers.md must not reference product-specific term: {term!r}"
        )


# ── AppGenerator agents.yaml ───────────────────────────────────────────────────


def test_appgenerator_admin_registry_agent_mentions_admin_index_js() -> None:
    """The AdminRegistryAgent two-tier note must reference admin/index.js."""
    agents = _read("factory_app/workflows/AppGenerator/agents.yaml")
    assert "admin/index.js" in agents, (
        "agents.yaml AdminRegistryAgent section must mention admin/index.js "
        "as part of the Tier 2 custom operator page contract"
    )


def test_appgenerator_admin_registry_agent_mentions_admin_pages() -> None:
    agents = _read("factory_app/workflows/AppGenerator/agents.yaml")
    assert "admin/pages/" in agents, (
        "agents.yaml must mention admin/pages/ as the Tier 2 custom operator page directory"
    )


def test_appgenerator_guidance_states_admin_registry_not_route_registry() -> None:
    agents = _read("factory_app/workflows/AppGenerator/agents.yaml")
    assert "Do NOT add full-page custom route ownership fields" in agents, (
        "agents.yaml must state that admin/admin_registry.yaml must not own "
        "full-page custom route component fields"
    )


def test_appgenerator_guidance_mentions_contracts_admin_yaml() -> None:
    agents = _read("factory_app/workflows/AppGenerator/agents.yaml")
    assert "contracts/admin.yaml" in agents, (
        "agents.yaml must reference modules/{module}/contracts/admin.yaml "
        "as the Tier 1 panel declaration file"
    )


def test_appgenerator_guidance_states_workspacelayout_for_admin_pages() -> None:
    agents = _read("factory_app/workflows/AppGenerator/agents.yaml")
    # WorkspaceLayout already appears in agents.yaml for admin/pages/ chrome rule
    assert "WorkspaceLayout" in agents
    assert "admin/pages/" in agents


# ── Canonical doc cross-references ────────────────────────────────────────────


def test_assembly_contract_states_admin_registry_not_a_route_registry() -> None:
    doc = _read("docs/architecture/builder/appgenerator-output-assembly-contract.md")
    assert "admin/admin_registry.yaml" in doc
    assert "not a route registry" in doc or "must not own full-page" in doc, (
        "appgenerator-output-assembly-contract.md must state that "
        "admin_registry.yaml is not a route registry"
    )


def test_admin_system_doc_covers_panel_rendering() -> None:
    doc = _read("docs/architecture/app/admin-system.md")
    assert "contracts/admin.yaml" in doc
    assert "renderer" in doc


def test_canonical_app_structure_lists_both_tier_files() -> None:
    doc = _read("docs/architecture/app/canonical-app-structure.md")
    # Tier 1 file
    assert "admin_registry.yaml" in doc
    # Tier 2 files
    assert "admin/index.js" in doc or "admin/pages/" in doc


# ── No product-specific terms in the new doc ──────────────────────────────────


def test_admin_ui_tiers_doc_no_product_specific_examples() -> None:
    """New doc must use only generic/neutral operator surface examples."""
    doc = _read("docs/architecture/app/admin-ui-tiers.md")
    for term in _PRODUCT_TERMS:
        assert term not in doc, (
            f"admin-ui-tiers.md introduced a product-specific term: {term!r}. "
            f"Use neutral names such as: {', '.join(_NEUTRAL_EXAMPLES)}"
        )

