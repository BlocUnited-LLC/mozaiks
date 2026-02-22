"""Runtime execution context used by tool invocation paths."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(slots=True, kw_only=True)
class SandboxExecutionResult:
    success: bool
    output: Any = None
    error: str | None = None
    stdout: str = ""
    stderr: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


class SandboxPort(Protocol):
    """Executes code in an isolated sandbox runtime."""

    async def execute_python(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        env: dict[str, str],
        requirements: list[str] | None = None,
        source_files: dict[str, str] | None = None,
    ) -> SandboxExecutionResult:
        ...

    async def execute_node(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        env: dict[str, str],
        npm_packages: list[str] | None = None,
        source_files: dict[str, str] | None = None,
    ) -> SandboxExecutionResult:
        ...


class SecretsPort(Protocol):
    """Resolves scoped secrets for a run."""

    async def get_secrets(self, *, scope: str) -> dict[str, str]:
        ...


@dataclass(slots=True, kw_only=True)
class RuntimeContext:
    """Execution-scoped runtime context injected by callers."""

    sandbox: SandboxPort | None = None
    secrets: SecretsPort | None = None
    env: dict[str, str] = field(default_factory=dict)
    ui_tool_submissions: dict[str, dict[str, Any]] = field(default_factory=dict)
