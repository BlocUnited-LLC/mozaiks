"""ACP-backed CLI coding provider for the refinement control plane.

Implements :class:`~mozaiksai.control_plane.ports.CodingExecutionProvider` by
driving an ACP-compatible CLI coding agent (Claude Code, Codex, OpenCode) for
one bounded prompt turn inside a disposable staged workspace, then harvesting
the workspace diff deterministically.

Authority model: the provider receives an explicitly scoped
:class:`CodingWorkerRequest` and returns a :class:`StagedPatchProposal`. It
holds no routing, scope, acceptance, or promotion authority, and nothing in
this module trusts the CLI agent: the workspace contains only copies of the
scoped files, the subprocess environment is an explicit allowlist, the agent's
question/permission channels are closed (``elicitation_policy="decline"``,
terminal capability not advertised), and every accepted change comes from the
post-run hash harvest — never from the agent's own claims. Out-of-scope edits
reject the whole proposal.

This provider is dark by default: ``refinement_policy.yaml``'s
``coding.providers.acp.enabled`` is ``false``, the ``ag2[acp]`` extra is
optional, and no production path constructs it yet (provider selection lands
separately).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from mozaiksai.control_plane.config import (
    ControlPlaneACPProviderConfig,
    ControlPlaneConfig,
    load_control_plane_config,
)
from mozaiksai.control_plane.contracts import (
    CodingWorkerRequest,
    ProposedFileChange,
    ProviderEventRecord,
    StagedPatchProposal,
)
from mozaiksai.control_plane.workspace import (
    StagedCodingWorkspace,
    WorkspaceHarvest,
    harvest_coding_workspace,
    materialize_coding_workspace,
)

logger = logging.getLogger(__name__)

try:  # pragma: no cover - exercised via the availability branch in tests
    from ag2 import Agent as _AG2Agent
    from ag2.acp import ACPConfig, ClaudeCodeConfig, CodexConfig, OpenCodeConfig

    _ACP_IMPORT_ERROR: Exception | None = None
except Exception as _import_exc:  # ImportError or missing-optional stub errors
    _AG2Agent = None  # type: ignore[misc, assignment]
    ACPConfig = None  # type: ignore[misc, assignment]
    _ACP_IMPORT_ERROR = _import_exc

DEFAULT_ACP_STAGING_ROOT = Path(".refinement_staging") / "acp_workspaces"

_MAX_PROVIDER_EVENTS = 200


def record_provider_event(event: Any, records: list[ProviderEventRecord]) -> None:
    """Translate one AG2 stream event into a bounded operational record.

    Captures plan updates, tool invocations, and mode changes as short
    summaries. Model reasoning (``ModelReasoning``) and raw message chunks are
    deliberately never recorded — operational events only, no chain of
    thought. Silently drops anything once the bound is reached.
    """
    if len(records) >= _MAX_PROVIDER_EVENTS:
        return
    from ag2.acp.events import ACPModeChange, ACPPlan
    from ag2.events.tool_events import BuiltinToolCallEvent

    if isinstance(event, ACPPlan):
        summary = "; ".join(f"[{entry.status}] {entry.content}" for entry in event.entries)
        if summary:
            records.append(ProviderEventRecord(kind="plan", summary=summary[:500]))
    elif isinstance(event, BuiltinToolCallEvent):
        name = str(getattr(event, "name", "") or "tool")
        records.append(ProviderEventRecord(kind="tool_call", summary=name[:500]))
    elif isinstance(event, ACPModeChange):
        records.append(ProviderEventRecord(kind="mode_change", summary=str(event.mode_id)[:500]))

# The only host environment variables that may reach the CLI agent subprocess.
# Everything else — including the Mozaiks runtime's own provider keys, Mongo
# URIs, and platform secrets — is withheld. Model-selection variables are
# adapter-owned and intentionally not forwarded from the host.
_ENV_PASSTHROUGH_KEYS = ("ANTHROPIC_API_KEY", "CODEX_API_KEY", "OPENAI_API_KEY")

_ADAPTERS = ("claude_code", "codex", "opencode")


def acp_available() -> bool:
    """True when the ``ag2[acp]`` extra is importable in this environment."""
    return _ACP_IMPORT_ERROR is None


def build_acp_agent_config(
    *,
    adapter: str,
    workspace_root: Path,
    turn_timeout_seconds: int,
    env_source: dict[str, str],
) -> Any:
    """Build the hardened ACPConfig for one provider execution.

    Every safety-relevant field is set explicitly rather than defaulted:
    the workspace is both ``cwd`` and ``fs_root``; the subprocess env is an
    explicit allowlist over ``env_source``; ``expose_tools=False`` (the AG2
    default is True) so no MCP gateway is started; ``allow_terminal=False``
    because agent-requested terminal commands would expand the provider beyond
    the mediated disposable-workspace file bridge; and
    ``elicitation_policy="decline"`` so the question capability is never
    advertised in headless execution. ``permission_policy="auto"`` is safe
    only because the blast radius is the disposable workspace plus the
    harvest filter.
    """
    if not acp_available():  # pragma: no cover - guarded by caller
        raise RuntimeError(f"ag2[acp] extra is not installed: {_ACP_IMPORT_ERROR}")
    if adapter not in _ADAPTERS:
        raise ValueError(f"Unknown ACP adapter {adapter!r}; expected one of {_ADAPTERS}")

    env = {key: env_source[key] for key in _ENV_PASSTHROUGH_KEYS if env_source.get(key)}
    preset = {
        "claude_code": ClaudeCodeConfig,
        "codex": CodexConfig,
        "opencode": OpenCodeConfig,
    }[adapter]
    return preset(
        cwd=str(workspace_root),
        fs_root=str(workspace_root),
        env=env or None,
        permission_policy="auto",
        elicitation_policy="decline",
        expose_tools=False,
        allow_terminal=False,
        turn_timeout=float(turn_timeout_seconds),
    )


def build_provider_prompt(request: CodingWorkerRequest, workspace: StagedCodingWorkspace) -> str:
    """Task framing for the CLI agent.

    Informative only — nothing here is load-bearing for safety. The editable
    set is enforced by the harvest, not by these instructions.
    """
    editable = "\n".join(f"- {path}" for path in sorted(workspace.editable_manifest))
    return "\n".join(
        [
            "You are performing one bounded, pre-approved code change in this",
            "workspace. The workspace contains copies of exactly the files in",
            "scope; there is no wider repository.",
            "",
            f"Request: {request.raw_user_request}",
            "",
            "Editable files (changes anywhere else are discarded):",
            editable,
            "",
            "Edit the files in place to satisfy the request. Do not create new",
            "files, do not delete files, and do not run commands. When you are",
            "done, reply with a short summary of what you changed and why.",
        ]
    )


class ACPCodingProvider:
    """One bounded CLI-agent execution per request, harvest-verified."""

    def __init__(
        self,
        *,
        config_loader: Any = load_control_plane_config,
        staging_root: Path | None = None,
        acp_config_factory: Callable[..., Any] | None = None,
        env_source: dict[str, str] | None = None,
    ) -> None:
        self._config_loader = config_loader
        self._staging_root = Path(staging_root) if staging_root is not None else DEFAULT_ACP_STAGING_ROOT
        self._acp_config_factory = acp_config_factory or build_acp_agent_config
        self._env_source = env_source

    @property
    def provider_id(self) -> str:
        return f"acp_{self._provider_config().adapter}"

    def _load_config(self) -> ControlPlaneConfig:
        config = self._config_loader()
        return config if isinstance(config, ControlPlaneConfig) else ControlPlaneConfig.model_validate(config)

    def _provider_config(self) -> ControlPlaneACPProviderConfig:
        return self._load_config().coding.providers.acp

    def _proposal(self, *, status: str, provider_id: str, **fields: Any) -> StagedPatchProposal:
        return StagedPatchProposal(
            proposal_id=uuid.uuid4().hex,
            provider_id=provider_id,
            status=status,  # type: ignore[arg-type]
            **fields,
        )

    async def execute(self, request: CodingWorkerRequest) -> StagedPatchProposal:
        provider_config = self._provider_config()
        provider_id = f"acp_{provider_config.adapter}"

        if not provider_config.enabled:
            return self._proposal(
                status="unavailable",
                provider_id=provider_id,
                error="ACP coding provider is disabled in refinement policy (coding.providers.acp.enabled).",
            )
        if not acp_available():
            return self._proposal(
                status="unavailable",
                provider_id=provider_id,
                error=f"ag2[acp] extra is not installed: {_ACP_IMPORT_ERROR}",
            )
        budget = provider_config.budget
        if len(request.files) > budget.max_files:
            return self._proposal(
                status="budget_exceeded",
                provider_id=provider_id,
                error=(
                    f"scoped file count {len(request.files)} exceeds the ACP provider budget "
                    f"max_files={budget.max_files}"
                ),
            )

        workspace_root = self._staging_root / request.app_id / uuid.uuid4().hex[:12]
        workspace: StagedCodingWorkspace | None = None
        try:
            workspace = materialize_coding_workspace(dict(request.files), workspace_root=workspace_root)
            return await self._run_turn(
                request=request,
                workspace=workspace,
                provider_config=provider_config,
                provider_id=provider_id,
            )
        except Exception as exc:
            logger.warning("ACP_CODING_PROVIDER_FAILED app=%s: %s", request.app_id, exc, exc_info=True)
            return self._proposal(status="failed", provider_id=provider_id, error=str(exc))
        finally:
            if workspace is not None:
                workspace.cleanup()

    async def _run_turn(
        self,
        *,
        request: CodingWorkerRequest,
        workspace: StagedCodingWorkspace,
        provider_config: ControlPlaneACPProviderConfig,
        provider_id: str,
    ) -> StagedPatchProposal:
        import os

        acp_config = self._acp_config_factory(
            adapter=provider_config.adapter,
            workspace_root=workspace.workspace_root,
            turn_timeout_seconds=provider_config.budget.max_wall_seconds,
            env_source=self._env_source if self._env_source is not None else dict(os.environ),
        )

        summary_text = ""
        finish_reason: str | None = None
        usage: dict[str, int] | None = None
        provider_model: str | None = None
        events: list[ProviderEventRecord] = []

        async with acp_config:
            agent = _AG2Agent("MozaiksACPCodingProvider", config=acp_config)
            prompt = build_provider_prompt(request, workspace)
            async with agent.run(prompt) as run:
                run.stream.subscribe(lambda event: record_provider_event(event, events))
                reply = await run.result()
            response = reply.response
            message = getattr(response, "message", None)
            summary_text = str(getattr(message, "content", "") or "").strip()
            finish_reason = getattr(response, "finish_reason", None)
            provider_model = getattr(response, "model", None)
            raw_usage = getattr(response, "usage", None)
            if raw_usage is not None:
                usage = {
                    key: value
                    for key, value in (
                        ("prompt_tokens", getattr(raw_usage, "prompt_tokens", None)),
                        ("completion_tokens", getattr(raw_usage, "completion_tokens", None)),
                        ("total_tokens", getattr(raw_usage, "total_tokens", None)),
                    )
                    if isinstance(value, int)
                }

        harvest = harvest_coding_workspace(workspace, allow_new_files=False, allow_deletes=False)

        if finish_reason == "timeout":
            return self._proposal(
                status="timeout",
                provider_id=provider_id,
                provider_model=provider_model,
                usage=usage,
                provider_events=events,
                error=(
                    f"ACP turn exceeded the provider budget max_wall_seconds="
                    f"{provider_config.budget.max_wall_seconds}; no changes were accepted."
                ),
            )
        if not harvest.clean:
            details = "; ".join(f"{violation.path} ({violation.kind})" for violation in harvest.violations)
            return self._proposal(
                status="rejected_scope",
                provider_id=provider_id,
                provider_model=provider_model,
                usage=usage,
                provider_events=events,
                error=f"workspace harvest found out-of-scope modifications: {details}",
            )

        return self._build_result_proposal(
            harvest=harvest,
            provider_id=provider_id,
            provider_model=provider_model,
            usage=usage,
            events=events,
            summary_text=summary_text,
            max_diff_bytes=provider_config.budget.max_diff_bytes,
        )

    def _build_result_proposal(
        self,
        *,
        harvest: WorkspaceHarvest,
        provider_id: str,
        provider_model: str | None,
        usage: dict[str, int] | None,
        events: list[ProviderEventRecord],
        summary_text: str,
        max_diff_bytes: int,
    ) -> StagedPatchProposal:
        changed = [entry for entry in harvest.files if entry.modified]
        if not changed:
            return self._proposal(
                status="empty",
                provider_id=provider_id,
                provider_model=provider_model,
                usage=usage,
                provider_events=events,
                error="ACP agent completed the turn without modifying any scoped file.",
            )

        diff_bytes = sum(len((entry.content or "").encode("utf-8")) for entry in changed)
        if diff_bytes > max_diff_bytes:
            return self._proposal(
                status="budget_exceeded",
                provider_id=provider_id,
                provider_model=provider_model,
                usage=usage,
                provider_events=events,
                error=(
                    f"harvested diff of {diff_bytes} bytes exceeds the ACP provider budget "
                    f"max_diff_bytes={max_diff_bytes}; no changes were accepted."
                ),
            )

        summary = summary_text or "ACP coding provider patch."
        return self._proposal(
            status="completed",
            provider_id=provider_id,
            provider_model=provider_model,
            usage=usage,
            provider_events=events,
            summary=summary[:2000],
            rationale=summary[:2000],
            changed_files=[
                ProposedFileChange(
                    path=entry.path,
                    op="create" if entry.op == "create" else "update",
                    content=entry.content or "",
                )
                for entry in changed
            ],
            owned_paths=[entry.path for entry in changed],
            validation_strategy_hint="local",
            needs_human_review=True,
        )


__all__ = [
    "ACPCodingProvider",
    "record_provider_event",
    "DEFAULT_ACP_STAGING_ROOT",
    "acp_available",
    "build_acp_agent_config",
    "build_provider_prompt",
]
