# ==============================================================================
# FILE: core/workflow/unified_workflow_manager.py
# DESCRIPTION: Unified workflow management - combines config loading and lifecycle management
#              Optimized single-responsibility design with clean separation of concerns
# ==============================================================================

import json
import os
import yaml
import importlib
from typing import Dict, Any, List, Optional, Tuple, Callable, Awaitable, Set
from pathlib import Path
from dataclasses import dataclass

from logs.logging_config import get_workflow_logger

logger = get_workflow_logger(workflow_name="unified_workflow_manager")

@dataclass
class WorkflowInfo:
    """Container for complete workflow information"""
    name: str
    config: Dict[str, Any]
    path: str
    status: str = "loaded"
    module: Optional[Any] = None
    tools_loaded: bool = False
    error: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
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

    Lifecycle "tools" removed; backend (agent) tools are bound directly during
    agent creation; hooks are managed via hooks_loader.
    """

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self, workflows_base_path: str = "workflows"):
        if hasattr(self, "_initialized"):
            return
        # Core caches / registries
        # Allow MOZAIKS_WORKFLOWS_PATH env var to override the default path
        env_path = os.environ.get("MOZAIKS_WORKFLOWS_PATH")
        self.workflows_base_path = Path(env_path) if env_path else Path(workflows_base_path)
        self._workflows: Dict[str, WorkflowInfo] = {}
        self._config_cache: Dict[str, Dict[str, Any]] = {}
        self._ui_registry: Dict[str, Dict[str, Any]] = {}
        self._ui_tool_path_cache: Dict[str, str] = {}
        self._ui_loaded_workflows: set[str] = set()
        self._hooks_loaded_workflows: set[str] = set()
        # Runtime handler and metadata registries
        self._handlers: Dict[str, Callable[..., Awaitable[Any]]] = {}
        self._handler_metadata: Dict[str, Dict[str, Any]] = {}
        self._initialized = False
        # Initial load
        self._load_all_workflows()
        self._initialized = True
        logger.info(
            f"Initialized unified workflow manager with {len(self._workflows)} workflows"
        )

    # ------------------------- UI TOOLS -------------------------
    def _load_workflow_tools(self, workflow_path: str) -> None:
        from pathlib import Path as _P
        tools_yaml_path = _P(workflow_path) / "tools.yaml"
        
        if not tools_yaml_path.exists():
            return

        workflow_name = _P(workflow_path).name
        if workflow_name in self._ui_loaded_workflows:
            return

        try:
            with open(tools_yaml_path, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f) or {}
            
            entries = data.get('tools', [])
            if not isinstance(entries, list):
                logger.warning(f"Invalid tools list in workflow {workflow_name}")
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
                    logger.warning(f"Skipping tool entry without required 'file' and 'function' fields in workflow '{workflow_name}'")
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
                agent = entry.get('agent') or entry.get('caller') or entry.get('executor')

                # UI detection
                ui_block = entry.get('ui') if isinstance(entry.get('ui'), dict) else None
                tool_type = entry.get('tool_type') or entry.get('type')
                has_meaningful_ui = bool(ui_block and (ui_block.get('component') or ui_block.get('mode')))
                # If ui block provided, assume UI_Tool when tool_type missing
                if not tool_type and has_meaningful_ui:
                    tool_type = 'UI_Tool'

                # inside _load_workflow_tools(), in the for-entry loop:
                if has_meaningful_ui or tool_type == 'UI_Tool':
                    ui_ct += 1
                    component = None
                    mode = 'inline'
                    if ui_block:
                        component = ui_block.get('component')
                        mode = ui_block.get('mode', 'inline')
                    else:
                        component = entry.get('component')
                        mode = entry.get('mode', 'inline')

                    # Fallback: if "function" not present, try to pull it from module_path "pkg.mod:func"
                    fn_name = function
                    if not fn_name and isinstance(module_path, str) and ':' in module_path:
                        fn_name = module_path.split(':', 1)[1].strip() or None

                    # Use component name as tool_id if available (tools call use_ui_tool with component name)
                    # Otherwise fall back to function name
                    lookup_id = component if component else tool_id

                    rec = {
                        'workflow_name': workflow_name,
                        'tool_id': lookup_id,        # component name for lookup (ActionPlan, AgentAPIKeyInput, etc.)
                        'fn': fn_name,               # raw function name for runtime event matching
                        'agent': agent,
                        'path': module_path,
                        'component': component,
                        'mode': mode,
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
                    tool_type = entry.get('tool_type') or entry.get('type')
                    has_meaningful_ui = bool(ui_block and (ui_block.get('component') or ui_block.get('mode')))
                    if not tool_type and has_meaningful_ui:
                        tool_type = 'UI_Tool'
                    
                    if has_meaningful_ui or tool_type == 'UI_Tool':
                        ui_ct += 1
                        component = None
                        mode = 'inline'
                        if ui_block:
                            component = ui_block.get('component')
                            mode = ui_block.get('mode', 'inline')
                        
                        fn_name = function
                        
                        # Use component name as tool_id if available (tools call use_ui_tool with component name)
                        # Otherwise fall back to function name
                        lookup_id = component if component else tool_id
                        
                        rec = {
                            'workflow_name': workflow_name,
                            'tool_id': lookup_id,        # component name for lookup
                            'fn': fn_name,
                            'agent': agent,
                            'path': module_path,
                            'component': component,
                            'mode': mode,
                            'classification': 'ui',
                            'tool_type': tool_type,
                            'trigger': entry.get('trigger'),  # preserve lifecycle metadata
                        }
                        
                        key = f"{workflow_name}.{lookup_id}"
                        self._ui_registry[key] = rec
                        if module_path:
                            self._ui_tool_path_cache[module_path] = key

            self._ui_loaded_workflows.add(workflow_name)
            logger.info(f"Tools loaded for {workflow_name}: ui={ui_ct}")
        except Exception as e:  # pragma: no cover
            logger.error(f"Failed parsing tools.json for {workflow_name}: {e}")


    def get_ui_tool_record(self, tool_path_or_id: str) -> Optional[Dict[str, Any]]:
        """Lookup UI tool record by module path or tool id.

        Accepts either the full python path (used in tools.json) or the tool_id.
        """
        registry_key = self._ui_tool_path_cache.get(tool_path_or_id)
        if registry_key and registry_key in self._ui_registry:
            return self._ui_registry[registry_key]
        # Fallback treat argument as tool_id
        for rec in self._ui_registry.values():
            if rec.get('tool_id') == tool_path_or_id:
                return rec
        return None

    def iter_ui_tools(self) -> List[Dict[str, Any]]:
        return list(self._ui_registry.values())

    def detect_ui_tool_event(self, event: Any) -> Tuple[bool, Dict[str, Any]]:
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
    
    def discover_workflows(self) -> List[str]:
        """Discover all available workflows in the workflows directory."""
        if not self.workflows_base_path.exists():
            logger.warning(f"Workflows directory not found: {self.workflows_base_path}")
            return []
        
        workflows = []
        for item in self.workflows_base_path.iterdir():
            if item.is_dir() and not item.name.startswith('.'):
                # Check for modular YAML config files
                if (item / "orchestrator.yaml").exists():
                    workflows.append(item.name)
                    logger.debug(f"Discovered workflow (orchestrator.yaml): {item.name}")
                # Also treat workflows that declare configuration via tools.yaml as first-class
                elif (item / "tools.yaml").exists() or (item / "tools").exists():
                    workflows.append(item.name)
                    logger.debug(f"Discovered workflow (tools.yaml): {item.name}")
                    logger.debug(f"Discovered workflow (tools.json): {item.name}")
        
        return workflows
    
    def _load_all_workflows(self) -> None:
        """Load all workflow configs and initialize them"""
        try:
            workflow_names = self.discover_workflows()
            
            if not workflow_names:
                logger.warning("⚠️ No workflows found in the workflows directory")
                return
            
            for workflow_name in workflow_names:
                try:
                    self._load_single_workflow(workflow_name)
                except Exception as e:
                    logger.error(f"❌ Failed to load workflow {workflow_name}: {e}")
                    # Store error info for debugging
                    self._workflows[workflow_name.lower()] = WorkflowInfo(
                        name=workflow_name,
                        config={},
                        path=str(self.workflows_base_path / workflow_name),
                        status="error",
                        error=str(e)
                    )
                    
        except Exception as e:
            logger.error(f"❌ Critical error loading workflows: {e}")
    
    def _load_single_workflow(self, workflow_name: str) -> WorkflowInfo:
        """Load a single workflow with all its components"""
        workflow_path = self.workflows_base_path / workflow_name
        
        if not workflow_path.exists():
            raise ValueError(f"Workflow not found: {workflow_name}")
        # Load configuration directly (modular JSON files) – canonical, no legacy keys
        config = self._load_modular_workflow_config(workflow_path)
        if not config:
            logger.warning(f"⚠️ Empty config for workflow: {workflow_name}")
            config = {}

        workflow_info = WorkflowInfo(name=workflow_name, config=config, path=str(workflow_path))

        # UI tools
        try:
            self._load_workflow_tools(str(workflow_path))
            has_ui_tools = any(r.get('workflow_name') == workflow_name for r in getattr(self, '_ui_registry', {}).values())
            workflow_info.tools_loaded = has_ui_tools
        except Exception as e:  # pragma: no cover
            logger.warning(f"Could not load UI tools for {workflow_name}: {e}")

        # Optional module
        try:
            init_file = workflow_path / "__init__.py"
            if init_file.exists():
                module_path = f"workflows.{workflow_name}"
                workflow_info.module = importlib.import_module(module_path)
        except ImportError as e:  # pragma: no cover
            logger.debug(f"No module found for workflow {workflow_name}: {e}")

        normalized_name = workflow_name.lower()
        self._workflows[normalized_name] = workflow_info
        self._config_cache[normalized_name] = config
        logger.info(f"Successfully loaded workflow: {workflow_name}")
        return workflow_info
    
    # ========================================================================
    # CONFIGURATION ACCESS API
    # ========================================================================
    
    def get_config(self, workflow_name: str) -> Dict[str, Any]:
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
    
    def get_inline_agents(self, workflow_name: str) -> List[str]:
        """Get list of agents that should appear in chat pane"""
        config = self.get_config(workflow_name)
        return config.get("chat_pane_agents", [])
    
    def get_artifact_agents(self, workflow_name: str) -> List[str]:
        """Get list of agents that produce artifacts"""
        config = self.get_config(workflow_name)
        return config.get("artifact_agents", [])
    
    def get_initial_message(self, workflow_name: str) -> Optional[str]:
        """Get initial message for workflow"""
        config = self.get_config(workflow_name)
        return config.get("initial_message")
    
    def get_visual_agents(self, workflow_name: str) -> List[str]:
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

    def get_auto_tool_agents(self, workflow_name: str) -> Set[str]:
        """Return set of agent names with auto_tool_mode enabled.
        
        Auto-tool agents emit structured outputs that are automatically converted to tool calls.
        Their text messages (containing agent_message) should be suppressed to avoid duplication
        since the agent_message also appears in the tool_call payload.
        
        Returns:
            Set of agent names with auto_tool_mode=true (e.g., {"ContextAgent", "APIKeyAgent"})
        """
        config = self.get_config(workflow_name)
        agents_data = config.get('agents', {})
        
        agents_dict: Dict[str, Any] = {}

        # Handle double-nesting: agents.agents.{agent_name}
        if isinstance(agents_data, dict) and 'agents' in agents_data:
            nested = agents_data.get('agents')
            if isinstance(nested, dict):
                agents_dict = nested
            elif isinstance(nested, list):
                for item in nested:
                    if isinstance(item, dict) and isinstance(item.get('name'), str):
                        agents_dict[item['name']] = item
        elif isinstance(agents_data, dict):
            agents_dict = agents_data
        elif isinstance(agents_data, list):
            for item in agents_data:
                if isinstance(item, dict) and isinstance(item.get('name'), str):
                    agents_dict[item['name']] = item
        
        auto_tool_agents: Set[str] = set()
        
        # Iterate over agent_name -> agent_config mapping
        for agent_name, agent_config in agents_dict.items():
            if not isinstance(agent_config, dict):
                continue
            auto_tool_mode = agent_config.get('auto_tool_mode', False)
            if auto_tool_mode is True:
                auto_tool_agents.add(agent_name)
        
        return auto_tool_agents

    def get_ui_hidden_triggers(self, workflow_name: str) -> Dict[str, Set[str]]:
        """Return mapping of agent_name -> set of ui_hidden trigger values.

        Extracts state variables with ui_hidden: true from context_variables.json
        and returns a dict mapping each source agent to the set of trigger values
        that should be hidden from the UI.

        Returns:
            Dict mapping agent_name to Set of trigger values (e.g., {"InterviewAgent": {"NEXT"}})
        """
        config = self.get_config(workflow_name)
        context_vars = config.get('context_variables', {})
        # Handle double-nesting: context_variables.context_variables.definitions
        if 'context_variables' in context_vars:
            context_vars = context_vars['context_variables']
        definitions = context_vars.get('definitions', {})

        hidden_triggers: Dict[str, Set[str]] = {}

        # Most workflows use list-based definitions: [{name, type, source, ...}, ...]
        if isinstance(definitions, list):
            iterable = [(d.get('name'), d) for d in definitions if isinstance(d, dict)]
        elif isinstance(definitions, dict):
            iterable = list(definitions.items())
        else:
            iterable = []

        for var_name, var_config in iterable:
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

    def get_structured_output_registry(self, workflow_name: str) -> Dict[str, Optional[str]]:
        """Return a normalized mapping: agent_name -> model_name or None.

        Supports both the new dict-based registry ({"Agent": "Model"|None})
        and the legacy list-based registry ([{"agent": "...","agent_definition": "...|None"}]).
        """
        config = self.get_config(workflow_name)
        so = config.get("structured_outputs") or {}
        reg = so.get("registry")

        normalized: Dict[str, Optional[str]] = {}

        # New schema: dict mapping
        if isinstance(reg, dict):
            for agent, model in reg.items():
                if isinstance(agent, str):
                    normalized[agent] = model if isinstance(model, str) else None
            return normalized

        # Legacy schema: list of {agent, agent_definition}
        if isinstance(reg, list):
            for item in reg:
                if isinstance(item, dict):
                    agent = item.get("agent")
                    model = item.get("agent_definition")
                    if isinstance(agent, str):
                        normalized[agent] = model if isinstance(model, str) else None
            return normalized

        # Defensive: handle accidental nesting like {"structured_outputs": {...}}
        if isinstance(reg, dict) and "structured_outputs" in reg:
            nested = reg.get("structured_outputs")
            if isinstance(nested, dict):
                for agent, model in nested.items():
                    if isinstance(agent, str):
                        normalized[agent] = model if isinstance(model, str) else None

        return normalized

    def get_agent_structured_outputs_config(self, workflow_name: str) -> Dict[str, bool]:
        """Return agent -> bool indicating whether a structured model is assigned."""
        reg = self.get_structured_output_registry(workflow_name)
        return {agent: (model is not None) for agent, model in reg.items()}
    
    def get_all_workflow_names(self) -> List[str]:
        """Get list of all loaded workflow names"""
        return [info.name for info in self._workflows.values() if info.status == "loaded"]

    def list_workflows(self) -> List[str]:
        """Alias for get_all_workflow_names() — returns all loaded workflow names."""
        return self.get_all_workflow_names()

    # ========================================================================
    # LIFECYCLE MANAGEMENT API
    # ========================================================================
    
    def reload_workflow(self, workflow_name: str) -> Dict[str, Any]:
        """Hot-reload a workflow and its tools"""
        normalized_name = workflow_name.lower()
        
        # Reload module if it exists
        if normalized_name in self._workflows:
            workflow_info = self._workflows[normalized_name]
            if workflow_info.module:
                try:
                    importlib.reload(workflow_info.module)
                    logger.info(f"Reloaded workflow module: {workflow_name}")
                except Exception as e:
                    logger.error(f"Failed to reload workflow module {workflow_name}: {e}")
        
        # Reload embedded UI tool metadata
        try:
            workflow_path = self.workflows_base_path / workflow_name
            keys_to_remove = [k for k,v in self._ui_registry.items() if v.get('workflow_name') == workflow_name]
            for k in keys_to_remove:
                self._ui_registry.pop(k, None)
            self._load_workflow_tools(str(workflow_path))
        except Exception as e:
            logger.warning(f"Could not reload UI tools for {workflow_name}: {e}")
        
        # Reload the workflow completely
        try:
            workflow_info = self._load_single_workflow(workflow_name)
            return workflow_info.to_dict()
        except Exception as e:
            logger.error(f"Failed to reload workflow {workflow_name}: {e}")
            return {"error": str(e)}
    
    def unload_workflow(self, workflow_name: str) -> None:
        """Unload a workflow (remove from active workflows)"""
        normalized_name = workflow_name.lower()
        
        if normalized_name in self._workflows:
            del self._workflows[normalized_name]
        
        if normalized_name in self._config_cache:
            del self._config_cache[normalized_name]
        
        logger.info(f"Unloaded workflow: {workflow_name}")
    
    def get_workflow_info(self, workflow_name: str) -> Optional[Dict[str, Any]]:
        """Get complete information about a loaded workflow"""
        normalized_name = workflow_name.lower()
        workflow_info = self._workflows.get(normalized_name)
        return workflow_info.to_dict() if workflow_info else None
    
    def list_loaded_workflows(self) -> List[str]:
        """List all currently loaded workflows"""
        return [info.name for info in self._workflows.values()]
    
    def get_ui_tools(self, workflow_name: str) -> Dict[str, Any]:
        return {k: v for k, v in getattr(self, '_ui_registry', {}).items() if v.get('workflow_name') == workflow_name}

    # Convenience method (list form) for external callers
    def get_workflow_tools(self, workflow_name: str):
        return list(self.get_ui_tools(workflow_name).values())

    # ========================================================================
    # VALIDATION API
    # ========================================================================
    
    def validate_workflow(self, workflow_name: str) -> Dict[str, Any]:
        """Validate a workflow's structure and configuration"""
        workflow_path = self.workflows_base_path / workflow_name
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
                with open(orchestrator_yaml, 'r', encoding='utf-8') as f:
                    yaml.safe_load(f)
                validation_result['info']['orchestrator_yaml_valid'] = True
            except Exception as e:
                validation_result['errors'].append(f"Invalid orchestrator.yaml: {e}")
                validation_result['valid'] = False
        else:
            validation_result['warnings'].append("No orchestrator.yaml found")
        
        # Check for other config files (YAML only)
        config_names = ['agents', 'handoffs', 'tools', 'ui_config', 'hooks']
        for config_name in config_names:
            yaml_path = workflow_path / f"{config_name}.yaml"
            if yaml_path.exists():
                validation_result['info'][f'has_{config_name}'] = True
        
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
    
    def get_status_summary(self) -> Dict[str, Any]:
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
    
    def refresh_all(self) -> Dict[str, Any]:
        """Refresh all workflows and return summary"""
        logger.info("Refreshing all workflows...")
        self._workflows.clear()
        self._config_cache.clear()
        self._hooks_loaded_workflows.clear()
        self._load_all_workflows()
        return self.get_status_summary()

    # ========================================================================
    # INTERNAL CONFIG LOADING (replaces legacy file_manager)
    # ========================================================================
    def _load_config_if_exists(self, base_path: Path, config_name: str) -> Dict[str, Any]:
        """Load YAML config file.
        
        Args:
            base_path: Directory containing the config file
            config_name: Base name without extension (e.g., 'orchestrator', 'agents')
        
        Returns:
            Parsed config dict, or empty dict if file doesn't exist
        """
        yaml_path = base_path / f"{config_name}.yaml"
        
        if not yaml_path.exists():
            return {}
        
        try:
            with open(yaml_path, 'r', encoding='utf-8-sig') as f:
                data = yaml.safe_load(f)
                return data if isinstance(data, dict) else {}
        except Exception as e:
            logger.error(f"Failed reading YAML {yaml_path}: {e}")
            return {}

    def _load_modular_workflow_config(self, workflow_path: Path) -> Dict[str, Any]:
        """Load modular workflow configuration from YAML files.

        Canonical files: orchestrator.yaml, agents.yaml, handoffs.yaml, context_variables.yaml,
        structured_outputs.yaml, tools.yaml, ui_config.yaml, hooks.yaml.
        Tools file expected to expose unified 'tools' list (no legacy agent_tools/ui_tools splitting).
        
        Top-level merge rules:
          - orchestrator keys merged at root
          - explicit sections under their key (agents, handoffs, context_variables, structured_outputs)
          - tools contributes its root keys (currently only 'tools') without transformation
          - ui_config contributes its root keys
        """
        config: Dict[str, Any] = {}
        if not workflow_path.exists():
            return config
        
        # Orchestrator (top-level keys)
        orchestrator = self._load_config_if_exists(workflow_path, 'orchestrator')
        config.update(orchestrator)
        
        # Sectioned files
        for section_name in ['agents', 'handoffs', 'context_variables', 'structured_outputs', 'hooks']:
            data = self._load_config_if_exists(workflow_path, section_name)
            if data:
                config[section_name] = data
        
        # Tools file (canonical unified list under 'tools')
        tools_data = self._load_config_if_exists(workflow_path, 'tools')
        if tools_data:
            # Validate: ensure only 'tools' key we care about; ignore legacy keys if appear
            tools_list = tools_data.get('tools')
            lifecycle_tools = tools_data.get('lifecycle_tools')
            if isinstance(tools_list, list):
                config['tools'] = tools_list
            if isinstance(lifecycle_tools, list):
                config['lifecycle_tools'] = lifecycle_tools
        
        # UI config
        ui_data = self._load_config_if_exists(workflow_path, 'ui_config')
        if ui_data:
            # Merge UI config keys at root (e.g., visual_agents)
            config.update(ui_data)
        
        return config

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
            logger.debug(f"Registered workflow handler '{workflow_name}' (transport={transport}, human_loop={human_loop})")
            return func
        return decorator

    def get_workflow_handler(self, workflow_name: str) -> Optional[Callable[..., Awaitable[Any]]]:
        """Return a handler; if absent create a dynamic orchestration delegate."""
        key = workflow_name.lower()
        if key in self._handlers:
            return self._handlers[key]
        # Lazy dynamic handler creation using orchestration engine
        async def dynamic_handler(app_id: str, chat_id: str, user_id: Optional[str] = None, initial_message: Optional[str] = None, **kwargs):
            from .orchestration_patterns import run_workflow_orchestration
            return await run_workflow_orchestration(
                workflow_name=workflow_name,
                app_id=app_id,
                chat_id=chat_id,
                user_id=user_id,
                initial_message=initial_message,
                **kwargs,
            )
        # Cache it
        self._handlers[key] = dynamic_handler
        self._handler_metadata.setdefault(key, {"human_loop": self.has_human_in_the_loop(workflow_name), "transport": "websocket"})
        logger.debug(f"Created dynamic handler for workflow '{workflow_name}'")
        return dynamic_handler

    def get_workflow_transport(self, workflow_name: str) -> str:
        meta = self._handler_metadata.get(workflow_name.lower())
        if meta:
            return meta.get("transport", "websocket")
        # Fallback: future: derive from config
        return "websocket"

    def workflow_status_summary(self) -> Dict[str, Any]:
        handlers = list(self._handlers.keys())
        return {
            "registered_workflows": handlers,
            "total_registered": len(handlers),
            "metadata": self._handler_metadata.copy(),
            **self.get_status_summary(),
        }

    # ========================================================================
    # HOOKS API
    # ========================================================================
    def register_hooks(self, workflow_name: str, agents: Dict[str, Any], force: bool = False) -> List[Any]:
        """Register hooks for a workflow using hooks.json.

        Parameters
        ----------
        workflow_name: str
            Name of the workflow directory under the base path.
        agents: dict[str, ConversableAgent]
            Mapping of agent names to instantiated ConversableAgent objects.
        force: bool
            If True, re-read and re-register even if hooks were already loaded.
        """
        logger.info(f"Registering hooks for workflow '{workflow_name}' (force={force})")
        # Pre-registration introspection (best-effort)
        pre_snapshot: Dict[str, Dict[str, Any]] = {}
        try:
            for aname, agent in agents.items():
                hook_map: Dict[str, Any] = {}
                # Common patterns: agent._hooks (dict[str,list]) or agent.hooks
                for attr in ("_hooks", "hooks"):
                    if hasattr(agent, attr):
                        raw = getattr(agent, attr)
                        if isinstance(raw, dict):
                            for htype, fns in raw.items():
                                try:
                                    hook_map[htype] = len(fns) if hasattr(fns, '__len__') else 'unknown'
                                except Exception:
                                    hook_map[htype] = 'err'
                        break
                pre_snapshot[aname] = hook_map
            logger.debug(f"[HOOKS] Pre-registration snapshot for '{workflow_name}': {pre_snapshot}")
        except Exception as _pre_err:
            logger.debug(f"[HOOKS] Pre-registration introspection failed: {_pre_err}")

        if (workflow_name in self._hooks_loaded_workflows) and not force:
            logger.debug(f"Hooks already registered for {workflow_name}; skipping")
            return []

        try:
            from .execution.hooks import register_hooks_for_workflow
            registered = register_hooks_for_workflow(workflow_name, agents, base_path=str(self.workflows_base_path))
            if registered:
                self._hooks_loaded_workflows.add(workflow_name)
                logger.info(f"Added '{workflow_name}' to hooks-loaded set; total loaded workflows={len(self._hooks_loaded_workflows)}")
            else:
                logger.debug(f"No hooks registered for '{workflow_name}' (empty or errors)")
            # Post-registration introspection to compute deltas
            try:
                post_snapshot: Dict[str, Dict[str, Any]] = {}
                for aname, agent in agents.items():
                    hook_map: Dict[str, Any] = {}
                    for attr in ("_hooks", "hooks"):
                        if hasattr(agent, attr):
                            raw = getattr(agent, attr)
                            if isinstance(raw, dict):
                                for htype, fns in raw.items():
                                    try:
                                        hook_map[htype] = len(fns) if hasattr(fns, '__len__') else 'unknown'
                                    except Exception:
                                        hook_map[htype] = 'err'
                            break
                    post_snapshot[aname] = hook_map
                # Compute naive delta counts
                deltas: Dict[str, Dict[str, Any]] = {}
                for aname, after in post_snapshot.items():
                    before = pre_snapshot.get(aname, {})
                    delta_map: Dict[str, Any] = {}
                    for htype, count_after in after.items():
                        count_before = before.get(htype, 0)
                        if isinstance(count_after, int) and isinstance(count_before, int):
                            delta = count_after - count_before
                        else:
                            delta = 'n/a'
                        if delta:
                            delta_map[htype] = delta
                    if delta_map:
                        deltas[aname] = delta_map
                logger.debug(f"[HOOKS] Post-registration snapshot for '{workflow_name}': {post_snapshot}")
                logger.debug(f"[HOOKS] Registration deltas for '{workflow_name}': {deltas}")
            except Exception as _post_err:
                logger.debug(f"[HOOKS] Post-registration introspection failed: {_post_err}")
            return registered
        except Exception as e:  # pragma: no cover
            logger.error(f"Failed to register hooks for {workflow_name}: {e}", exc_info=True)
            return []

# ========================================================================
# GLOBAL INSTANCE & API
# ========================================================================

# Single global instance
_unified_workflow_manager = UnifiedWorkflowManager()

def get_workflow_manager() -> UnifiedWorkflowManager:
    """Get the global workflow manager instance"""
    return _unified_workflow_manager

def initialize_workflows(base_path: str = "workflows") -> Dict[str, Dict[str, Any]]:
    """Initialize workflows with custom base path"""
    global _unified_workflow_manager
    # Create new instance with custom path
    new_manager = object.__new__(UnifiedWorkflowManager)
    new_manager.workflows_base_path = Path(base_path)
    new_manager._workflows = {}
    new_manager._config_cache = {}
    new_manager._initialized = False
    new_manager._load_all_workflows()
    new_manager._initialized = True
    
    _unified_workflow_manager = new_manager
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

def workflow_status_summary() -> Dict[str, Any]:
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

