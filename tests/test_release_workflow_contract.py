"""Safety contracts for the manually dispatched package publication workflow."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8"))
EVENTS = WORKFLOW.get("on", WORKFLOW.get(True))  # PyYAML uses YAML 1.1's boolean `on`.
JOBS = WORKFLOW["jobs"]


def _step(job: str, name: str) -> dict:
    return next(step for step in JOBS[job]["steps"] if step.get("name") == name)


def _run_changelog_gate(tmp_path: Path, changelog: str) -> subprocess.CompletedProcess[str]:
    (tmp_path / "CHANGELOG.md").write_text(changelog, encoding="utf-8")
    command = _step("build", "Verify dated release notes and tag identity")["run"]
    script = command.split("python - <<'PY'\n", 1)[1].split("\nPY", 1)[0]
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env={**os.environ, "RELEASE_VERSION": "0.2.0"},
        capture_output=True,
        text=True,
        check=False,
    )


def test_release_requires_explicit_main_candidate_and_successful_push_ci() -> None:
    assert set(EVENTS) == {"workflow_dispatch"}
    inputs = EVENTS["workflow_dispatch"]["inputs"]
    assert inputs["candidate_sha"]["required"] is True
    assert inputs["release_target"]["options"] == ["testpypi", "pypi"]
    assert inputs["release_target"]["default"] == "testpypi"
    assert inputs["confirm_release"]["required"] is True

    gate = _step("build", "Verify release gate")
    assert gate["env"]["CANDIDATE_SHA"] == "${{ inputs.candidate_sha }}"
    assert gate["env"]["GH_TOKEN"] == "${{ github.token }}"
    script = gate["run"]
    assert "refs/heads/main" in script
    assert '"$CANDIDATE_SHA" != "$GITHUB_SHA"' in script
    assert '"$CANDIDATE_SHA" != "$checked_out_sha"' in script
    assert '"$CANDIDATE_SHA" != "$main_sha"' in script
    assert "actions/workflows/ci.yml/runs?head_sha=$CANDIDATE_SHA&branch=main&event=push" in script
    assert 'latest.get("status") != "completed"' in script
    assert 'latest.get("conclusion") != "success"' in script


def test_publication_requires_dated_notes_and_protected_pypi_environment() -> None:
    assert "CHANGELOG.md needs a dated section" in _step(
        "build", "Verify dated release notes and tag identity"
    )["run"]
    assert "approve-release" not in JOBS
    protection = _step("publish-pypi", "Verify PyPI environment protection")["run"]
    assert 'rule.get("type") == "required_reviewers"' in protection
    assert 'environment.get("can_admins_bypass") is not False' in protection
    assert 'branch_policy.get("protected_branches") is not True' in protection
    assert 'branch_policy.get("custom_branch_policies") is not False' in protection


@pytest.mark.parametrize(
    ("changelog", "error"),
    [
        (
            "## Unreleased\n\n### Changed\n\n- Pending change.\n\n"
            "## 0.2.0 - 2026-10-08\n\n- Release notes.\n",
            "Unreleased section must be empty",
        ),
        (
            "## Unreleased\n\n## 0.1.11 - 2026-07-20\n\n- Old notes.\n\n"
            "## 0.2.0 - 2026-10-08\n\n- Release notes.\n",
            "dated section for the release version directly below Unreleased",
        ),
        (
            "## Unreleased\n\n## 0.2.0 - 2026-02-30\n\n- Release notes.\n",
            "release section has an invalid date",
        ),
    ],
)
def test_release_changelog_rejects_unprepared_notes(
    tmp_path: Path, changelog: str, error: str
) -> None:
    result = _run_changelog_gate(tmp_path, changelog)
    assert result.returncode != 0
    assert error in result.stderr


def test_release_changelog_accepts_prepared_notes(tmp_path: Path) -> None:
    result = _run_changelog_gate(
        tmp_path,
        "# Changelog\n\n## Unreleased\n\n## 0.2.0 - 2026-10-08\n\n"
        "### Changed\n\n- Release notes.\n\n## 0.1.11 - 2026-07-20\n",
    )
    assert result.returncode == 0, result.stderr


def test_testpypi_rehearsal_cannot_publish_to_production() -> None:
    rehearsal = JOBS["publish-testpypi"]
    assert rehearsal["if"] == "inputs.release_target == 'testpypi'"
    assert rehearsal["needs"] == "build"
    assert rehearsal["environment"]["name"] == "testpypi"
    assert rehearsal["permissions"]["id-token"] == "write"
    protection = _step("publish-testpypi", "Verify TestPyPI environment protection")["run"]
    assert "required_reviewers" in protection
    assert 'branch_policy.get("protected_branches") is not True' in protection
    assert _step("publish-testpypi", "Publish to TestPyPI")["with"]["repository-url"] == (
        "https://test.pypi.org/" + "leg" + "acy/"
    )


def test_pypi_upload_precedes_release_at_exact_candidate() -> None:
    publication = JOBS["publish-pypi"]
    assert publication["if"] == "inputs.release_target == 'pypi'"
    assert publication["needs"] == "build"
    assert publication["environment"]["name"] == "pypi"
    assert publication["permissions"]["id-token"] == "write"
    assert "branches/main" in _step("publish-pypi", "Verify candidate still matches main")["run"]

    release = JOBS["github-release"]
    assert release["needs"] == ["build", "publish-pypi"]
    assert release["if"] == "inputs.release_target == 'pypi'"
    assert _step("github-release", "Checkout exact published candidate")["with"]["ref"] == (
        "${{ inputs.candidate_sha }}"
    )
    release_action = _step("github-release", "Publish GitHub release")["with"]
    assert release_action["target_commitish"] == "${{ inputs.candidate_sha }}"
    assert release_action["body_path"] == "release-notes.md"
