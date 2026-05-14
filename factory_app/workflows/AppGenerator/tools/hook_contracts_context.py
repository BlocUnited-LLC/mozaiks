from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Dict, Any, List, Optional

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


def _context_get(context_variables: Any, key: str, default: Any = None) -> Any:
    if context_variables is None:
        return default
    getter = getattr(context_variables, "get", None)
    if callable(getter):
        try:
            return getter(key, default)
        except TypeError:
            value = getter(key)
            return default if value is None else value
    data = getattr(context_variables, "data", None)
    if isinstance(data, dict):
        return data.get(key, default)
    if isinstance(context_variables, dict):
        return context_variables.get(key, default)
    return default


def _format_connector_inventory_block(inventory: Dict[str, Any]) -> str:
    required = inventory.get("required_services") or []
    ready = inventory.get("ready_services") or []
    missing = inventory.get("missing_required_services") or []
    known_unready = inventory.get("known_but_unready_required_services") or []
    display_names = inventory.get("display_names") or {}

    def _display(service: str) -> str:
        return str(display_names.get(service) or service.replace("_", " ").title())

    lines = ["App Connector Inventory:"]
    lines.append(f"- Ready connectors: {', '.join(_display(service) for service in ready) if ready else 'none'}")
    lines.append(f"- Required by current plan: {', '.join(_display(service) for service in required) if required else 'none'}")
    if known_unready:
        lines.append(
            f"- Known but not runtime-ready: {', '.join(_display(service) for service in known_unready)}"
        )
    if missing:
        lines.append(
            f"- Still missing for direct runtime use: {', '.join(_display(service) for service in missing)}"
        )
    lines.append(
        "- Never invent a separate API-key table or module collection for these connectors."
    )
    lines.append(
        "- If the product needs an integration that is not ready here, explicitly call it out as missing and recommend collecting/configuring it instead of assuming access."
    )
    return "\n".join(lines)


def _app_connector_inventory_summary(agent: Any) -> str:
    try:
        from mozaiksai.core.workflow.generator_support.connector_service import get_connector_inventory
    except Exception:
        return ""

    app_id = _context_get(getattr(agent, "context_variables", None), "app_id")
    if not app_id:
        return ""

    required_services = _context_get(getattr(agent, "context_variables", None), "required_connector_services", [])
    try:
        import asyncio
        running_loop = None
        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            running_loop = None

        if running_loop and running_loop.is_running():
            holder: Dict[str, Any] = {}
            error_holder: Dict[str, Exception] = {}

            def _runner() -> None:
                try:
                    holder["inventory"] = asyncio.run(
                        get_connector_inventory(str(app_id), required_services=required_services)
                    )
                except Exception as exc:  # pragma: no cover - defensive
                    error_holder["error"] = exc

            thread = threading.Thread(target=_runner, daemon=True)
            thread.start()
            thread.join(timeout=5.0)
            if thread.is_alive():
                logger.warning(
                    "[%s] Connector inventory fetch timed out after 5 s — skipping",
                    getattr(agent, "name", "?"),
                )
                return ""
            if "error" in error_holder:
                raise error_holder["error"]
            inventory = holder.get("inventory")
        else:
            inventory = asyncio.run(get_connector_inventory(str(app_id), required_services=required_services))
    except Exception as exc:
        logger.debug("[%s] Failed to build app connector inventory summary: %s", getattr(agent, "name", "?"), exc)
        return ""

    if not isinstance(inventory, dict):
        return ""
    return _format_connector_inventory_block(inventory)


def inject_contracts_context(agent, messages: List[Dict[str, Any]]) -> None:
    """Inject config impacts contract + runtime connector summary into agent system message."""
    try:
        contract = _read_config_impacts_contract()
        connectors = _connector_summary()
        app_connectors = _app_connector_inventory_summary(agent)

        if not contract and not connectors and not app_connectors:
            return

        parts = []
        if contract:
            parts.append("[CONFIG IMPACTS CONTRACT]\n" + contract)
        if connectors:
            parts.append("[RUNTIME CONNECTORS]\n" + connectors)
        if app_connectors:
            parts.append("[APP CONNECTOR INVENTORY]\n" + app_connectors)

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
