
# FILE: mozaiksai/core/adapters/__init__.py
# DESCRIPTION: Engine adapters — implementations of port protocols.
# ==============================================================================

from .ag2_agent_runner import AG2StructuredAgentRunner
from .ag2_orchestration import AG2OrchestrationAdapter
from .docker_sandbox import DockerSandboxAdapter, docker_available, get_docker_sandbox
from .e2b_sandbox import E2BSandboxAdapter, get_e2b_sandbox
from .http_app_backend import HttpAppBackendAdapter, get_app_backend


def get_sandbox_adapter(strategy: str):
    """Return the SandboxPort adapter for the given validation strategy.

    Raises ValueError for unknown strategies; raises RuntimeError when the
    requested provider is not available (missing API key or daemon).
    """
    import os

    if strategy == "e2b":
        if not os.getenv("E2B_API_KEY", "").strip():
            raise RuntimeError(
                "E2B_API_KEY is not configured. "
                "Set E2B_API_KEY or choose a different MOZAIKS_APP_VALIDATION_STRATEGY."
            )
        return get_e2b_sandbox()

    if strategy == "docker":
        if not docker_available():
            raise RuntimeError(
                "Docker is not available. Install Docker Desktop or Docker Engine "
                "and ensure the daemon is running, or choose a different "
                "MOZAIKS_APP_VALIDATION_STRATEGY."
            )
        return get_docker_sandbox()

    raise ValueError(
        f"Unknown sandbox strategy {strategy!r}. Expected 'e2b' or 'docker'."
    )


__all__ = [
    "AG2OrchestrationAdapter",
    "AG2StructuredAgentRunner",
    "DockerSandboxAdapter",
    "E2BSandboxAdapter",
    "HttpAppBackendAdapter",
    "docker_available",
    "get_app_backend",
    "get_docker_sandbox",
    "get_e2b_sandbox",
    "get_sandbox_adapter",
]
