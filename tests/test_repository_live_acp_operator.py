"""The operator transport-smoke entry refuses unsafe use and writes content-free evidence.

No test here runs Docker or a model: the live turn is replaced by a scripted
``RepositoryDockerTurn`` so the host-side staging, finalization, usage ledger,
and receipt can be checked. The entry itself refuses to run under pytest.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from mozaiksai.control_plane import (
    RepositoryDockerExecutionError,
    RepositoryDockerTurn,
    StagedPatchProposal,
)
from mozaiksai.control_plane.repository_acp_usage import IsolatedACPUsage
from mozaiksai.core.semantics.archive import ArchiveEntry, build_deterministic_archive
from scripts import smoke_repository_live_acp_turn as smoke

_PASTED = "operator-smoke-placeholder-0001"
_SOURCE = "# Throwaway\n\nIntro line.\n"
_EDITED = "# Throwaway\n\nIntro line.\nOne more sentence.\n"
_WORKER = "sha256:" + "1" * 64
_GATEWAY = "sha256:" + "2" * 64
_ENV = {smoke.ENABLE_ENV: "1"}


@pytest.fixture
def snapshot(tmp_path: Path) -> Path:
    root = tmp_path / "snapshot"
    (root / "docs").mkdir(parents=True)
    (root / "README.md").write_text(_SOURCE, encoding="utf-8", newline="\n")
    (root / "docs" / "guide.md").write_text("# Guide\n", encoding="utf-8", newline="\n")
    return root


@pytest.fixture(autouse=True)
def operator_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(smoke, "_attended", lambda: True)
    monkeypatch.setattr(smoke, "_checkout_identity", lambda: ("a" * 40, False))


def _argv(snapshot: Path, output: Path, *extra: str) -> list[str]:
    return [
        "--snapshot-dir", str(snapshot), "--baseline-sha", "b" * 40,
        "--edit", "README.md", "--read-only", "docs/guide.md",
        "--instruction", "Add one sentence to the introduction.",
        "--model", "claude-model-id", "--worker-image-id", _WORKER, "--gateway-image-id", _GATEWAY,
        "--output-dir", str(output), *extra,
    ]


def _never_prompt(_prompt: str) -> str:
    raise AssertionError("the key prompt must not be reached")


def _turn(content: str = _EDITED, *, usage: bool = True) -> RepositoryDockerTurn:
    return RepositoryDockerTurn(
        proposal=StagedPatchProposal(
            proposal_id="p-1", provider_id="acp_docker", status="completed",
            changed_files=[{"path": "README.md", "op": "update", "content": content}],
            owned_paths=["README.md"],
        ),
        workspace_archive=build_deterministic_archive(
            [ArchiveEntry(path="README.md", content=content.encode("utf-8"))],
        ),
        usage=IsolatedACPUsage(prompt_tokens=10, completion_tokens=4, total_tokens=14) if usage else None,
    )


@pytest.mark.parametrize(("environ", "message"), [
    ({}, "MOZAIKS_RUN_REPOSITORY_LIVE_ACP=1"),
    ({**_ENV, "CI": "true"}, "CI"),
    ({**_ENV, "GITHUB_ACTIONS": "true"}, "CI"),
    ({**_ENV, "PYTEST_CURRENT_TEST": "x"}, "pytest"),
])
def test_entry_refuses_without_opt_in_or_inside_automation(
    snapshot: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str],
    environ: dict[str, str], message: str,
) -> None:
    assert smoke.run(_argv(snapshot, tmp_path / "out"), environ=environ, read_key=_never_prompt) == 2
    assert message in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


def test_entry_never_runs_under_pytest_by_default(snapshot: Path, tmp_path: Path) -> None:
    import os

    assert smoke.ENABLE_ENV not in os.environ
    assert smoke.run(_argv(snapshot, tmp_path / "out"), read_key=_never_prompt) == 2


def test_entry_requires_an_attended_terminal(
    snapshot: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(smoke, "_attended", lambda: False)
    assert smoke.run(_argv(snapshot, tmp_path / "out"), environ=_ENV, read_key=_never_prompt) == 2


def test_entry_refuses_a_dirty_checkout(snapshot: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(smoke, "_checkout_identity", lambda: ("a" * 40, True))
    assert smoke.run(_argv(snapshot, tmp_path / "out"), environ=_ENV, read_key=_never_prompt) == 2


@pytest.mark.parametrize("change", [
    {"--max-wall-seconds": "89"},
    {"--max-wall-seconds": "601"},
    {"--worker-image-id": "mozaiks-acp-live-worker:local"},
    {"--gateway-image-id": "sha256:" + "z" * 64},
    {"--baseline-sha": "c" * 39},
    {"--repository-label": "BlocUnited-LLC/mozaiks-app"},
    {"--edit": "../outside.md"},
    {"--edit": "/etc/hosts"},
    {"--edit": "docs\\guide.md"},
    {"--edit": "missing.md"},
    {"--read-only": "README.md"},
])
def test_entry_rejects_unsafe_inputs_before_the_key_prompt(
    snapshot: Path, tmp_path: Path, change: dict[str, str],
) -> None:
    argv = _argv(snapshot, tmp_path / "out")
    for flag, value in change.items():
        if flag in argv:
            argv[argv.index(flag) + 1] = value
        else:
            argv.extend([flag, value])
    assert smoke.run(argv, environ=_ENV, read_key=_never_prompt) == 2


def test_entry_limits_read_only_paths(snapshot: Path, tmp_path: Path) -> None:
    for name in ("a.md", "b.md"):
        (snapshot / name).write_text("x\n", encoding="utf-8")
    argv = _argv(snapshot, tmp_path / "out", "--read-only", "a.md", "--read-only", "b.md")
    assert smoke.run(argv, environ=_ENV, read_key=_never_prompt) == 2


def test_entry_refuses_output_inside_the_snapshot(snapshot: Path) -> None:
    assert smoke.run(_argv(snapshot, snapshot / "out"), environ=_ENV, read_key=_never_prompt) == 2


def test_entry_refuses_a_linked_snapshot_file(snapshot: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside.md"
    outside.write_text("secret\n", encoding="utf-8")
    try:
        (snapshot / "linked.md").symlink_to(outside)
    except OSError:
        pytest.skip("symlinks unavailable")
    argv = _argv(snapshot, tmp_path / "out")
    argv[argv.index("--edit") + 1] = "linked.md"
    assert smoke.run(argv, environ=_ENV, read_key=_never_prompt) == 2


def test_successful_smoke_writes_content_free_receipt_and_host_verified_diff(
    snapshot: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    monkeypatch.setenv("ANTHROPIC_API_KEY", "env-key-must-be-ignored")

    async def fake_turn(request: Any, **kwargs: Any) -> RepositoryDockerTurn:
        captured.update(kwargs, request=request)
        kwargs["validate_path"]("README.md")
        with pytest.raises(ValueError):
            kwargs["validate_path"]("other.md")
        return _turn()

    monkeypatch.setattr(smoke, "execute_repository_docker_turn", fake_turn)
    output = tmp_path / "out"
    assert smoke.run(_argv(snapshot, output), environ=_ENV, read_key=lambda _prompt: f"  {_PASTED}\n") == 0

    profile = captured["live_profile"]
    assert profile.upstream_api_key.get_secret_value() == _PASTED
    assert (profile.adapter, profile.model) == ("claude_code", "claude-model-id")
    assert captured["image"] == captured["expected_image_id"] == _WORKER
    assert profile.gateway_image == profile.gateway_expected_image_id == _GATEWAY
    assert captured["max_wall_seconds"] == 300
    context = captured["approved_context"]
    assert context.allowed_paths == ["README.md", "docs/guide.md"]
    assert context.read_only_paths == ["docs/guide.md"]
    assert context.create_paths == [] and context.delete_paths == []
    assert context.repository_full_name == "throwaway/transport-smoke"
    assert len(context.baseline_commit_sha) == 40
    assert set(captured["snapshot"].file_manifest) == {"README.md", "docs/guide.md"}
    assert captured["request"].files == {"README.md": _SOURCE}
    assert captured["request"].read_only_files == {"docs/guide.md": "# Guide\n"}

    receipt_text = (output / "receipt.json").read_text(encoding="utf-8")
    receipt = json.loads(receipt_text)
    assert receipt["oss_sha"] == "a" * 40
    assert receipt["worker_image_id"] == _WORKER and receipt["gateway_image_id"] == _GATEWAY
    assert receipt["approved_path"] == "README.md"
    assert receipt["proposal_status"] == "completed"
    assert receipt["repository_patch_digest"].startswith("sha256:")
    assert receipt["usage"] == {"prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14}
    assert receipt["usage_recorded"] is True and receipt["error_code"] is None
    for forbidden in (_PASTED, "env-key-must-be-ignored", "One more sentence", "Intro line", "# Guide",
                      "Add one sentence"):
        assert forbidden not in receipt_text
    diff = (output / "host-verified.diff").read_text(encoding="utf-8")
    assert "+One more sentence." in diff and diff.startswith("--- a/README.md")
    assert (snapshot / "README.md").read_text(encoding="utf-8") == _SOURCE


def test_failed_turn_records_only_an_error_code(
    snapshot: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def failing_turn(_request: Any, **_kwargs: Any) -> RepositoryDockerTurn:
        raise RepositoryDockerExecutionError("REPOSITORY_LIVE_GATEWAY_NOT_READY")

    monkeypatch.setattr(smoke, "execute_repository_docker_turn", failing_turn)
    output = tmp_path / "out"
    assert smoke.run(_argv(snapshot, output), environ=_ENV, read_key=lambda _prompt: _PASTED) == 1
    receipt = json.loads((output / "receipt.json").read_text(encoding="utf-8"))
    assert receipt["error_code"] == "REPOSITORY_LIVE_GATEWAY_NOT_READY"
    assert receipt["proposal_status"] is None and receipt["repository_patch_digest"] is None
    assert receipt["usage"] is None and receipt["host_verified_diff"] is None
    assert not (output / "host-verified.diff").exists()


def test_host_verification_failure_withholds_the_diff(
    snapshot: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def mismatched_turn(_request: Any, **_kwargs: Any) -> RepositoryDockerTurn:
        turn = _turn()
        return RepositoryDockerTurn(
            proposal=StagedPatchProposal.model_validate({
                **turn.proposal.model_dump(),
                "changed_files": [{"path": "README.md", "op": "update", "content": "claimed but not staged\n"}],
            }),
            workspace_archive=turn.workspace_archive, usage=None,
        )

    monkeypatch.setattr(smoke, "execute_repository_docker_turn", mismatched_turn)
    output = tmp_path / "out"
    assert smoke.run(_argv(snapshot, output), environ=_ENV, read_key=lambda _prompt: _PASTED) == 1
    receipt = json.loads((output / "receipt.json").read_text(encoding="utf-8"))
    assert receipt["proposal_status"] == "completed"
    assert receipt["error_code"] == "REPOSITORY_PATCH_PROPOSAL_MISMATCH"
    assert receipt["repository_patch_digest"] is None and receipt["usage"] is None
    assert not (output / "host-verified.diff").exists()


def test_empty_key_cancels_without_a_receipt(snapshot: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def never_turn(*_args: Any, **_kwargs: Any) -> RepositoryDockerTurn:
        raise AssertionError("no turn without a key")

    monkeypatch.setattr(smoke, "execute_repository_docker_turn", never_turn)
    assert smoke.run(_argv(snapshot, tmp_path / "out"), environ=_ENV, read_key=lambda _prompt: "  ") == 2
    assert not (tmp_path / "out").exists()
