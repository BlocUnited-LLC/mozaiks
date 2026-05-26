"""
Code Context Tool System

Provides generation-time code understanding capabilities for AppGenerator/AgentGenerator workflows.
- Neutral language-aware extraction (TreeSitter-based)
- Agent-specific formatting
- Workspace-level persistence (survives across sessions)
- Diff detection for modification workflows
"""

from .tools import (
    index_codebase,
    get_code_context_for_agent,
    get_code_diff,
    CODE_CONTEXT_TOOLS
)

__all__ = [
    "index_codebase",
    "get_code_context_for_agent",
    "get_code_diff",
    "CODE_CONTEXT_TOOLS"
]
