"""
Tests for carry_forward_report persistence through artifact creation.

Proves the full persistence path:
    resolve_carry_forward_preservation
      → context_variables["carry_forward_report"]
      → _register_app_bundle_artifact_version reads "carry_forward_report"
      → commit_metadata={"metadata": {"carry_forward_report": report, ...}}
      → ArtifactVersionDoc.commit_metadata.metadata
      → Studio build history: v.model_dump()["commit_metadata"]["metadata"]["carry_forward_report"]

12 tests:
 - Tests 1-4  verify the carry_forward_report → metadata path via source inspection.
   Direct import of generate_and_download triggers a circular import when run in
   isolation (see test_carry_forward_preservation.py test 26 for the same pattern).
 - Tests 5-7  verify the ArtifactCommitMetadata / ArtifactVersionDoc model shapes.
 - Tests 8-9  verify the resolver writes the report to the context key.
 - Tests 10-11 verify the Studio build history model_dump path.
 - Test 12    verifies the context key identity ("carry_forward_report", not "carry_forward_additions").
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_APP_ID = "app_cf_persist_test"

_GENERATE_AND_DOWNLOAD_SRC = (
    Path(__file__).resolve().parents[1]
    / "factory_app"
    / "workflows"
    / "AppGenerator"
    / "tools"
    / "generate_and_download.py"
).read_text(encoding="utf-8")

_MINIMAL_REPORT: dict[str, Any] = {
    "previous_app_bundle_ref": "av_prior_001",
    "workspace_available": True,
    "workspace_source": "artifact_zip",
    "preserved_paths": ["modules/settings/module.yaml"],
    "skipped_paths": {},
    "conflicts": {},
    "decisions_processed": [],
    "reused_modules": ["settings"],
    "adapted_modules": [],
    "regenerated_modules": [],
    "dropped_modules": [],
    "warnings": [],
}

# Keys that CarryForwardReportSummary.jsx reads from the artifact payload.
_REQUIRED_REPORT_KEYS = {
    "previous_app_bundle_ref",
    "workspace_available",
    "preserved_paths",
    "conflicts",
    "skipped_paths",
    "reused_modules",
    "dropped_modules",
    "warnings",
}


# ---------------------------------------------------------------------------
# Group 1: generate_and_download source verifies carry_forward_report persistence (4 tests)
#
# Direct import of generate_and_download triggers a circular import chain
# (BuilderArtifactStore → mozaiksai.core.data → persistence_manager →
#  mozaiksai.core.workflow → orchestration_patterns → persistence_manager) when
# no other test in the session has pre-initialized the chain.  Source inspection
# is the established pattern in this repo (see test_carry_forward_preservation.py
# test 26).
# ---------------------------------------------------------------------------


def test_generate_and_download_reads_carry_forward_report_from_context() -> None:
    """[1] _register_app_bundle_artifact_version reads "carry_forward_report" from context."""
    assert 'context_variables.get("carry_forward_report")' in _GENERATE_AND_DOWNLOAD_SRC, (
        '_register_app_bundle_artifact_version must read "carry_forward_report" from context'
    )


def test_generate_and_download_guards_with_isinstance_dict() -> None:
    """[2] Report is only included when it is a dict (absent/non-dict context returns are skipped)."""
    # Covers both "absent from context" (None) and "non-dict" (str, list, ...) cases.
    assert "isinstance(cf_report, dict)" in _GENERATE_AND_DOWNLOAD_SRC, (
        "_register_app_bundle_artifact_version must guard carry_forward_report with isinstance(cf_report, dict)"
    )


def test_generate_and_download_has_try_except_for_report_read() -> None:
    """[3] The context.get("carry_forward_report") call is wrapped in try/except (graceful failure)."""
    # Verify the source has both the read and a surrounding except block.
    # The exact block reads the key and catches any exception from context.get().
    src = _GENERATE_AND_DOWNLOAD_SRC
    assert 'cf_report = context_variables.get("carry_forward_report")' in src, (
        'Source must contain: cf_report = context_variables.get("carry_forward_report")'
    )
    # The carry_forward_report section must be inside a try-except.
    # Find the index of the read and check an except clause follows it.
    read_idx = src.index('context_variables.get("carry_forward_report")')
    # Look for 'except Exception' within the 400 characters after the read.
    nearby = src[read_idx: read_idx + 400]
    assert "except Exception" in nearby, (
        "context.get('carry_forward_report') must be inside a try-except Exception block"
    )


def test_generate_and_download_assigns_report_to_bundle_content_metadata() -> None:
    """[4] Report dict is written to bundle_content_metadata["carry_forward_report"]."""
    assert 'bundle_content_metadata["carry_forward_report"] = cf_report' in _GENERATE_AND_DOWNLOAD_SRC, (
        "_register_app_bundle_artifact_version must write carry_forward_report to bundle_content_metadata"
    )


# ---------------------------------------------------------------------------
# Group 2: ArtifactCommitMetadata and ArtifactVersionDoc model shape (3 tests)
# ---------------------------------------------------------------------------


def test_artifact_commit_metadata_accepts_carry_forward_report() -> None:
    """[5] ArtifactCommitMetadata.metadata accepts carry_forward_report without ValidationError."""
    from mozaiksai.core.artifacts.models import ArtifactCommitMetadata

    meta = ArtifactCommitMetadata.model_validate(
        {"metadata": {"carry_forward_report": _MINIMAL_REPORT}}
    )
    assert meta.metadata["carry_forward_report"] == _MINIMAL_REPORT


def test_artifact_version_doc_model_dump_includes_commit_metadata_metadata() -> None:
    """[6] ArtifactVersionDoc.model_dump() includes commit_metadata.metadata (studio build history path)."""
    from mozaiksai.core.artifacts.models import ArtifactVersionDoc

    doc = ArtifactVersionDoc.model_validate(
        {
            "_id": "av_test_01",
            "app_id": _APP_ID,
            "artifact_kind": "app_bundle",
            "artifact_key": "app_bundle",
            "version_number": 1,
            "lineage_root_id": "av_test_01",
            "commit_metadata": {"metadata": {"carry_forward_report": _MINIMAL_REPORT}},
        }
    )
    dumped = doc.model_dump(by_alias=False, mode="python")
    assert "commit_metadata" in dumped
    assert "metadata" in dumped["commit_metadata"]


def test_artifact_version_doc_report_survives_model_dump_roundtrip() -> None:
    """[7] carry_forward_report value preserved after model_dump() — no data loss."""
    from mozaiksai.core.artifacts.models import ArtifactVersionDoc

    doc = ArtifactVersionDoc.model_validate(
        {
            "_id": "av_test_02",
            "app_id": _APP_ID,
            "artifact_kind": "app_bundle",
            "artifact_key": "app_bundle",
            "version_number": 1,
            "lineage_root_id": "av_test_02",
            "commit_metadata": {"metadata": {"carry_forward_report": _MINIMAL_REPORT}},
        }
    )
    dumped = doc.model_dump(by_alias=False, mode="python")
    report_in_dump = dumped["commit_metadata"]["metadata"]["carry_forward_report"]
    assert report_in_dump == _MINIMAL_REPORT


# ---------------------------------------------------------------------------
# Group 3: Resolver writes carry_forward_report to context (2 tests)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolver_writes_carry_forward_report_to_context() -> None:
    """[8] resolve_carry_forward_preservation writes carry_forward_report to context dict."""
    from factory_app.control_plane.tools.resolve_carry_forward_preservation import (
        resolve_carry_forward_preservation,
    )

    ctx: dict[str, Any] = {
        "app_id": _APP_ID,
        "previous_app_bundle_ref": "av_prior",
        "app_build_plan": {
            "carry_forward_decisions": [
                {"module_id": "settings", "decision": "reuse", "reason": "generic"},
            ]
        },
    }
    workspace = {
        "present": True,
        "source": "artifact_zip",
        "file_map": {"modules/settings/module.yaml": "id: settings\n"},
    }
    with (
        patch(
            "factory_app.control_plane.tools.resolve_carry_forward_preservation.load_artifact_workspace",
            new=AsyncMock(return_value=workspace),
        ),
        patch(
            "factory_app.control_plane.tools.resolve_carry_forward_preservation.get_artifact_store",
            return_value=MagicMock(),
        ),
        patch(
            "factory_app.control_plane.tools.resolve_carry_forward_preservation.get_artifact_content_store",
            return_value=MagicMock(),
        ),
    ):
        await resolve_carry_forward_preservation(
            context_variables=ctx,
            artifact_store=MagicMock(),
            content_store=MagicMock(),
        )

    assert "carry_forward_report" in ctx, "carry_forward_report not written to context"
    assert isinstance(ctx["carry_forward_report"], dict)


@pytest.mark.asyncio
async def test_resolver_report_has_required_keys() -> None:
    """[9] carry_forward_report from resolver has all 8 keys expected by CarryForwardReportSummary."""
    from factory_app.control_plane.tools.resolve_carry_forward_preservation import (
        resolve_carry_forward_preservation,
    )

    ctx: dict[str, Any] = {
        "app_id": _APP_ID,
        "previous_app_bundle_ref": "av_prior",
        "app_build_plan": {
            "carry_forward_decisions": [
                {"module_id": "settings", "decision": "reuse", "reason": "generic"},
                {"module_id": "contacts", "decision": "drop", "reason": "not relevant"},
            ]
        },
    }
    workspace = {
        "present": True,
        "source": "artifact_zip",
        "file_map": {"modules/settings/module.yaml": "id: settings\n"},
    }
    with (
        patch(
            "factory_app.control_plane.tools.resolve_carry_forward_preservation.load_artifact_workspace",
            new=AsyncMock(return_value=workspace),
        ),
        patch(
            "factory_app.control_plane.tools.resolve_carry_forward_preservation.get_artifact_store",
            return_value=MagicMock(),
        ),
        patch(
            "factory_app.control_plane.tools.resolve_carry_forward_preservation.get_artifact_content_store",
            return_value=MagicMock(),
        ),
    ):
        result = await resolve_carry_forward_preservation(
            context_variables=ctx,
            artifact_store=MagicMock(),
            content_store=MagicMock(),
        )

    report = result["carry_forward_report"]
    missing = _REQUIRED_REPORT_KEYS - set(report.keys())
    assert not missing, f"carry_forward_report missing keys: {sorted(missing)}"
    assert isinstance(report["preserved_paths"], list)
    assert isinstance(report["warnings"], list)


# ---------------------------------------------------------------------------
# Group 4: Studio build history payload (2 tests)
# ---------------------------------------------------------------------------


def test_studio_build_history_includes_commit_metadata_in_model_dump() -> None:
    """[10] ArtifactVersionDoc.model_dump() (studio build history) includes nested commit_metadata.metadata."""
    from mozaiksai.core.artifacts.models import ArtifactVersionDoc

    doc = ArtifactVersionDoc.model_validate(
        {
            "_id": "av_hist_01",
            "app_id": _APP_ID,
            "artifact_kind": "app_bundle",
            "artifact_key": "app_bundle",
            "version_number": 1,
            "lineage_root_id": "av_hist_01",
            "commit_metadata": {
                "message": "AppGenerator: TestBundle",
                "metadata": {
                    "artifact_path": "/tmp/bundle.zip",
                    "bundle_name": "TestBundle",
                    "carry_forward_report": _MINIMAL_REPORT,
                },
            },
        }
    )
    # Studio /api/studio/build/history calls v.model_dump(by_alias=False, mode="python")
    dumped = doc.model_dump(by_alias=False, mode="python")
    assert "commit_metadata" in dumped
    assert "metadata" in dumped["commit_metadata"]
    assert "carry_forward_report" in dumped["commit_metadata"]["metadata"]


def test_studio_build_history_report_nested_correctly() -> None:
    """[11] Studio build history: carry_forward_report at commit_metadata.metadata.carry_forward_report."""
    from mozaiksai.core.artifacts.models import ArtifactVersionDoc

    doc = ArtifactVersionDoc.model_validate(
        {
            "_id": "av_hist_02",
            "app_id": _APP_ID,
            "artifact_kind": "app_bundle",
            "artifact_key": "app_bundle",
            "version_number": 2,
            "lineage_root_id": "av_hist_02",
            "commit_metadata": {
                "metadata": {"carry_forward_report": _MINIMAL_REPORT},
            },
        }
    )
    dumped = doc.model_dump(by_alias=False, mode="python")
    nested = dumped["commit_metadata"]["metadata"]["carry_forward_report"]
    assert nested["reused_modules"] == ["settings"]
    assert nested["preserved_paths"] == ["modules/settings/module.yaml"]
    assert nested["workspace_available"] is True


# ---------------------------------------------------------------------------
# Group 5: Context key identity (1 test)
# ---------------------------------------------------------------------------


def test_artifact_metadata_key_is_carry_forward_report_not_additions() -> None:
    """[12] "carry_forward_additions" (file-merge key) is NOT fed into artifact metadata.

    Only "carry_forward_report" is read for commit_metadata.metadata inclusion.
    The source must not contain any assignment of carry_forward_additions to
    bundle_content_metadata.
    """
    src = _GENERATE_AND_DOWNLOAD_SRC
    # The persistence key must be carry_forward_report
    assert 'cf_report = context_variables.get("carry_forward_report")' in src, (
        'generate_and_download must read "carry_forward_report" for artifact metadata'
    )
    # carry_forward_additions is only used as the file-merge key (merged into files_map),
    # not as an artifact metadata key.
    assert 'bundle_content_metadata["carry_forward_additions"]' not in src, (
        '"carry_forward_additions" must not be written to bundle_content_metadata'
    )

