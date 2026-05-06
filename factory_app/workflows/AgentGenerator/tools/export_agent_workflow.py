"""Workflow-specific GitHub export wrapper for AgentGenerator outputs.

This wrapper:
1) Calls the shared Mozaiks Backend deploy pipeline (export_to_github).
2) Extracts high-level metadata from the bundle (agent + tool names).
3) Persists an app-scoped export record for downstream workflows (e.g., AppGenerator).

It intentionally does not implement business logic beyond metadata extraction.
"""

from __future__ import annotations

import yaml
import zipfile
from pathlib import PurePosixPath
from typing import Any, Dict, List, Optional, Tuple

from logs.logging_config import get_workflow_logger

from mozaiksai.core.workflow.generator_support.agent_endpoints import (
    resolve_agent_api_url,
    resolve_agent_websocket_url,
)
from mozaiksai.core.workflow.generator_support.workflow_exports import record_workflow_export
from .export_to_github import export_to_github_tool


def _find_zip_entry(names: List[str], suffix: str) -> Optional[str]:
    for name in names:
        if not isinstance(name, str):
            continue
        if name.endswith(suffix):
            return name
    return None


def _read_zip_yaml(zf: zipfile.ZipFile, member: str) -> Optional[Dict[str, Any]]:
    """Read and parse a YAML file from inside a zip."""
    try:
        raw = zf.read(member)
        text = raw.decode("utf-8", errors="replace")
        parsed = yaml.safe_load(text)
        return parsed if isinstance(parsed, dict) else {"_raw": parsed}
    except Exception:
        return None



def _extract_agent_names(agents_payload: Any) -> List[str]:
    if isinstance(agents_payload, dict):
        # common format: {"agents": {"AgentName": {...}}}
        inner = agents_payload.get("agents")
        if isinstance(inner, dict):
            return sorted([k for k in inner.keys() if isinstance(k, str) and k.strip()])
        # alternative: {"AgentName": {...}}
        return sorted([k for k in agents_payload.keys() if isinstance(k, str) and k.strip()])
    if isinstance(agents_payload, list):
        names: List[str] = []
        for item in agents_payload:
            if isinstance(item, dict):
                nm = item.get("name")
                if isinstance(nm, str) and nm.strip():
                    names.append(nm.strip())
        return sorted(list(dict.fromkeys(names)))
    return []


def _extract_tool_names(tools_payload: Any) -> List[str]:
    if not isinstance(tools_payload, dict):
        return []
    entries = tools_payload.get("tools")
    if not isinstance(entries, list):
        return []
    names: List[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        nm = entry.get("name") or entry.get("function")
        if isinstance(nm, str) and nm.strip():
            names.append(nm.strip())
    return sorted(list(dict.fromkeys(names)))

def _extract_websocket_config(zf: zipfile.ZipFile, names: List[str]) -> Optional[Dict[str, Any]]:
    """Extract websocket_config.yaml from bundle if present."""
    ws_entry = _find_zip_entry(names, "/websocket_config.yaml")
    if ws_entry:
        return _read_zip_yaml(zf, ws_entry)
    return None



def _extract_bundle_metadata(bundle_path: str) -> Tuple[Optional[str], List[str], List[str], Optional[Dict[str, Any]]]:
    """Return (workflow_name_in_bundle, agent_names, tool_names, websocket_config)."""

    zpath = PurePosixPath(bundle_path.replace("\\", "/"))
    if zpath.suffix.lower() != ".zip":
        return None, [], [], None

    try:
        with zipfile.ZipFile(bundle_path, "r") as zf:
            names = [n for n in zf.namelist() if isinstance(n, str)]
            agents_entry = _find_zip_entry(names, "/agents.yaml")
            tools_entry = _find_zip_entry(names, "/tools.yaml")

            wf_name = None
            if agents_entry:
                wf_name = agents_entry.split("/", 1)[0]
            elif tools_entry:
                wf_name = tools_entry.split("/", 1)[0]

            agent_names: List[str] = []
            tool_names: List[str] = []
            if agents_entry:
                agents_yaml = _read_zip_yaml(zf, agents_entry)
                if agents_yaml is not None:
                    agent_names = _extract_agent_names(agents_yaml)
            if tools_entry:
                tools_yaml = _read_zip_yaml(zf, tools_entry)
                if tools_yaml is not None:
                    tool_names = _extract_tool_names(tools_yaml)

            websocket_config = _extract_websocket_config(zf, names)
            return wf_name, agent_names, tool_names, websocket_config
    except Exception:
        return None, [], [], None


async def export_agent_workflow_to_github(
    *,
    app_id: str,
    bundle_path: str,
    repo_name: Optional[str] = None,
    commit_message: Optional[str] = None,
    user_id: Optional[str] = None,
    context_variables: Optional[Any] = None,
) -> Dict[str, Any]:
    wf_logger = get_workflow_logger(workflow_name="AgentGenerator", chat_id=None, app_id=app_id)
    wf_name_in_bundle, agent_names, tool_names, websocket_config = _extract_bundle_metadata(bundle_path)
    websocket_url = resolve_agent_websocket_url(app_id)
    api_url = resolve_agent_api_url(app_id)

    result = await export_to_github_tool.execute(
        app_id=app_id,
        bundle_path=bundle_path,
        repo_name=repo_name,
        commit_message=commit_message,
        user_id=user_id,
        workflow_type="agent-generator",
        context_variables=context_variables,
    )

    payload = result.model_dump()
    payload["workflow_type"] = "agent-generator"
    payload["bundle_workflow_name"] = wf_name_in_bundle
    payload["agent_names"] = agent_names
    payload["tool_names"] = tool_names
    payload["available_agents"] = agent_names
    payload["available_tools"] = tool_names
    payload["agent_websocket_url"] = websocket_url
    payload["agent_api_url"] = api_url
    payload["websocket_config"] = websocket_config

    if result.success:
        try:
            await record_workflow_export(
                app_id=app_id,
                user_id=user_id,
                workflow_type="agent-generator",
                repo_url=result.repo_url,
                job_id=result.job_id,
                meta={
                    "bundle_workflow_name": wf_name_in_bundle,
                    "agent_names": agent_names,
                    "tool_names": tool_names,
                    "available_agents": agent_names,
                    "available_tools": tool_names,
                },
                extra_fields={
                    "bundle_workflow_name": wf_name_in_bundle,
                    "agent_names": agent_names,
                    "tool_names": tool_names,
                    "available_agents": agent_names,
                    "available_tools": tool_names,
                    "agent_websocket_url": websocket_url,
                    "agent_api_url": api_url,
                    "websocket_config": websocket_config,
                    "repoFullName": result.repo_full_name,
                    "baseCommitSha": result.base_commit_sha,
                    "workflowRunUrl": result.workflow_run_url,
                    "deploymentUrl": result.deployment_url,
                },
            )
        except Exception as exc:
            wf_logger.warning(f"[EXPORT] Failed to record workflow export metadata: {exc}")

    return payload


__all__ = ["export_agent_workflow_to_github"]
