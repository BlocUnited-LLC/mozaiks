from __future__ import annotations

from typing import Any


def inject_context_graph_context(agent: Any, messages: list[dict[str, Any]]) -> None:
    """Inject preloaded Context Graph pack data into an agent system prompt.

    This hook is synchronous by design. It consumes context already attached to
    the workflow run instead of reaching into persistence from workflow-local
    agent construction.
    """
    context_data = _context_data(getattr(agent, "context_variables", None))
    graph_pack = context_data.get("context_graph_pack")
    if not graph_pack:
        return

    body = _format_context_graph_pack(
        graph_pack=graph_pack,
        agent_name=str(getattr(agent, "name", "") or ""),
    )
    if not body:
        return

    current = str(getattr(agent, "system_message", "") or "")
    updated = _replace_section(current, "[CONTEXT GRAPH]", body)
    if updated != current and hasattr(agent, "update_system_message"):
        agent.update_system_message(updated)


def _context_data(context_variables: Any) -> dict[str, Any]:
    if context_variables is None:
        return {}
    if isinstance(context_variables, dict):
        return context_variables
    data = getattr(context_variables, "data", None)
    if isinstance(data, dict):
        return data
    if hasattr(context_variables, "get"):
        out: dict[str, Any] = {}
        for key in ("context_graph_pack",):
            try:
                value = context_variables.get(key)
            except Exception:
                value = None
            if value is not None:
                out[key] = value
        return out
    return {}


def _format_context_graph_pack(*, graph_pack: Any, agent_name: str) -> str:
    if isinstance(graph_pack, dict):
        status = graph_pack.get("status")
        reason = graph_pack.get("reason")
        source = graph_pack.get("source")
        graph_id = graph_pack.get("graph_id") or graph_pack.get("graphId")
        stale_status = graph_pack.get("stale_status") or graph_pack.get("staleStatus")
        selected_paths = (
            graph_pack.get("relevant_paths")
            or graph_pack.get("selected_file_paths")
            or graph_pack.get("candidate_files")
            or graph_pack.get("file_tree")
            or []
        )
        matched_nodes = graph_pack.get("matched_nodes") or graph_pack.get("related_nodes") or []
        matched_edges = graph_pack.get("matched_edges") or graph_pack.get("related_edges") or []
        semantic_items = (
            graph_pack.get("semantic_annotation_targets")
            or graph_pack.get("semantic_annotation_candidates")
            or (graph_pack.get("semantic_annotation_request") or {}).get("items")
            or []
        )
        warnings = graph_pack.get("warnings") or []
        lines = [
            f"Agent: {agent_name}" if agent_name else "",
            f"Status: {status}" if status else "",
            f"Reason: {reason}" if reason else "",
            f"Source: {source}" if source else "",
            f"Graph: {graph_id}" if graph_id else "",
            f"Stale status: {stale_status}" if stale_status else "",
        ]
        if selected_paths:
            lines.append("Relevant paths:")
            for item in selected_paths[:12]:
                if isinstance(item, dict):
                    path = item.get("path") or item.get("label") or item.get("node_id")
                else:
                    path = item
                if path:
                    lines.append(f"- {path}")
        if matched_nodes:
            lines.append("Relevant graph nodes:")
            for item in matched_nodes[:12]:
                if isinstance(item, dict):
                    label = item.get("label") or item.get("node_id")
                    node_type = item.get("node_type")
                    lines.append(f"- {label} ({node_type})" if node_type else f"- {label}")
        if matched_edges:
            lines.append("Relevant relationships:")
            for item in matched_edges[:8]:
                if isinstance(item, dict):
                    edge_type = item.get("edge_type")
                    source_node_id = item.get("source_node_id")
                    target_node_id = item.get("target_node_id")
                    if edge_type and source_node_id and target_node_id:
                        lines.append(f"- {source_node_id} -[{edge_type}]-> {target_node_id}")
        if semantic_items:
            lines.append("Semantic annotation targets:")
            for item in semantic_items[:8]:
                if isinstance(item, dict):
                    label = item.get("qualified_name") or item.get("label") or item.get("node_id")
                    role = item.get("contract_role") or item.get("symbol_kind")
                    path = item.get("path")
                    suffix = f" [{role}]" if role else ""
                    lines.append(f"- {label}{suffix} at {path}" if path else f"- {label}{suffix}")
        if warnings:
            lines.append("Warnings:")
            for warning in warnings[:6]:
                lines.append(f"- {warning}")
        return "\n".join(line for line in lines if line).strip()
    return str(graph_pack).strip()[:4000]


def _replace_section(prompt: str, header: str, body: str) -> str:
    section = f"{header}\n{body.strip()}"
    if header not in prompt:
        return f"{prompt.rstrip()}\n\n{section}".strip()
    before, _sep, after = prompt.partition(header)
    next_marker = after.find("\n[")
    if next_marker >= 0:
        return f"{before.rstrip()}\n\n{section}\n{after[next_marker:]}".strip()
    return f"{before.rstrip()}\n\n{section}".strip()
