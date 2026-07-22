# ==============================================================================
# FILE: mozaiksai/core/workflow/workflow_manager.py
# DESCRIPTION: Loads workflow declaratives, caches parsed configs, and indexes workflow UI tool metadata.
# ==============================================================================

import importlib
import json
import os
import sys
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from logs.logging_config import get_workflow_logger
from mozaiksai.core.runtime.app.ai_config import resolve_runtime_ai_config

from .contract_validation import validate_workflow_context_contract
from .declarative import (
    parse_a2a_config,
    parse_agents_config,
    parse_context_variables_config,
    parse_middleware_config,
    parse_orchestrator_config,
    parse_structured_outputs_config,
    parse_tools_config,
    parse_transition_graph_config,
    parse_ui_config,
)
from .paths import (
    discover_workflow_paths,
    resolve_active_app_root,
    resolve_workflow_path,
    resolve_workflows_root,
)

logger = get_workflow_logger(workflow_name="unified_workflow_manager")

@dataclass
class WorkflowInfo:
    """Container for complete workflow information"""
    name: str
    config: dict[str, Any]
    path: str
    status: str = "loaded"
    module: Any | None = None
    tools_loaded: bool = False
    error: str | None = None
    
    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary representation"""
        return {
            'name': self.name,
            'config': self.config,
            'path': self.path,
            'status': self.status,
            'module': self.module.__name__ if self.module else None,
            'tools_loaded': self.tools_loaded,
            'error': self.error
        }

class UnifiedWorkflowManager:
    """Unified workflow manager focusing on config + UI tool metadata.

    Backend agent tools are bound directly during agent creation; prompt
    middleware declarations are compiled into AG2 1.0 beta middleware.
    """

    _instance = None
    _ALLOWED_STARTUP_MODES = {"AgentDriven", "UserDriven", "BackendOnly"}

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, workflows_base_path: str | None = None):
        if hasattr(self, "_initialized"):
            return
        # Core caches / registries
        self.workflows_base_path = resolve_workflows_root(workflows_base_path)
        self._ai_config: dict[str, Any] = {}
        self._workflows: dict[str, WorkflowInfo] = {}
        self._workflow_paths: dict[str, Path] = {}
        self._config_cache: dict[str, dict[str, Any]] = {}
        self._ui_registry: dict[str, dict[str, Any]] = {}
        self._ui_tool_path_cache: dict[str, str] = {}
        self._ui_loaded_workflows: set[str] = set()
        # Runtime handler and metadata registries
        self._handlers: dict[str, Callable[..., Awaitable[Any]]] = {}
        self._handler_metadata: dict[str, dict[str, Any]] = {}
        self._initialized = False
        # Initial load
        self._load_all_workflows()
        self._initialized = True
        logger.info(
            "Initialized unified workflow manager with %s workflows", len(self._workflows))

    # ------------------------- UI TOOLS -------------------------
    def _load_workflow_tools(self, workflow_path: str, *, tools_payload: dict[str, Any] | None = None) -> None:
        from pathlib import Path as _P
        tools_yaml_path = _P(workflow_path) / "tools.yaml"

        workflow_name = _P(workflow_path).name
        if workflow_name in self._ui_loaded_workflows:
            return

        try:
            data: dict[str, Any] = {}
            if isinstance(tools_payload, dict):
                data = dict(tools_payload)
            elif tools_yaml_path.exists():
                with open(tools_yaml_path, encoding='utf-8') as f:
                    raw = yaml.safe_load(f) or {}
                data = parse_tools_config(raw)

            if not data:
                logger.debug("No tools.yaml for workflow %s", workflow_name)
                return
            
            entries = data.get('tools', [])
            if not isinstance(entries, list):
                logger.warning("Invalid tools list in workflow %s", workflow_name)
                return

            ui_ct = 0
            seen_ids: set[str] = set()

            for entry in entries:
                if not isinstance(entry, dict):
                    continue

                # Enforce new schema only (file + function required for tools entry)
                file_name = entry.get('file')
                function = entry.get('function')
                if not (isinstance(file_name, str) and file_name.strip() and isinstance(function, str) and function.strip()):
                    logger.warning("Skipping tool entry without required 'file' and 'function' fields in workflow '%s'", workflow_name)
                    continue
                tool_id = function.strip()

                if not tool_id:
                    # nothing we can do
                    continue

                # Ensure uniqueness (avoid collisions across entries)
                base_tool_id = tool_id
                suffix = 2
                while tool_id in seen_ids:
                    tool_id = f"{base_tool_id}_{suffix}"
                    suffix += 1
                seen_ids.add(tool_id)

                # Determine module path (best-effort)
                mod = file_name.replace('\\', '/').split('/')[-1].rsplit('.', 1)[0]
                module_path = f"workflows.{workflow_name}.tools.{mod}:{function}"

                # Agent field
                agent = entry.get('agent')

                # UI detection
                ui_block = entry.get('ui') if isinstance(entry.get('ui'), dict) else None
                tool_type = entry.get('tool_type')
                is_ui_surface = tool_type in {'UI_Tool', 'UI_Surface'}

                # inside _load_workflow_tools(), in the for-entry loop:
                if is_ui_surface:
                    ui_ct += 1
                    component = ui_block.get('component') if ui_block else None
                    mode = ui_block.get('mode') if ui_block else None
                    fn_name = function
                    lookup_id = component if component else tool_id

                    rec = {
                        'workflow_name': workflow_name,
                        'tool_id': lookup_id,        # component name for lookup (ActionPlan, AgentAPIKeyInput, etc.)
                        'fn': fn_name,               # raw function name for runtime event matching
                        'agent': agent,
                        'path': module_path,
                        'component': component,
                        'mode': mode,
                        'workflow_primitive': ui_block.get('workflow_primitive') if ui_block else None,
                        'realization': ui_block.get('realization') if ui_block else None,
                        'ui_contract': entry.get('ui_contract') if isinstance(entry.get('ui_contract'), dict) else None,
                        'classification': 'ui',
                        'tool_type': tool_type,
                    }

                    key = f"{workflow_name}.{lookup_id}"
                    self._ui_registry[key] = rec
                    if module_path:
                        self._ui_tool_path_cache[module_path] = key
                    continue


                # Non-UI tools are ignored here on purpose

            # Process lifecycle_tools section (same UI detection logic)
            lifecycle_entries = data.get('lifecycle_tools', [])
            if isinstance(lifecycle_entries, list):
                for entry in lifecycle_entries:
                    if not isinstance(entry, dict):
                        continue
                    
                    file_name = entry.get('file')
                    function = entry.get('function')
                    if not (isinstance(file_name, str) and file_name.strip() and isinstance(function, str) and function.strip()):
                        continue
                    
                    tool_id = function.strip()
                    if not tool_id:
                        continue
                    
                    # Ensure uniqueness
                    base_tool_id = tool_id
                    suffix = 2
                    while tool_id in seen_ids:
                        tool_id = f"{base_tool_id}_{suffix}"
                        suffix += 1
                    seen_ids.add(tool_id)
                    
                    mod = file_name.replace('\\', '/').split('/')[-1].rsplit('.', 1)[0]
                    module_path = f"workflows.{workflow_name}.tools.{mod}:{function}"
                    agent = entry.get('agent')
                    
                    # UI detection for lifecycle tools
                    ui_block = entry.get('ui') if isinstance(entry.get('ui'), dict) else None
                    tool_type = entry.get('tool_type')
                    is_ui_surface = tool_type in {'UI_Tool', 'UI_Surface'}
                    
                    if is_ui_surface:
                        ui_ct += 1
                        component = ui_block.get('component') if ui_block else None
                        mode = ui_block.get('mode') if ui_block else None
                        fn_name = function
                        lookup_id = component if component else tool_id
                        
                        rec = {
                            'workflow_name': workflow_name,
                            'tool_id': lookup_id,        # component name for lookup
                            'fn': fn_name,
                            'agent': agent,
                            'path': module_path,
                            'component': component,
                            'mode': mode,
                            'workflow_primitive': ui_block.get('workflow_primitive') if ui_block else None,
                            'realization': ui_block.get('realization') if ui_block else None,
                            'ui_contract': entry.get('ui_contract') if isinstance(entry.get('ui_contract'), dict) else None,
                            'classification': 'ui',
                            'tool_type': tool_type,
                            'trigger': entry.get('trigger'),  # preserve lifecycle metadata
                        }
                        
                        key = f"{workflow_name}.{lookup_id}"
                        self._ui_registry[key] = rec
                        if module_path:
                            self._ui_tool_path_cache[module_path] = key

            self._ui_loaded_workflows.add(workflow_name)
            logger.info("Tools loaded for %s: ui=%s", workflow_name, ui_ct)
        except Exception as e:  # pragma: no cover
            logger.error("Failed parsing tool config for %s: %s", workflow_name, e, exc_info=True)


    def get_ui_tool_record(self, tool_path_or_id: str) -> dict[str, Any] | None:
        """Lookup UI tool record by module path or tool id.

        Accepts either the full python path or the tool_id.
        """
        registry_key = self._ui_tool_path_cache.get(tool_path_or_id)
        if registry_key and registry_key in self._ui_registry:
            return self._ui_registry[registry_key]
        # Fallback treat argument as tool_id
        for rec in self._ui_registry.values():
            if rec.get('tool_id') == tool_path_or_id or rec.get('fn') == tool_path_or_id:
                return rec
        return None

    def iter_ui_tools(self) -> list[dict[str, Any]]:
        return list(self._ui_registry.values())

    def detect_ui_tool_call(self, event: Any) -> tuple[bool, dict[str, Any]]:
        name = getattr(event, "tool_name", None)
        if not isinstance(name, str) or not name:
            return False, {}
        for rec in self._ui_registry.values():
            if rec.get('tool_id') == name or rec.get('fn') == name:
                return True, rec
        return False, {}

    # ========================================================================
    # DISCOVERY & LOADING
    # ========================================================================
    
    def discover_workflows(self) -> list[str]:
        """Discover all available workflows in the workflows directory."""
        if not self.workflows_base_path.exists():
            logger.warning("Workflows directory not found: %s", self.workflows_base_path)
            return []

        workflows = discover_workflow_paths(self.workflows_base_path)
        self._workflow_paths = {name.lower(): path for name, path in workflows.items()}
        for name, path in workflows.items():
            logger.debug("Discovered workflow (orchestrator.yaml): %s @ %s", name, path)
        return list(workflows.keys())
    
    def _load_all_workflows(self) -> None:
        """Load all workflow configs and initialize them"""
        try:
            self._ai_config = self._load_ai_config()
            workflow_names = self.discover_workflows()

            if not workflow_names:
                logger.warning("WORKFLOW_LOAD_EMPTY: no workflows found in the workflows directory")
                return

            for workflow_name in workflow_names:
                try:
                    self._load_single_workflow(workflow_name)
                except Exception as e:
                    logger.error("WORKFLOW_LOAD_FAILED workflow=%s: %s", workflow_name, e, exc_info=True)
                    # Store error info for debugging
                    workflow_path = self.resolve_workflow_path(workflow_name)
                    self._workflows[workflow_name.lower()] = WorkflowInfo(
                        name=workflow_name,
                        config={},
                        path=str(workflow_path or (self.workflows_base_path / workflow_name)),
                        status="error",
                        error=str(e)
                    )

            # Emit a startup summary so partial-load degradation is visible in logs.
            ok_names = [n for n, w in self._workflows.items() if w.status != "error"]
            err_names = [n for n, w in self._workflows.items() if w.status == "error"]
            if err_names:
                logger.warning(
                    "WORKFLOW_LOAD_DEGRADED: %d/%d workflows loaded. Failed: %s",
                    len(ok_names),
                    len(ok_names) + len(err_names),
                    ", ".join(sorted(err_names)),
                )
            else:
                logger.info("WORKFLOW_LOAD_OK: %d workflows loaded.", len(ok_names))

        except Exception as e:
            logger.error("WORKFLOW_LOAD_ALL_FAILED: %s", e, exc_info=True)
    
    def _load_single_workflow(self, workflow_name: str) -> WorkflowInfo:
        """Load a single workflow with all its components"""
        workflow_path = self.resolve_workflow_path(workflow_name)
        
        if workflow_path is None or not workflow_path.exists():
            raise ValueError(f"Workflow not found: {workflow_name}")
        # Load configuration directly from modular YAML files.
        config = self._load_modular_workflow_config(workflow_path)
        if not config:
            logger.warning("WORKFLOW_CONFIG_EMPTY workflow=%s", workflow_name)
            config = {}
        self._validate_orchestrator_contract(workflow_name, config)
        validate_workflow_context_contract(
            workflow_name=workflow_name,
            workflow_config=config,
            workflow_path=workflow_path,
        )

        workflow_info = WorkflowInfo(name=workflow_name, config=config, path=str(workflow_path))

        # UI tools
        try:
            self._load_workflow_tools(
                str(workflow_path),
                tools_payload={
                    "tools": config.get("tools", []),
                    "lifecycle_tools": config.get("lifecycle_tools", []),
                },
            )
            has_ui_tools = any(r.get('workflow_name') == workflow_name for r in getattr(self, '_ui_registry', {}).values())
            workflow_info.tools_loaded = has_ui_tools
        except Exception as e:  # pragma: no cover
            logger.warning("Could not load UI tools for %s: %s", workflow_name, e)

        # Optional module
        try:
            init_file = workflow_path / "__init__.py"
            if init_file.exists():
                module_path = f"workflows.{workflow_name}"
                workflow_info.module = importlib.import_module(module_path)
        except ImportError as e:  # pragma: no cover
            logger.debug("No module found for workflow %s: %s", workflow_name, e)

        normalized_name = workflow_name.lower()
        self._workflows[normalized_name] = workflow_info
        self._workflow_paths[normalized_name] = workflow_path
        self._config_cache[normalized_name] = config
        logger.info("Successfully loaded workflow: %s", workflow_name)
        return workflow_info

    def resolve_workflow_path(self, workflow_name: str) -> Path | None:
        normalized_name = str(workflow_name or "").strip().lower()
        if not normalized_name:
            return None
        cached = self._workflow_paths.get(normalized_name)
        if cached and cached.exists():
            return cached
        resolved = resolve_workflow_path(workflow_name, self.workflows_base_path)
        if resolved is not None:
            self._workflow_paths[normalized_name] = resolved
        return resolved
    
    # ========================================================================
    # CONFIGURATION ACCESS API
    # ========================================================================
    
    def get_config(self, workflow_name: str) -> dict[str, Any]:
        """Get configuration for a workflow type"""
        normalized_name = workflow_name.lower()
        return self._config_cache.get(normalized_name, {})
    
    def has_human_in_the_loop(self, workflow_name: str) -> bool:
        """Check if workflow requires human interaction"""
        config = self.get_config(workflow_name)
        val = config.get("human_in_the_loop", False)
        # Normalize common representations to boolean
        if isinstance(val, bool):
            return val
        try:
            if isinstance(val, (int, float)):
                return bool(int(val))
        except Exception:
            pass
        if isinstance(val, str):
            v = val.strip().lower()
            if v in {"true", "yes", "1", "on", "always"}:
                return True
            if v in {"false", "no", "0", "off", "never"}:
                return False
        return False
    
    def get_initial_message(self, workflow_name: str) -> str | None:
        """Get initial message for workflow"""
        config = self.get_config(workflow_name)
        return config.get("initial_message")
    
    def get_visual_agents(self, workflow_name: str) -> list[str]:
        """Return list of agents considered 'visual' for chat rendering.

        The canonical representation is the top-level "visual_agents" list in the
        workflow configuration. Each entry should be a string agent name that is
        allowed to surface text/UI artifacts to the end user.
        """
        config = self.get_config(workflow_name)
        visual_agents = config.get("visual_agents")
        if not isinstance(visual_agents, list):
            return []
        return [str(agent) for agent in visual_agents if isinstance(agent, str)]

    def get_auto_tool_agents(self, workflow_name: str) -> set[str]:
        """Return set of agent names that have auto-invoke tools.

        Derives auto-tool agents from tools.yaml - any agent with a tool marked
        `auto_tool_call: true` is considered an auto-tool agent. agents.yaml does
        not define a matching auto-tool field.

        Auto-tool agents emit structured outputs that trigger automatic tool calls.
        Their text messages (containing agent_message) should be suppressed to avoid
        duplication since the agent_message also appears in the tool_call payload.

        Returns:
            Set of agent names with auto_tool_call tools (e.g., {"GapAnalysisAgent"})
        """
        config = self.get_config(workflow_name)
        tools_list = config.get("tools", [])

        if not isinstance(tools_list, list):
            return set()

        auto_tool_agents: set[str] = set()

        for entry in tools_list:
            if not isinstance(entry, dict):
                continue

            # Check auto_tool_call flag
            auto_tool_call = entry.get("auto_tool_call", False)
            if not auto_tool_call:
                continue

            # Extract agent name(s)
            agent_field = entry.get("agent") or entry.get("caller")
            if isinstance(agent_field, str):
                auto_tool_agents.add(agent_field)
            elif isinstance(agent_field, (list, tuple)):
                for agent in agent_field:
                    if isinstance(agent, str):
                        auto_tool_agents.add(agent)

        return auto_tool_agents

    def get_ui_hidden_triggers(self, workflow_name: str) -> dict[str, set[str]]:
        """Return mapping of agent_name -> set of ui_hidden trigger values.

        Extracts state variables with ui_hidden: true from context_variables.yaml
        and returns a dict mapping each source agent to the set of trigger values
        that should be hidden from the UI.

        Returns:
            Dict mapping agent_name to Set of trigger values (e.g., {"InterviewAgent": {"NEXT"}})
        """
        config = self.get_config(workflow_name)
        context_vars = config.get("context_variables", {})
        definitions = context_vars.get("definitions", {}) if isinstance(context_vars, dict) else {}

        hidden_triggers: dict[str, set[str]] = {}
        if not isinstance(definitions, dict):
            return hidden_triggers

        for _, var_config in definitions.items():
            if not isinstance(var_config, dict):
                continue
            source = var_config.get('source', {})
            if source.get('type') != 'state':
                continue

            # Parse triggers[] for ui_hidden triggers
            if isinstance(source.get('triggers'), list):
                for trigger in source['triggers']:
                    if not isinstance(trigger, dict):
                        continue
                    if trigger.get('ui_hidden') is True:
                        agent = trigger.get('agent')
                        match = trigger.get('match', {})
                        trigger_value = None
                        if isinstance(match, dict):
                            # Support both {equals: "NEXT"} and {contains: "NEXT"}
                            trigger_value = match.get('equals') or match.get('contains')
                        if isinstance(agent, str) and agent and isinstance(trigger_value, str) and trigger_value:
                            hidden_triggers.setdefault(agent, set()).add(trigger_value)

        return hidden_triggers

    def get_structured_output_registry(self, workflow_name: str) -> dict[str, str | None]:
        """Return a normalized mapping: agent_name -> model_name or None."""
        config = self.get_config(workflow_name)
        so = config.get("structured_outputs") or {}
        reg = so.get("registry")

        normalized: dict[str, str | None] = {}

        if isinstance(reg, dict):
            for agent, model in reg.items():
                if isinstance(agent, str):
                    normalized[agent] = model if isinstance(model, str) else None

        return normalized

    def get_agent_structured_outputs_config(self, workflow_name: str) -> dict[str, bool]:
        """Return agent -> bool indicating whether a structured model is assigned."""
        reg = self.get_structured_output_registry(workflow_name)
        return {agent: (model is not None) for agent, model in reg.items()}
    
    def get_all_workflow_names(self) -> list[str]:
        """Get list of all loaded workflow names"""
        return [info.name for info in self._workflows.values() if info.status == "loaded"]

    def list_workflows(self) -> list[str]:
        """Alias for get_all_workflow_names() — returns all loaded workflow names."""
        return self.get_all_workflow_names()

    # ========================================================================
    # LIFECYCLE MANAGEMENT API
    # ========================================================================
    
    def reload_workflow(self, workflow_name: str) -> dict[str, Any]:
        """Hot-reload a workflow and its tools"""
        normalized_name = workflow_name.lower()
        
        # Reload module if it exists
        if normalized_name in self._workflows:
            workflow_info = self._workflows[normalized_name]
            if workflow_info.module:
                try:
                    module_name = getattr(workflow_info.module, "__name__", "")
                    if module_name and module_name in sys.modules:
                        importlib.reload(workflow_info.module)
                        logger.info("Reloaded workflow module: %s", workflow_name)
                    else:
                        logger.debug(
                            "Workflow module reload skipped for %s because %s is not in sys.modules",
                            workflow_name,
                            module_name or "<unknown>",
                        )
                except Exception as e:
                    logger.error("WORKFLOW_MODULE_RELOAD_FAILED workflow=%s: %s", workflow_name, e, exc_info=True)
        
        # Reload embedded UI tool metadata
        try:
            workflow_path = self.resolve_workflow_path(workflow_name)
            if workflow_path is None:
                raise ValueError(f"Workflow not found: {workflow_name}")
            keys_to_remove = [k for k,v in self._ui_registry.items() if v.get('workflow_name') == workflow_name]
            for k in keys_to_remove:
                self._ui_registry.pop(k, None)
            self._load_workflow_tools(str(workflow_path))
        except Exception as e:
            logger.warning("Could not reload UI tools for %s: %s", workflow_name, e)
        
        # Reload the workflow completely
        try:
            workflow_info = self._load_single_workflow(workflow_name)
            return workflow_info.to_dict()
        except Exception as e:
            logger.error("WORKFLOW_RELOAD_FAILED workflow=%s: %s", workflow_name, e, exc_info=True)
            return {"error": str(e)}
    
    def unload_workflow(self, workflow_name: str) -> None:
        """Unload a workflow (remove from active workflows)"""
        normalized_name = workflow_name.lower()
        
        if normalized_name in self._workflows:
            del self._workflows[normalized_name]
        
        if normalized_name in self._config_cache:
            del self._config_cache[normalized_name]
        
        logger.info("Unloaded workflow: %s", workflow_name)
    
    def get_workflow_info(self, workflow_name: str) -> dict[str, Any] | None:
        """Get complete information about a loaded workflow"""
        normalized_name = workflow_name.lower()
        workflow_info = self._workflows.get(normalized_name)
        return workflow_info.to_dict() if workflow_info else None
    
    def list_loaded_workflows(self) -> list[str]:
        """List all currently loaded workflows"""
        return [info.name for info in self._workflows.values()]
    
    def get_ui_tools(self, workflow_name: str) -> dict[str, Any]:
        return {k: v for k, v in getattr(self, '_ui_registry', {}).items() if v.get('workflow_name') == workflow_name}

    # Convenience method (list form) for external callers
    def get_workflow_tools(self, workflow_name: str):
        return list(self.get_ui_tools(workflow_name).values())

    # ========================================================================
    # VALIDATION API
    # ========================================================================
    
    def validate_workflow(self, workflow_name: str) -> dict[str, Any]:
        """Validate a workflow's structure and configuration"""
        workflow_path = self.resolve_workflow_path(workflow_name) or (self.workflows_base_path / workflow_name)
        validation_result = {
            'valid': True,
            'errors': [],
            'warnings': [],
            'info': {
                'name': workflow_name,
                'path': str(workflow_path)
            }
        }
        
        if not workflow_path.exists():
            validation_result['valid'] = False
            validation_result['errors'].append(f"Workflow directory not found: {workflow_path}")
            return validation_result
        
        # Check for modern modular structure
        orchestrator_yaml = workflow_path / "orchestrator.yaml"
        
        if orchestrator_yaml.exists():
            validation_result['info']['has_orchestrator_yaml'] = True
            try:
                with open(orchestrator_yaml, encoding='utf-8') as f:
                    orchestrator_data = yaml.safe_load(f) or {}
                parsed_orchestrator = parse_orchestrator_config(orchestrator_data)
                self._validate_orchestrator_contract(workflow_name, parsed_orchestrator)
                validation_result['info']['orchestrator_yaml_valid'] = True
            except Exception as e:
                validation_result['errors'].append(f"Invalid orchestrator.yaml: {e}")
                validation_result['valid'] = False
        else:
            validation_result['warnings'].append("No orchestrator.yaml found")
        
        # Validate other declarative config files against strict contracts.
        section_parsers = {
            "agents": parse_agents_config,
            "transition_graph": parse_transition_graph_config,
            "context_variables": parse_context_variables_config,
            "structured_outputs": parse_structured_outputs_config,
            "tools": parse_tools_config,
            "ui_config": parse_ui_config,
            "middleware": parse_middleware_config,
            "a2a": parse_a2a_config,
        }
        for section_name, parser in section_parsers.items():
            yaml_path = workflow_path / f"{section_name}.yaml"
            if not yaml_path.exists():
                continue
            validation_result['info'][f'has_{section_name}'] = True
            try:
                with open(yaml_path, encoding="utf-8-sig") as f:
                    payload = yaml.safe_load(f)
                if payload is None:
                    payload = {}
                parser(payload)
                validation_result['info'][f'{section_name}_valid'] = True
            except Exception as e:
                validation_result['errors'].append(f"Invalid {section_name}.yaml: {e}")
                validation_result['valid'] = False

        try:
            modular_config = self._load_modular_workflow_config(workflow_path)
            validate_workflow_context_contract(
                workflow_name=workflow_name,
                workflow_config=modular_config,
                workflow_path=workflow_path,
            )
            validation_result['info']['context_contract_valid'] = True
        except Exception as e:
            validation_result['errors'].append(f"Invalid workflow context contract: {e}")
            validation_result['valid'] = False
        
        # Check for tools directory
        tools_dir = workflow_path / "tools"
        if tools_dir.exists():
            validation_result['info']['has_tools_dir'] = True
            tool_files = list(tools_dir.glob("*.py"))
            validation_result['info']['tool_count'] = len(tool_files)
        
        # Check for __init__.py
        init_file = workflow_path / "__init__.py"
        validation_result['info']['has_init'] = init_file.exists()
        
        # Check if workflow is currently loaded
        normalized_name = workflow_name.lower()
        if normalized_name in self._workflows:
            workflow_info = self._workflows[normalized_name]
            validation_result['info']['loaded'] = True
            validation_result['info']['status'] = workflow_info.status
            validation_result['info']['tools_loaded'] = workflow_info.tools_loaded
        else:
            validation_result['info']['loaded'] = False
        
        return validation_result
    
    # ========================================================================
    # UTILITY METHODS
    # ========================================================================
    
    def get_status_summary(self) -> dict[str, Any]:
        """Get comprehensive status summary"""
        loaded_count = len([w for w in self._workflows.values() if w.status == "loaded"])
        error_count = len([w for w in self._workflows.values() if w.status == "error"])
        tools_loaded_count = len([w for w in self._workflows.values() if w.tools_loaded])
        
        return {
            "total_workflows": len(self._workflows),
            "loaded_workflows": loaded_count,
            "error_workflows": error_count,
            "tools_loaded_count": tools_loaded_count,
            "workflow_names": [w.name for w in self._workflows.values()],
            "base_path": str(self.workflows_base_path),
            "summary": f"{loaded_count} loaded, {error_count} errors, {tools_loaded_count} with tools"
        }
    
    def refresh_all(self) -> dict[str, Any]:
        """Refresh all workflows and return summary"""
        logger.info("Refreshing all workflows...")
        self._workflows.clear()
        self._workflow_paths.clear()
        self._config_cache.clear()
        self._load_all_workflows()
        return self.get_status_summary()

    # ========================================================================
    # INTERNAL CONFIG LOADING
    # ========================================================================
    def _load_config_if_exists(self, base_path: Path, config_name: str) -> dict[str, Any] | None:
        """Load YAML config file.
        
        Args:
            base_path: Directory containing the config file
            config_name: Base name without extension (e.g., 'orchestrator', 'agents')
        
        Returns:
            Parsed config dict (possibly empty), or None if file doesn't exist.
        """
        yaml_path = base_path / f"{config_name}.yaml"
        
        if not yaml_path.exists():
            return None
        
        try:
            with open(yaml_path, encoding='utf-8-sig') as f:
                data = yaml.safe_load(f)
            if data is None:
                return {}
            if not isinstance(data, dict):
                raise ValueError(f"{yaml_path} must contain a mapping at root")
            return data
        except Exception as e:
            raise ValueError(f"Failed reading YAML {yaml_path}: {e}") from e

    def _resolve_ai_config_path(self) -> Path:
        raw = (os.getenv("MOZAIKS_AI_CONFIG_PATH") or "").strip()
        if raw:
            return Path(raw).resolve()
        platform_scoped = self.workflows_base_path.resolve().parent / "config" / "ai.json"
        if platform_scoped.exists():
            return platform_scoped

        return (resolve_active_app_root() / "config" / "ai.json").resolve()

    def _load_ai_config(self) -> dict[str, Any]:
        path = self._resolve_ai_config_path()
        sibling_app_root = (self.workflows_base_path.resolve().parent / "app").resolve()
        sibling_app_ai_path = (sibling_app_root / "config" / "ai.json").resolve()
        app_root = sibling_app_root if (sibling_app_root / "app.json").exists() else path.parent.parent

        if not path.exists():
            if sibling_app_ai_path.exists():
                path = sibling_app_ai_path
                app_root = sibling_app_root
            else:
                logger.info("AI config not found, skipping workflow AI metadata: %s", path)
                return resolve_runtime_ai_config({}, app_root=app_root)

        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                logger.warning("AI config must be a JSON object: %s", path)
                return resolve_runtime_ai_config({}, app_root=app_root)
            return resolve_runtime_ai_config(data, app_root=app_root)
        except Exception as e:
            logger.error("AI_CONFIG_READ_FAILED %s: %s", path, e, exc_info=True)
            return resolve_runtime_ai_config({}, app_root=app_root)

    def _load_modular_workflow_config(self, workflow_path: Path) -> dict[str, Any]:
        """Load modular workflow configuration from YAML files.

        Canonical files: orchestrator.yaml, agents.yaml, transition_graph.yaml,
        context_variables.yaml, structured_outputs.yaml, tools.yaml,
        ui_config.yaml, middleware.yaml.
        Optional file: a2a.yaml.
        Tools file is expected to expose a unified 'tools' list.

        Top-level merge rules:
          - orchestrator keys merged at root
          - explicit sections under their key (agents, transition_graph, middleware, context_variables, structured_outputs)
          - tools contributes its root keys ('tools' and 'lifecycle_tools') without transformation
          - ui_config contributes its root keys
        """
        config: dict[str, Any] = {}
        if not workflow_path.exists():
            return config

        # Orchestrator (top-level keys)
        orchestrator_raw = self._load_config_if_exists(workflow_path, "orchestrator")
        if orchestrator_raw is None:
            raise ValueError(f"Workflow '{workflow_path.name}' is missing required file: orchestrator.yaml")
        orchestrator = parse_orchestrator_config(orchestrator_raw)
        config.update(orchestrator)

        # Sectioned files
        section_parsers = {
            "agents": parse_agents_config,
            "transition_graph": parse_transition_graph_config,
            "context_variables": parse_context_variables_config,
            "structured_outputs": parse_structured_outputs_config,
            "middleware": parse_middleware_config,
            "a2a": parse_a2a_config,
        }
        for section_name, parser in section_parsers.items():
            raw_data = self._load_config_if_exists(workflow_path, section_name)
            if raw_data is None:
                continue
            config[section_name] = parser(raw_data)
        
        # Tools file (canonical unified list under 'tools')
        tools_raw = self._load_config_if_exists(workflow_path, "tools")
        if tools_raw is not None:
            tools_data = parse_tools_config(tools_raw)
            config["tools"] = tools_data.get("tools", [])
            config["lifecycle_tools"] = tools_data.get("lifecycle_tools", [])
        
        # UI config
        ui_raw = self._load_config_if_exists(workflow_path, "ui_config")
        if ui_raw is not None:
            ui_data = parse_ui_config(ui_raw)
            # Merge UI config keys at root (e.g., visual_agents)
            config.update(ui_data)

        configured_entry_point = (
            ((self._ai_config or {}).get("workflows") or {}).get("entry_point")
        )
        if isinstance(configured_entry_point, str) and configured_entry_point.strip():
            config["entry_point"] = workflow_path.name.lower() == configured_entry_point.strip().lower()
        else:
            config.pop("entry_point", None)
        
        return config

    def _validate_orchestrator_contract(self, workflow_name: str, config: dict[str, Any]) -> None:
        """Enforce required orchestrator contract fields for runtime safety."""
        declared_name = str(config.get("workflow_name") or "").strip()
        if not declared_name:
            raise ValueError(f"Workflow '{workflow_name}' is missing required field: workflow_name")

        if declared_name.lower() != workflow_name.lower():
            raise ValueError(
                f"Workflow '{workflow_name}' has mismatched workflow_name '{declared_name}' in orchestrator.yaml"
            )

        startup_mode = str(config.get("workflow_startup_mode") or "").strip()
        if not startup_mode:
            raise ValueError(
                f"Workflow '{workflow_name}' is missing required field: workflow_startup_mode"
            )

        if startup_mode not in self._ALLOWED_STARTUP_MODES:
            allowed = ", ".join(sorted(self._ALLOWED_STARTUP_MODES))
            raise ValueError(
                f"Workflow '{workflow_name}' has invalid workflow_startup_mode '{startup_mode}'. "
                f"Allowed values: {allowed}"
            )

    # ========================================================================
    # RUNTIME HANDLER REGISTRY
    # ========================================================================
    def register_workflow_handler(self, workflow_name: str, *, human_loop: bool = False, transport: str = "websocket"):
        """Decorator to register a custom async handler for a workflow.

        Example:
            @workflow_manager.register_workflow_handler("my_flow")
            async def handler(...): ...
        """
        def decorator(func: Callable[..., Awaitable[Any]]):
            self._handlers[workflow_name.lower()] = func
            self._handler_metadata[workflow_name.lower()] = {
                "human_loop": human_loop,
                "transport": transport,
            }
            logger.debug("Registered workflow handler '%s' (transport=%s, human_loop=%s)", workflow_name, transport, human_loop)
            return func
        return decorator

    def get_workflow_handler(self, workflow_name: str) -> Callable[..., Awaitable[Any]] | None:
        """Return a handler; if absent create a dynamic orchestration delegate."""
        key = workflow_name.lower()
        if key in self._handlers:
            return self._handlers[key]
        # Lazy dynamic handler creation via OrchestrationPort (engine-agnostic)
        async def dynamic_handler(app_id: str, chat_id: str, user_id: str | None = None, initial_message: str | None = None, **kwargs):
            from mozaiksai.core.adapters.ag2_orchestration import get_ag2_adapter
            from mozaiksai.core.ports.orchestration import RunRequest
            adapter = get_ag2_adapter()
            return await adapter.run(RunRequest(
                workflow_name=workflow_name,
                app_id=app_id,
                chat_id=chat_id,
                user_id=user_id or "",
                initial_message=initial_message,
                extra=kwargs,
            ))
        # Cache it
        self._handlers[key] = dynamic_handler
        self._handler_metadata.setdefault(key, {"human_loop": self.has_human_in_the_loop(workflow_name), "transport": "websocket"})
        logger.debug("Created dynamic handler for workflow '%s'", workflow_name)
        return dynamic_handler

    def get_workflow_transport(self, workflow_name: str) -> str:
        meta = self._handler_metadata.get(workflow_name.lower())
        if meta:
            return meta.get("transport", "websocket")
        # Fallback: future: derive from config
        return "websocket"

    def workflow_status_summary(self) -> dict[str, Any]:
        handlers = list(self._handlers.keys())
        return {
            "registered_workflows": handlers,
            "total_registered": len(handlers),
            "metadata": self._handler_metadata.copy(),
            **self.get_status_summary(),
        }

