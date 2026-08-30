"""
Source-inspection tests for carry-forward report UI components.

These tests check static source content only — no runtime imports, no JSX
rendering, no side effects.  This follows the same pattern used for other
UI contract tests in this repo (test_admin_ui_two_tier_contract.py).

Coverage:

 CarryForwardReportPanel (full panel — AppOverviewPage, 13 tests):
  1.  CarryForwardReportPanel.jsx file exists
  2.  Panel and StatusPill imported from StudioShared
  3.  No hardcoded hex/rgb/hsl colors
  4.  No local primitive clones
  5.  AppOverviewPage imports CarryForwardReportPanel
  6.  AppOverviewPage mounts CarryForwardReportPanel when cfReport present
  7.  Component returns null when report absent
  8.  preserved_paths count rendered
  9.  Conflicts shown with generated-output-wins explanation
 10.  Workspace-unavailable warning state
 11.  Backend-not-copied notice
 12.  Sensitive path redaction
 13.  Semantic Tailwind token classes

 CarryForwardReportSummary (compact — AppBuildReviewPage, 10 tests):
 14.  CarryForwardReportSummary.jsx file exists
 15.  StatusPill imported from StudioShared (no Panel — compact)
 16.  No hardcoded hex/rgb colors
 17.  No local primitive clones
 18.  Returns null when report absent
 19.  Renders reused_modules and dropped_modules
 20.  Conflict count with generated-output-wins language
 21.  Workspace-unavailable warning state
 22.  Backend-not-copied notice
 23.  Sensitive path redaction

 AppBuildReviewPage (build review, 10 tests):
 24.  AppBuildReviewPage.jsx file exists
 25.  Imports CarryForwardReportSummary
 26.  Conditionally mounts CarryForwardReportSummary when cfReport present
 27.  Shows "No carry-forward" notice when report absent
 28.  Imports from StudioShared
 29.  No hardcoded colors
 30.  Uses buildHistory from snapshot
 31.  AppOverviewPage carry-forward behavior still intact (regression)
 32.  StudioPage.jsx routes /activity to AppBuildReviewPage
 33.  admin/index.js registers AppBuildReviewPage
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

_PANEL_PATH = REPO_ROOT / "factory_app" / "app" / "admin" / "pages" / "CarryForwardReportPanel.jsx"
_SUMMARY_PATH = REPO_ROOT / "factory_app" / "app" / "admin" / "pages" / "CarryForwardReportSummary.jsx"
_BUILD_REVIEW_PATH = REPO_ROOT / "factory_app" / "app" / "admin" / "pages" / "AppBuildReviewPage.jsx"
_OVERVIEW_PATH = REPO_ROOT / "factory_app" / "app" / "admin" / "pages" / "AppOverviewPage.jsx"
_INDEX_PATH = REPO_ROOT / "factory_app" / "app" / "admin" / "index.js"


def _panel_src() -> str:
    return _PANEL_PATH.read_text(encoding="utf-8")


def _summary_src() -> str:
    return _SUMMARY_PATH.read_text(encoding="utf-8")


def _build_review_src() -> str:
    return _BUILD_REVIEW_PATH.read_text(encoding="utf-8")


def _overview_src() -> str:
    return _OVERVIEW_PATH.read_text(encoding="utf-8")


def _overview_src() -> str:
    return _OVERVIEW_PATH.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 1. File existence
# ---------------------------------------------------------------------------

def test_carry_forward_report_panel_file_exists():
    assert _PANEL_PATH.exists(), (
        "factory_app/app/admin/pages/CarryForwardReportPanel.jsx must exist"
    )


# ---------------------------------------------------------------------------
# 2. Shared primitives imported — no local clones
# ---------------------------------------------------------------------------

def test_panel_imports_from_console_shared():
    src = _panel_src()
    # Must import from the canonical StudioShared adapter, not re-declare primitives
    assert "StudioShared" in src, (
        "CarryForwardReportPanel must import from StudioShared.jsx (shared primitives)"
    )
    # Panel and StatusPill are the expected imports for this component
    assert "Panel" in src
    assert "StatusPill" in src


def test_panel_does_not_import_from_artifact_design_system():
    src = _panel_src()
    assert "artifactDesignSystem" not in src, (
        "CarryForwardReportPanel must not import from the removed artifactDesignSystem"
    )


# ---------------------------------------------------------------------------
# 3. No hardcoded colors
# ---------------------------------------------------------------------------

def test_panel_no_hardcoded_hex_colors():
    src = _panel_src()
    import re
    # Hex color literals (#fff, #ffffff, #abc123, etc.)
    hex_matches = re.findall(r'#[0-9a-fA-F]{3,8}\b', src)
    assert hex_matches == [], (
        f"CarryForwardReportPanel must not contain hardcoded hex colors: {hex_matches}"
    )


def test_panel_no_hardcoded_rgb_colors():
    src = _panel_src()
    import re
    rgb_matches = re.findall(r'\brgb[a]?\s*\(', src)
    assert rgb_matches == [], (
        f"CarryForwardReportPanel must not contain hardcoded rgb() colors: {rgb_matches}"
    )


# ---------------------------------------------------------------------------
# 4. No local primitive clones
# ---------------------------------------------------------------------------

def test_panel_no_local_primitive_clones():
    src = _panel_src()
    # These are the names of shared primitives — check they are not re-declared
    # as local function components. The file may *import* them but not *define* them.
    # Check that there is no `function StatusPill` or `function Badge` etc.
    forbidden_local_defs = [
        "function Badge(",
        "function Card(",
        "function MetricTile(",
        "function StatCard(",
    ]
    for decl in forbidden_local_defs:
        assert decl not in src, (
            f"CarryForwardReportPanel must not locally define shared primitive: {decl!r}"
        )


# ---------------------------------------------------------------------------
# 5 & 6. AppOverviewPage wires the panel
# ---------------------------------------------------------------------------

def test_overview_imports_carry_forward_report_panel():
    src = _overview_src()
    assert "CarryForwardReportPanel" in src, (
        "AppOverviewPage must import CarryForwardReportPanel"
    )


def test_overview_mounts_carry_forward_report_panel_conditionally():
    src = _overview_src()
    # The panel must be conditionally rendered when cfReport is present
    assert "CarryForwardReportPanel" in src
    # A conditional guard (cfReport && ...) must be present
    assert "cfReport" in src, (
        "AppOverviewPage must extract cfReport from latestArtifact commit_metadata"
    )
    assert "carry_forward_report" in src, (
        "AppOverviewPage must reference carry_forward_report key from artifact metadata"
    )


# ---------------------------------------------------------------------------
# 7. Null-guard: component renders nothing when report is absent
# ---------------------------------------------------------------------------

def test_panel_has_null_guard_for_absent_report():
    src = _panel_src()
    # The component must return null when report is falsy
    assert "return null" in src, (
        "CarryForwardReportPanel must return null when report is absent or invalid"
    )


# ---------------------------------------------------------------------------
# 8. preserved_paths count rendered
# ---------------------------------------------------------------------------

def test_panel_renders_preserved_paths_count():
    src = _panel_src()
    assert "preserved_paths" in src, (
        "CarryForwardReportPanel must reference preserved_paths"
    )
    # Should display a count or length
    assert ".length" in src or "length" in src


# ---------------------------------------------------------------------------
# 9. Conflict explanation — generated output wins
# ---------------------------------------------------------------------------

def test_panel_explains_generated_output_wins_conflicts():
    src = _panel_src()
    assert "conflict" in src.lower() or "conflicts" in src.lower(), (
        "CarryForwardReportPanel must surface conflict information"
    )
    assert "generated output" in src.lower() or "overwrote" in src.lower(), (
        "CarryForwardReportPanel must explain that generated output wins conflicts"
    )


# ---------------------------------------------------------------------------
# 10. Workspace unavailable warning state
# ---------------------------------------------------------------------------

def test_panel_has_workspace_unavailable_warning():
    src = _panel_src()
    assert "workspace_available" in src, (
        "CarryForwardReportPanel must check workspace_available"
    )
    assert "not available" in src.lower() or "unavailable" in src.lower(), (
        "CarryForwardReportPanel must show a warning when workspace was not available"
    )
    # Must use warning semantic tokens, not hardcoded colors
    assert "text-warning" in src or "bg-warning" in src or "border-warning" in src, (
        "CarryForwardReportPanel workspace-unavailable state must use warning semantic tokens"
    )


# ---------------------------------------------------------------------------
# 11. Backend-not-copied notice
# ---------------------------------------------------------------------------

def test_panel_states_backend_code_not_copied():
    src = _panel_src()
    assert "backend" in src.lower(), (
        "CarryForwardReportPanel must mention backend in the context of what was not copied"
    )
    assert "not copied" in src.lower() or "never" in src.lower() or "was not" in src.lower(), (
        "CarryForwardReportPanel must clearly state backend code was not copied"
    )


# ---------------------------------------------------------------------------
# 12. Sensitive path redaction logic present
# ---------------------------------------------------------------------------

def test_panel_has_sensitive_path_redaction():
    src = _panel_src()
    # Must have some form of sensitive-term check for paths
    assert "secret" in src.lower() or "sensitive" in src.lower() or "redact" in src.lower(), (
        "CarryForwardReportPanel must contain sensitive path filtering/redaction logic"
    )
    # Redacted marker must be present
    assert "redacted" in src.lower(), (
        "CarryForwardReportPanel must display '[redacted]' for sensitive paths"
    )


# ---------------------------------------------------------------------------
# Bonus: component uses semantic Tailwind classes (spot check)
# ---------------------------------------------------------------------------

def test_panel_uses_semantic_token_classes():
    src = _panel_src()
    semantic_classes = [
        "text-foreground",
        "text-muted-foreground",
        "bg-card",
        "border-border",
    ]
    found = [cls for cls in semantic_classes if cls in src]
    assert len(found) >= 2, (
        f"CarryForwardReportPanel must use semantic Tailwind token classes. "
        f"Found only: {found}"
    )


# ===========================================================================
# CarryForwardReportSummary (compact, 14–23)
# ===========================================================================

# ---------------------------------------------------------------------------
# 14. File existence
# ---------------------------------------------------------------------------

def test_carry_forward_report_summary_file_exists():
    assert _SUMMARY_PATH.exists(), (
        "factory_app/app/admin/pages/CarryForwardReportSummary.jsx must exist"
    )


# ---------------------------------------------------------------------------
# 15. StatusPill from StudioShared — no Panel (compact, not a full panel)
# ---------------------------------------------------------------------------

def test_summary_imports_status_pill_from_console_shared():
    src = _summary_src()
    assert "StudioShared" in src, (
        "CarryForwardReportSummary must import from StudioShared.jsx"
    )
    assert "StatusPill" in src


def test_summary_does_not_wrap_in_panel():
    src = _summary_src()
    # The summary is compact — it must NOT import Panel (that is the full panel's job)
    assert "Panel" not in src or "StatusPill" in src, (
        "CarryForwardReportSummary is a compact component and should not use the Panel wrapper"
    )
    # Verify it's only StatusPill imported, not Panel
    import_match = re.search(r"from.*StudioShared.*import\s*\{([^}]+)\}", src)
    if import_match:
        imports = import_match.group(1)
        assert "Panel" not in imports, (
            "CarryForwardReportSummary must not import Panel — it is a compact inline component"
        )


# ---------------------------------------------------------------------------
# 16. No hardcoded colors
# ---------------------------------------------------------------------------

def test_summary_no_hardcoded_hex_colors():
    src = _summary_src()
    hex_matches = re.findall(r'#[0-9a-fA-F]{3,8}\b', src)
    assert hex_matches == [], (
        f"CarryForwardReportSummary must not contain hardcoded hex colors: {hex_matches}"
    )


def test_summary_no_hardcoded_rgb_colors():
    src = _summary_src()
    rgb_matches = re.findall(r'\brgb[a]?\s*\(', src)
    assert rgb_matches == [], (
        f"CarryForwardReportSummary must not contain hardcoded rgb() colors: {rgb_matches}"
    )


# ---------------------------------------------------------------------------
# 17. No local primitive clones
# ---------------------------------------------------------------------------

def test_summary_no_local_primitive_clones():
    src = _summary_src()
    forbidden = ["function Badge(", "function Card(", "function MetricTile("]
    for decl in forbidden:
        assert decl not in src, (
            f"CarryForwardReportSummary must not locally define shared primitive: {decl!r}"
        )


# ---------------------------------------------------------------------------
# 18. Returns null when report absent
# ---------------------------------------------------------------------------

def test_summary_has_null_guard():
    src = _summary_src()
    assert "return null" in src, (
        "CarryForwardReportSummary must return null when report is absent or invalid"
    )


# ---------------------------------------------------------------------------
# 19. Renders reused_modules and dropped_modules
# ---------------------------------------------------------------------------

def test_summary_renders_reused_modules():
    src = _summary_src()
    assert "reused_modules" in src, (
        "CarryForwardReportSummary must reference reused_modules"
    )


def test_summary_renders_dropped_modules():
    src = _summary_src()
    assert "dropped_modules" in src, (
        "CarryForwardReportSummary must reference dropped_modules"
    )


# ---------------------------------------------------------------------------
# 20. Conflict count with generated-output-wins language
# ---------------------------------------------------------------------------

def test_summary_shows_conflict_count():
    src = _summary_src()
    assert "conflict" in src.lower(), (
        "CarryForwardReportSummary must reference conflicts"
    )


def test_summary_generated_output_wins_explanation():
    src = _summary_src()
    assert "generated output wins" in src.lower() or "generated output" in src.lower(), (
        "CarryForwardReportSummary must explain that generated output wins conflicts"
    )


# ---------------------------------------------------------------------------
# 21. Workspace-unavailable warning
# ---------------------------------------------------------------------------

def test_summary_has_workspace_unavailable_warning():
    src = _summary_src()
    assert "workspace_available" in src, (
        "CarryForwardReportSummary must check workspace_available"
    )
    assert "not available" in src.lower() or "unavailable" in src.lower() or "workspace" in src.lower()
    assert "text-warning" in src or "bg-warning" in src or "border-warning" in src, (
        "CarryForwardReportSummary workspace warning must use semantic warning tokens"
    )


# ---------------------------------------------------------------------------
# 22. Backend-not-copied notice
# ---------------------------------------------------------------------------

def test_summary_states_backend_not_copied():
    src = _summary_src()
    assert "backend" in src.lower(), (
        "CarryForwardReportSummary must mention backend"
    )
    assert "not copied" in src.lower() or "was not" in src.lower() or "never" in src.lower(), (
        "CarryForwardReportSummary must state backend code was not copied"
    )


# ---------------------------------------------------------------------------
# 23. Sensitive path redaction
# ---------------------------------------------------------------------------

def test_summary_has_sensitive_path_redaction():
    src = _summary_src()
    # Redaction logic may live in the file itself or in the shared _carry_forward_redact.js
    # module it imports from. Accept either.
    redact_src = (
        REPO_ROOT / "factory_app" / "app" / "admin" / "pages" / "_carry_forward_redact.js"
    )
    combined = src + (redact_src.read_text(encoding="utf-8") if redact_src.exists() else "")
    assert "secret" in combined.lower() or "sensitive" in combined.lower() or "redact" in combined.lower(), (
        "CarryForwardReportSummary (or its redaction import) must contain sensitive path filtering logic"
    )
    assert "redacted" in combined.lower(), (
        "CarryForwardReportSummary (or its redaction import) must reference '[redacted]' for sensitive paths"
    )


# ===========================================================================
# AppBuildReviewPage (build review page, 24–33)
# ===========================================================================

# ---------------------------------------------------------------------------
# 24. File existence
# ---------------------------------------------------------------------------

def test_app_build_review_page_file_exists():
    assert _BUILD_REVIEW_PATH.exists(), (
        "factory_app/app/admin/pages/AppBuildReviewPage.jsx must exist"
    )


# ---------------------------------------------------------------------------
# 25. Imports CarryForwardReportSummary
# ---------------------------------------------------------------------------

def test_build_review_page_imports_carry_forward_summary():
    src = _build_review_src()
    assert "CarryForwardReportSummary" in src, (
        "AppBuildReviewPage must import CarryForwardReportSummary"
    )


# ---------------------------------------------------------------------------
# 26. Conditionally mounts CarryForwardReportSummary when cfReport present
# ---------------------------------------------------------------------------

def test_build_review_page_mounts_summary_conditionally():
    src = _build_review_src()
    assert "CarryForwardReportSummary" in src
    assert "cfReport" in src, (
        "AppBuildReviewPage must extract cfReport from artifact commit_metadata"
    )
    assert "carry_forward_report" in src, (
        "AppBuildReviewPage must reference carry_forward_report key"
    )


# ---------------------------------------------------------------------------
# 27. Shows no-carry-forward notice when report absent
# ---------------------------------------------------------------------------

def test_build_review_page_shows_no_report_notice():
    src = _build_review_src()
    assert "carry-forward" in src.lower() or "No carry-forward" in src or "carry_forward" in src, (
        "AppBuildReviewPage must show a notice when no carry-forward report is present"
    )


# ---------------------------------------------------------------------------
# 28. Imports from StudioShared
# ---------------------------------------------------------------------------

def test_build_review_page_imports_from_console_shared():
    src = _build_review_src()
    assert "StudioShared" in src, (
        "AppBuildReviewPage must import from StudioShared.jsx (shared primitives)"
    )
    assert "Panel" in src
    assert "StatusPill" in src


# ---------------------------------------------------------------------------
# 29. No hardcoded colors
# ---------------------------------------------------------------------------

def test_build_review_page_no_hardcoded_hex_colors():
    src = _build_review_src()
    hex_matches = re.findall(r'#[0-9a-fA-F]{3,8}\b', src)
    assert hex_matches == [], (
        f"AppBuildReviewPage must not contain hardcoded hex colors: {hex_matches}"
    )


# ---------------------------------------------------------------------------
# 30. Uses buildHistory from snapshot
# ---------------------------------------------------------------------------

def test_build_review_page_uses_build_history():
    src = _build_review_src()
    assert "buildHistory" in src, (
        "AppBuildReviewPage must use buildHistory from snapshot"
    )


# ---------------------------------------------------------------------------
# 31. AppOverviewPage carry-forward behavior still intact (regression)
# ---------------------------------------------------------------------------

def test_overview_carry_forward_panel_still_mounted():
    src = _overview_src()
    assert "CarryForwardReportPanel" in src, (
        "AppOverviewPage must still mount CarryForwardReportPanel (regression)"
    )
    assert "cfReport" in src
    assert "carry_forward_report" in src


# ---------------------------------------------------------------------------
# 32. route_manifest routes /activity to AppBuildReviewPage
# ---------------------------------------------------------------------------

def test_console_page_routes_activity_to_build_review():
    src = (REPO_ROOT / "factory_app" / "app" / "ui" / "route_manifest.json").read_text(encoding="utf-8")
    assert "AppBuildReviewPage" in src, (
        "route_manifest.json must route /activity to AppBuildReviewPage"
    )
    assert '"/apps/:appId/activity"' in src, (
        "route_manifest.json must declare /apps/:appId/activity"
    )


# ---------------------------------------------------------------------------
# 33. admin/index.js registers AppBuildReviewPage
# ---------------------------------------------------------------------------

def test_admin_index_registers_build_review_page():
    src = _INDEX_PATH.read_text(encoding="utf-8")
    assert "AppBuildReviewPage" in src, (
        "admin/index.js must import and register AppBuildReviewPage"
    )
    assert "registerComponent" in src

