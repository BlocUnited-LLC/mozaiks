"""One bounded repository coding turn through a real ACP adapter.

The trusted launcher supplies an already approved file set and one synthetic
gateway token over stdin. This process has no source-control, promotion, or
model credential authority. The launcher must give the container only its
private model-gateway network, disposable tmpfs, and no host mounts.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Literal

from ag2.acp import ClaudeCodeConfig, CodexConfig
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator

from mozaiksai.control_plane import (
    CodingWorkerRequest,
    ControlPlaneACPProviderConfig,
    export_repository_workspace_archive,
)
from mozaiksai.control_plane.implementations.acp_coding_provider import ACPCodingProvider
from mozaiksai.control_plane.workspace import StagedCodingWorkspace

_GATEWAY_URL = "http://model-gateway:8765"
_STAGING_ROOT = Path("/workspace/acp_staging")
_CODEX_HOME = Path("/home/sandbox/.codex")
_CLAUDE_HOME = Path("/home/sandbox/.claude")
_MAX_INPUT_BYTES = 20_971_520
_MAX_ARCHIVE_BYTES = 16_777_216
_MAX_OUTPUT_BYTES = 40_000_000
_MAX_FILES = 50
_CREDENTIAL_NAMES = (
    "OPENAI_API_KEY", "CODEX_API_KEY", "ANTHROPIC_API_KEY",
    "CLAUDE_CODE_OAUTH_TOKEN", "OPENAI_ORG_ID", "OPENAI_PROJECT_ID",
)
_INPUT_KEYS = frozenset({
    "app_id", "build_family", "build_record_id", "change_class",
    "raw_user_request", "files", "read_only_files", "create_paths",
    "delete_paths", "live",
})


class _LiveTurn(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    adapter: Literal["claude_code", "codex"]
    model: StrictStr = Field(min_length=1, max_length=128)
    gateway_url: Literal["http://model-gateway:8765"]
    job_token: StrictStr = Field(min_length=32, max_length=256, repr=False)
    max_wall_seconds: StrictInt = Field(ge=30, le=3600)

    @field_validator("model")
    @classmethod
    def _safe_model_name(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]*", value, flags=re.ASCII):
            raise ValueError("invalid model identifier")
        return value

    @field_validator("job_token")
    @classmethod
    def _opaque_token(cls, value: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", value, flags=re.ASCII):
            raise ValueError("invalid job token")
        return value


class _LiveWorkerRequest(CodingWorkerRequest):
    """The scoped host request plus exact operation grants and gateway route."""

    create_paths: list[StrictStr] = Field(default_factory=list, max_length=_MAX_FILES)
    delete_paths: list[StrictStr] = Field(default_factory=list, max_length=_MAX_FILES)
    live: _LiveTurn


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _parse_request(raw: bytes) -> _LiveWorkerRequest:
    if not raw or len(raw) > _MAX_INPUT_BYTES:
        raise ValueError("invalid input size")
    decoded = json.loads(raw.decode("utf-8", errors="strict"), object_pairs_hook=_strict_object)
    if not isinstance(decoded, dict) or not set(decoded) <= _INPUT_KEYS:
        raise ValueError("unexpected worker input")
    request = _LiveWorkerRequest.model_validate(decoded)
    if (
        request.build_family != "app_bundle"
        or request.change_class != "patch"
        or not request.app_id
        or not request.raw_user_request
        or (not request.files and not request.create_paths)
        or len(request.files) + len(request.read_only_files) + len(request.create_paths) > _MAX_FILES
    ):
        raise ValueError("invalid repository coding scope")
    return request


def _check_process_environment() -> None:
    if any(os.environ.get(name) for name in _CREDENTIAL_NAMES):
        raise RuntimeError("model credential reached the isolated worker")
    if os.environ.get("HOME") != "/home/sandbox":
        raise RuntimeError("isolated home is unavailable")


def _write_private_config(path: Path, content: str) -> None:
    # A fresh one-shot home prevents a disk login or project config from
    # silently replacing the synthetic gateway identity.
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=False)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        output.write(content)


def _codex_config(live: _LiveTurn) -> None:
    # Provider selection is a user-level Codex setting. A project-local
    # .codex/config.toml or CODEX_CONFIG alone cannot establish this route.
    config = "\n".join((
        f"model = {json.dumps(live.model)}",
        'model_provider = "mozaiks_gateway"',
        '[model_providers.mozaiks_gateway]',
        'name = "Mozaiks job gateway"',
        f"base_url = {json.dumps(live.gateway_url + '/v1')}",
        'env_key = "MOZAIKS_JOB_TOKEN"',
        'wire_api = "responses"',
        'requires_openai_auth = false',
        'supports_websockets = false',
        '',
    ))
    _write_private_config(_CODEX_HOME / "config.toml", config)


def _live_config(live: _LiveTurn):
    def configure(
        *, adapter: str, workspace_root: Path, turn_timeout_seconds: int,
        env_source: dict[str, str],
    ) -> ClaudeCodeConfig | CodexConfig:
        if adapter != live.adapter or turn_timeout_seconds != live.max_wall_seconds or env_source:
            raise ValueError("ACP adapter configuration differs from the approved turn")
        if adapter == "codex":
            _codex_config(live)
            return CodexConfig(
                cwd=str(workspace_root),
                fs_root=str(workspace_root),
                model=live.model,
                permission_policy="auto",
                elicitation_policy="decline",
                expose_tools=False,
                allow_terminal=False,
                turn_timeout=float(live.max_wall_seconds),
                env={
                    "CODEX_HOME": str(_CODEX_HOME),
                    "MOZAIKS_JOB_TOKEN": live.job_token,
                    "NO_BROWSER": "1",
                    "CI": "1",
                },
            )
        _CLAUDE_HOME.mkdir(mode=0o700, parents=True, exist_ok=False)
        return ClaudeCodeConfig(
            cwd=str(workspace_root),
            fs_root=str(workspace_root),
            model=live.model,
            permission_policy="auto",
            elicitation_policy="decline",
            expose_tools=False,
            allow_terminal=False,
            turn_timeout=float(live.max_wall_seconds),
            env={
                "CLAUDE_CONFIG_DIR": str(_CLAUDE_HOME),
                "ANTHROPIC_BASE_URL": live.gateway_url,
                "ANTHROPIC_AUTH_TOKEN": live.job_token,
                "ANTHROPIC_MODEL": live.model,
                "NO_BROWSER": "1",
                "CI": "1",
            },
        )

    return configure


async def _execute(request: _LiveWorkerRequest) -> bytes:
    provider_config = ControlPlaneACPProviderConfig.model_validate({
        "enabled": True,
        "adapter": request.live.adapter,
        "budget": {
            "max_files": _MAX_FILES,
            "max_diff_bytes": _MAX_ARCHIVE_BYTES,
            "max_wall_seconds": request.live.max_wall_seconds,
        },
    })
    observed_archive: bytes | None = None

    def capture_workspace(workspace: StagedCodingWorkspace) -> None:
        nonlocal observed_archive
        observed_archive = export_repository_workspace_archive(
            workspace,
            max_files=_MAX_FILES,
            max_archive_bytes=_MAX_ARCHIVE_BYTES,
            create_paths=request.create_paths,
            delete_paths=request.delete_paths,
        )

    provider = ACPCodingProvider(
        provider_config=provider_config,
        staging_root=_STAGING_ROOT,
        acp_config_factory=_live_config(request.live),
        env_source={},
        on_completed_workspace=capture_workspace,
        create_paths=request.create_paths,
        delete_paths=request.delete_paths,
    )
    with contextlib.redirect_stdout(sys.stderr):
        proposal = await provider.execute(request)
    if proposal.status == "completed" and observed_archive is None:
        raise RuntimeError("completed turn lacks workspace archive")
    output = json.dumps({
        "proposal": proposal.model_dump(mode="json"),
        "workspace_archive_base64": (
            base64.b64encode(observed_archive).decode("ascii") if observed_archive is not None else None
        ),
    }, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(output) > _MAX_OUTPUT_BYTES:
        raise RuntimeError("worker output exceeds host budget")
    return output


def main() -> None:
    os.umask(0o077)
    try:
        _check_process_environment()
        raw = sys.stdin.buffer.read(_MAX_INPUT_BYTES + 1)
        request = _parse_request(raw)
        output = asyncio.run(_execute(request))
    except Exception:
        # Inputs and agent errors may contain the token or source. The trusted
        # launcher maps a nonzero exit to a generic failed attempt.
        sys.stderr.write("ACP_LIVE_WORKER_FAILED\n")
        raise SystemExit(1) from None
    sys.stdout.buffer.write(output)
    sys.stdout.buffer.flush()


if __name__ == "__main__":
    main()
