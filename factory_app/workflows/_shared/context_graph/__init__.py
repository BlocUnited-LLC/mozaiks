"""Shared workflow hooks and prompt packs for Context Graph context."""

from .prompt_pack import build_context_graph_prompt_pack, build_context_graph_unavailable_pack

__all__ = [
    "build_context_graph_prompt_pack",
    "build_context_graph_unavailable_pack",
]
