"""One-shot ACP proof worker. Both AG2 and its fake subprocess run here."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import sys
from pathlib import Path

from ag2.acp import ACPConfig
from pydantic import Field, StrictStr

from mozaiksai.control_plane import (
    CodingWorkerRequest,
    ControlPlaneCodingCapabilityConfig,
    ControlPlaneConfig,
    export_repository_workspace_archive,
)
from mozaiksai.control_plane.implementations.acp_coding_provider import ACPCodingProvider
from mozaiksai.control_plane.workspace import StagedCodingWorkspace

_FAKE_AGENT = "/opt/mozaiks/acp_proof/fake_agent.py"
_MAX_ARCHIVE_BYTES = 700_000


class _ProofWorkerRequest(CodingWorkerRequest):
    """The host's scoped request plus exact operation grants for this image."""

    create_paths: list[StrictStr] = Field(default_factory=list, max_length=3)
    delete_paths: list[StrictStr] = Field(default_factory=list, max_length=3)


def _verify_isolation_probe(summary: str, request: _ProofWorkerRequest) -> None:
    """Fail the synthetic proof if its known agent observed extra authority.

    This is test-fixture evidence only. A real agent's reply is never used as
    isolation or acceptance evidence.
    """
    try:
        result = json.loads(summary)
        terminal = result["terminal"]
        expected = {
            "adapter_host_secret_visible": False,
            "adapter_outbound_reachable": False,
            "baseline_file_visible": (
                "app/app.json" in request.files or "app/app.json" in request.read_only_files
            ),
            "read_only_test_visible": (
                "tests/test_dashboard.py" in request.files
                or "tests/test_dashboard.py" in request.read_only_files
            ),
            "host_sentinel_visible": False,
        }
        if (
            not isinstance(result, dict)
            or not isinstance(terminal, dict)
            or set(result) != {*expected, "terminal"}
            or any(result[key] is not value for key, value in expected.items())
            or set(terminal) != {"host_secret_visible", "outbound_reachable"}
            or any(value is not False for value in terminal.values())
        ):
            raise ValueError("isolation probe mismatch")
    except (KeyError, TypeError, ValueError):
        raise RuntimeError("ACP_PROOF_ISOLATION_FAILED") from None


def _fake_config(*, workspace_root: Path, turn_timeout_seconds: int, **_kwargs: object) -> ACPConfig:
    return ACPConfig(
        command=[sys.executable, _FAKE_AGENT],
        cwd=str(workspace_root),
        fs_root=str(workspace_root),
        env={},
        permission_policy="auto",
        elicitation_policy="decline",
        expose_tools=False,
        allow_terminal=False,
        turn_timeout=float(turn_timeout_seconds),
    )


async def _execute() -> str:
    request = _ProofWorkerRequest.model_validate_json(sys.stdin.buffer.read())
    policy = ControlPlaneConfig(
        enabled=True,
        llm_profiles={"codegen": {"llm_config": {"model": "proof-model"}}},
        coding=ControlPlaneCodingCapabilityConfig.model_validate(
            {
                "enabled": True,
                "llm_profile": "codegen",
                "providers": {"acp": {"enabled": True, "budget": {"max_wall_seconds": 30}}},
            }
        ),
    )
    observed_archive: bytes | None = None

    def _capture_workspace(workspace: StagedCodingWorkspace) -> None:
        nonlocal observed_archive
        observed_archive = export_repository_workspace_archive(
            workspace, max_files=3, max_archive_bytes=_MAX_ARCHIVE_BYTES,
            create_paths=request.create_paths, delete_paths=request.delete_paths,
        )

    provider = ACPCodingProvider(
        config_loader=lambda: policy,
        staging_root=Path("/workspace/acp_staging"),
        acp_config_factory=_fake_config,
        env_source={},
        on_completed_workspace=_capture_workspace,
        create_paths=request.create_paths,
        delete_paths=request.delete_paths,
    )
    with contextlib.redirect_stdout(sys.stderr):
        proposal = await provider.execute(request)
    if proposal.status == "completed":
        _verify_isolation_probe(proposal.summary, request)
    return json.dumps({
        "proposal": proposal.model_dump(mode="json"),
        "workspace_archive_base64": (
            base64.b64encode(observed_archive).decode("ascii") if observed_archive is not None else None
        ),
    })


if __name__ == "__main__":
    sys.stdout.write(asyncio.run(_execute()))
    sys.stdout.flush()