# ========================================================================
# GLOBAL INSTANCE & API
# ========================================================================

# Single global instance: root is driven by MOZAIKS_WORKFLOWS_PATH,
# host defaults, or the active app root.
_unified_workflow_manager = UnifiedWorkflowManager(workflows_base_path=resolve_workflows_root())

def get_workflow_manager() -> UnifiedWorkflowManager:
    """Get the global workflow manager instance"""
    return _unified_workflow_manager

def initialize_workflows(base_path: str | None = None) -> dict[str, dict[str, Any]]:
    """Initialize workflows with custom base path"""
    global _unified_workflow_manager, workflow_manager

    # Preserve object identity so modules that imported `workflow_manager`
    # by value don't retain stale references after reinitialization.
    previous_manager = _unified_workflow_manager

    # Reset singleton so all runtime caches are correctly rebuilt from base_path.
    UnifiedWorkflowManager._instance = None
    rebuilt_manager = UnifiedWorkflowManager(workflows_base_path=base_path)

    if previous_manager is not None and previous_manager is not rebuilt_manager:
        previous_manager.__dict__.clear()
        previous_manager.__dict__.update(rebuilt_manager.__dict__)
        _unified_workflow_manager = previous_manager
        UnifiedWorkflowManager._instance = previous_manager
    else:
        _unified_workflow_manager = rebuilt_manager

    workflow_manager = _unified_workflow_manager

    return {
        name: info.to_dict() 
        for name, info in _unified_workflow_manager._workflows.items()
    }

# Export the main API
workflow_manager = _unified_workflow_manager

# Functional API for workflow operations
def register_workflow(workflow_name: str, *, human_loop: bool = False, transport: str = "websocket"):
    return workflow_manager.register_workflow_handler(workflow_name, human_loop=human_loop, transport=transport)

def get_workflow_handler(workflow_name: str):
    return workflow_manager.get_workflow_handler(workflow_name)

def get_workflow_transport(workflow_name: str) -> str:
    return workflow_manager.get_workflow_transport(workflow_name)

def workflow_status_summary() -> dict[str, Any]:
    return workflow_manager.workflow_status_summary()

def get_workflow_tools(workflow_name: str):
    return list(workflow_manager.get_ui_tools(workflow_name).values())

__all__ = [
    "UnifiedWorkflowManager",
    "WorkflowInfo",
    "get_workflow_manager",
    "initialize_workflows",
    "workflow_manager",
    # functional facade
    "register_workflow",
    "get_workflow_handler",
    "get_workflow_transport",
    "workflow_status_summary",
    "get_workflow_tools",
]
