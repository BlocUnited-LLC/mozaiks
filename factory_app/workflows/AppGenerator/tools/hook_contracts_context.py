from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, Any, List

logger = logging.getLogger(__name__)


def _read_config_impacts_contract() -> str:
    root = Path(__file__).resolve().parents[4]
    contract_path = root / "docs" / "contracts" / "APPGENERATOR_CONFIG_IMPACTS.md"
    if not contract_path.exists():
        return ""

    text = contract_path.read_text(encoding="utf-8", errors="ignore")
    sections = []
    capture = False
    for line in text.splitlines():
        if line.strip().startswith("### Output: config_impacts.yaml"):
            capture = True
        if capture:
            sections.append(line)
        if capture and line.strip().startswith("## "):
            break
    return "\n".join(sections).strip()


def _connector_summary() -> str:
    root = Path(__file__).resolve().parents[4]
    loader_path = root / "app" / "services" / "connectors" / "loader.py"
    if not loader_path.exists():
        return ""

    return "\n".join(
        [
            "Runtime Connectors:",
            "- Use app.services.connectors.loader.load_connectors() to access platform/core APIs.",
            "- Platform services: hosting, discovery, social, community, growth, funding, governance, messaging, teams.",
            "- Core services: identity, billing.",
        ]
    )


def inject_contracts_context(agent, messages: List[Dict[str, Any]]) -> None:
    """Inject config impacts contract + runtime connector summary into agent system message."""
    try:
        contract = _read_config_impacts_contract()
        connectors = _connector_summary()

        if not contract and not connectors:
            return

        parts = []
        if contract:
            parts.append("[CONFIG IMPACTS CONTRACT]\n" + contract)
        if connectors:
            parts.append("[RUNTIME CONNECTORS]\n" + connectors)

        context_str = "\n\n".join(parts)
        header = "\n\n[CONTRACTS CONTEXT]"

        current_system_message = agent.system_message
        if "[CONTRACTS CONTEXT]" in current_system_message:
            base = current_system_message.split("[CONTRACTS CONTEXT]")[0].strip()
            new_system_message = f"{base}{header}\n{context_str}"
        else:
            new_system_message = f"{current_system_message}{header}\n{context_str}"

        if new_system_message != current_system_message:
            agent.update_system_message(new_system_message)
            logger.info("[%s] Injected contracts context", agent.name)

    except Exception as exc:
        logger.error("[%s] Failed to inject contracts context: %s", agent.name, exc)
