"""Safety contracts for the manually dispatched package publication workflow."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8"))
EVENTS = WORKFLOW.get("on", WORKFLOW.get(True))  # PyYAML uses YAML 1.1's boolean `on`.
JOBS = WORKFLOW["jobs"]


def _step(job: str, name: str) -> dict:
    return next(step for step in JOBS[job]["steps"] if step.get("name") == name)


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


def test_publication_requires_dated_notes_and_protected_environment() -> None:
    assert "CHANGELOG.md needs a dated section" in _step(
        "build", "Verify dated release notes and tag identity"
    )["run"]
    approval = JOBS["approve-release"]
    assert approval["needs"] == "build"
    assert approval["environment"]["name"] == "release"
    assert approval["if"] == "inputs.release_target == 'pypi'"
    protection = _step("approve-release", "Verify release environment protection")["run"]
    assert 'rule.get("type") == "required_reviewers"' in protection
    assert 'environment.get("can_admins_bypass") is not False' in protection


def test_testpypi_rehearsal_cannot_publish_to_production() -> None:
    rehearsal = JOBS["publish-testpypi"]
    assert rehearsal["if"] == "inputs.release_target == 'testpypi'"
    assert rehearsal["needs"] == "build"
    assert rehearsal["environment"]["name"] == "testpypi"
    assert rehearsal["permissions"]["id-token"] == "write"
    assert "required_reviewers" in _step(
        "publish-testpypi", "Verify TestPyPI environment protection"
    )["run"]
    assert _step("publish-testpypi", "Publish to TestPyPI")["with"]["repository-url"] == (
        "https://test.pypi.org/legacy/"
    )


def test_pypi_upload_precedes_release_at_exact_candidate() -> None:
    publication = JOBS["publish-pypi"]
    assert publication["if"] == "inputs.release_target == 'pypi'"
    assert publication["needs"] == ["build", "approve-release"]
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
