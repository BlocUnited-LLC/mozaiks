"""One-shot ACP proof worker. Both AG2 and its fake subprocess run here."""

from __future__ import annotations

import asyncio
import contextlib
import sys
from pathlib import Path

from ag2.acp import ACPConfig

from mozaiksai.control_plane import (
    CodingWorkerRequest,
    ControlPlaneCodingCapabilityConfig,
    ControlPlaneConfig,
)
from mozaiksai.control_plane.implementations.acp_coding_provider import ACPCodingProvider

_FAKE_AGENT = "/opt/mozaiks/acp_proof/fake_agent.py"


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
    request = CodingWorkerRequest.model_validate_json(sys.stdin.buffer.read())
    policy = ControlPlaneConfig(
        enabled=True,
        coding=ControlPlaneCodingCapabilityConfig.model_validate(
            {"enabled": True, "providers": {"acp": {"enabled": True, "budget": {"max_wall_seconds": 30}}}}
        ),
    )
    provider = ACPCodingProvider(
        config_loader=lambda: policy,
        staging_root=Path("/workspace/acp_staging"),
        acp_config_factory=_fake_config,
        env_source={},
    )
    with contextlib.redirect_stdout(sys.stderr):
        proposal = await provider.execute(request)
    return proposal.model_dump_json()


if __name__ == "__main__":
    sys.stdout.write(asyncio.run(_execute()))
    sys.stdout.flush()
