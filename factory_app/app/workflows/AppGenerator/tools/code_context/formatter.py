"""
Agent-Specific Context Formatter

Formats extracted code context for specific agent consumption.
Each agent has defined requirements and gets tailored context.

Agent Requirements Map:
- ServiceAgent: config_config, database_config, model_context
- ControllerAgent: config_config, service_context

Ported from project-aid-v2 code_context_formatter.py
"""

import logging
import re
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)


class AgentContextFormatter:
    """
    Formats aggregated code context for specific agents.
    Uses an agent_requirements map to filter relevant context types.
    """

    # Agent requirements map: defines which context types each agent needs
    agent_requirements = {
        'ServiceAgent': ['config_config', 'database_config', 'model_context'],
        'ControllerAgent': ['config_config', 'service_context'],
    }

    def __init__(self, aggregated_context: Dict[str, List[Dict]]):
        """
        Args:
            aggregated_context: Map of context_type -> list of context dicts
                Example: {"model_context": [{...}, {...}], "service_context": [...]}
        """
        self.aggregated_context = aggregated_context

    def format_for_agent(self, agent_name: str) -> str:
        """
        Format context for a specific agent based on its requirements.
        
        Args:
            agent_name: Name of the requesting agent
        
        Returns:
            Formatted context string ready for agent prompt
        """
        required_contexts = self.agent_requirements.get(agent_name, [])
        if not required_contexts:
            logger.warning(f"No context requirements defined for agent: {agent_name}")
            return ""

        # Filter context to only include required types
        filtered_context = {
            ctx_type: self.aggregated_context.get(ctx_type, [])
            for ctx_type in required_contexts
            if ctx_type in self.aggregated_context
        }

        if not any(filtered_context.values()):
            logger.debug(f"No matching context found for {agent_name}")
            return ""

        # Route to agent-specific formatter
        formatter_map = {
            'ServiceAgent': self._format_for_ServiceAgent,
            'ControllerAgent': self._format_for_ControllerAgent,
        }

        formatter = formatter_map.get(agent_name)
        if formatter:
            return formatter(filtered_context)
        else:
            return self._format_default(filtered_context, agent_name)

    # =========================================================================
    # Shared Formatting Helpers
    # =========================================================================

    def format_import_statement(
        self,
        module: str,
        imports: List[str],
        file_type: str,
        agent_name: str
    ) -> str:
        """Generate import statements based on file type."""
        if not module or not imports:
            return ""
        
        if file_type == "python":
            import_list = ", ".join(imports)
            return f"from {module} import {import_list}"
        elif file_type in ("javascript", "typescript"):
            import_list = ", ".join(imports)
            return f"import {{ {import_list} }} from '{module}';"
        elif file_type == "css":
            return f"@import '{module}';"
        else:
            return f"# Import from {module}: {', '.join(imports)}"

    def _group_related_contexts(
        self,
        contexts: List[Dict],
        context_category: str
    ) -> Dict[str, Dict]:
        """
        Group contexts by module for organized formatting.
        
        Args:
            contexts: List of context dicts
            context_category: Category name (for logging)
        
        Returns:
            Dict of module_name -> merged context
        """
        grouped = {}
        for ctx in contexts:
            module = ctx.get("module", "unknown")
            if module not in grouped:
                grouped[module] = {"module": module}
            
            # Merge keys from context
            for key, value in ctx.items():
                if key == "module":
                    continue
                if key not in grouped[module]:
                    grouped[module][key] = value
                elif isinstance(value, list):
                    if not isinstance(grouped[module][key], list):
                        grouped[module][key] = []
                    grouped[module][key].extend(value)
                elif isinstance(value, dict):
                    if not isinstance(grouped[module][key], dict):
                        grouped[module][key] = {}
                    grouped[module][key].update(value)
        
        return grouped

    def _group_models_by_dependency(self, models: List[Dict]) -> List[Dict]:
        """
        Sort models based on their field dependencies.
        Models that depend on others come after their dependencies.
        """
        if not models:
            return []
        
        dependencies = {}
        for model in models:
            model_name = model.get("name")
            if not model_name:
                continue
            
            dependencies[model_name] = set()
            fields = model.get("fields", {})
            for field_info in fields.values():
                field_type = field_info.get("type", "")
                if field_type:
                    # Extract the base type (before any generic brackets)
                    possible_model = field_type.split("[")[0].strip()
                    if possible_model and possible_model[0].isupper():
                        if any(m.get("name") == possible_model for m in models):
                            dependencies[model_name].add(possible_model)
        
        # Topological sort
        sorted_models = []
        visited = set()

        def visit(name):
            if name in visited:
                return
            visited.add(name)
            for dep in dependencies.get(name, []):
                visit(dep)
            model = next((m for m in models if m.get("name") == name), None)
            if model:
                sorted_models.append(model)

        for model in models:
            if model.get("name"):
                visit(model["name"])

        return sorted_models

    def _format_module_section(
        self,
        grouped_contexts: Dict[str, Dict],
        section_type: str
    ) -> List[str]:
        """Format a grouped context section."""
        sections = []
        
        for module, content in grouped_contexts.items():
            section = [f"**Module: {content.get('module', module)}**"]
            
            # Format classes
            if content.get("classes"):
                section.append("**Classes:**")
                for cls in content["classes"]:
                    section.append(f"- **{cls.get('name', 'Unknown')}**")
                    if cls.get("docstring"):
                        section.append(f"  - Description: {cls['docstring']}")
                    if cls.get("methods"):
                        section.append("  - Methods:")
                        for method in cls["methods"]:
                            params = ", ".join(method.get("parameters", []))
                            section.append(f"    - `{method['name']}({params})`")
                            if method.get("docstring"):
                                section.append(f"      {method['docstring'][:100]}")
            
            # Format functions
            if content.get("functions"):
                section.append("**Functions:**")
                for func in content["functions"]:
                    params = ", ".join(func.get("parameters", []))
                    section.append(f"- `{func['name']}({params})`")
                    if func.get("docstring"):
                        section.append(f"  {func['docstring'][:100]}")
            
            sections.append("\n".join(section))
        
        return sections

    def _format_model_content(self, model_info: Dict) -> List[str]:
        """Format model content with field information and relationships."""
        sections = []
        
        if "name" in model_info:
            sections.append(f"- **Class: {model_info['name']}**")
            
            if model_info.get("docstring"):
                sections.append(f"  - **Description:** {model_info['docstring']}")
            
            if model_info.get("bases"):
                sections.append(f"  - **Inherits from:** {', '.join(model_info['bases'])}")
            
            # Format fields with complete metadata
            fields = model_info.get("fields", {})
            if fields:
                sections.append("  - **Fields:**")
                for field_name, field_info in fields.items():
                    field_str = f"    - `{field_name}"
                    
                    if field_info.get("type"):
                        field_str += f": {field_info['type']}"
                    
                    # Build field parameters
                    params = []
                    if field_info.get("default") is not None:
                        default_val = field_info["default"]
                        if default_val == "[]":
                            params.append("default_factory=list")
                        elif default_val == "None":
                            params.append("default=None")
                        else:
                            params.append(f"default={default_val}")
                    
                    if field_info.get("description"):
                        params.append(f'description="{field_info["description"]}"')
                    
                    if field_info.get("constraints"):
                        params.extend(field_info["constraints"])
                    
                    if params:
                        field_str += f" = Field({', '.join(params)})"
                    field_str += "`"
                    
                    # Add relationship info based on type
                    relationship = field_info.get("relationship")
                    if relationship:
                        if "PyObjectId" in field_info.get("type", "") or relationship.endswith("_ref"):
                            if "List[" in field_info.get("type", "") or relationship == "one_to_many_ref":
                                field_str += " (references multiple)"
                            else:
                                field_str += " (references one)"
                        elif relationship in ["one_to_one", "one_to_many"]:
                            if "List[" in field_info.get("type", "") or relationship == "one_to_many":
                                field_str += " (contains multiple)"
                            else:
                                field_str += " (contains one)"
                        elif relationship == "embedded":
                            field_str += " (embedded document)"
                    
                    sections.append(field_str)
            
            # Format validators
            validators = model_info.get("validators", [])
            if validators:
                sections.append("  - **Validators:**")
                for validator in validators:
                    if validator.get("decorators"):
                        for dec in validator["decorators"]:
                            sections.append(f"    - {dec}")
                    sections.append(f"    - `{validator['name']}`")
                    if validator.get("docstring"):
                        sections.append(f"      {validator['docstring']}")
            
            # Format Config
            config = model_info.get("config", {})
            if config:
                sections.append("  - **Config:**")
                for key, value in config.get("attributes", {}).items():
                    sections.append(f"    - {key} = {value}")
        
        return sections

    # =========================================================================
    # Agent-Specific Formatters
    # =========================================================================

    def _format_for_ServiceAgent(self, filtered_context: Dict[str, List[Dict]]) -> str:
        """Format context for ServiceAgent with comprehensive model relationships."""
        config_ctx = filtered_context.get("config_config", [])
        database_ctx = filtered_context.get("database_config", [])
        model_ctx = filtered_context.get("model_context", [])

        grouped_config = self._group_related_contexts(config_ctx, "config")
        grouped_database = self._group_related_contexts(database_ctx, "database")
        grouped_models = self._group_related_contexts(model_ctx, "model")

        import_statements = []
        file_type = "python"
        
        # Database imports
        for module, content in grouped_database.items():
            classes = [cls["name"] for cls in content.get("classes", [])]
            functions = [func["name"] for func in content.get("functions", [])]
            if classes or functions:
                import_statements.append(self.format_import_statement(
                    content["module"], classes + functions, file_type, "ServiceAgent"
                ))
        
        # Model imports
        for module, content in grouped_models.items():
            classes = [cls["name"] for cls in content.get("classes", [])]
            if classes:
                import_statements.append(self.format_import_statement(
                    content["module"], classes, file_type, "ServiceAgent"
                ))

        # Format model sections with dependency ordering
        model_sections = []
        for module, content in grouped_models.items():
            section = [f"**Module: {content['module']}**"]
            ordered_classes = self._group_models_by_dependency(content.get("classes", []))
            for cls in ordered_classes:
                section.extend(self._format_model_content(cls))
            if section:
                model_sections.append("\n".join(section))

        config_sections = self._format_module_section(grouped_config, "config")
        database_sections = self._format_module_section(grouped_database, "database")

        formatted = (
            "### Import Instructions ###\n"
            "1. You MUST correctly import and use all of the following provided modules exactly as given:\n\n"
            f"{chr(10).join(import_statements)}\n\n"
            "### Configuration Context ###\n"
            "1. The following environment variables are set at runtime:\n"
            "   - **Python**: Use `import os` and `os.getenv('MY_VAR')`.\n\n"
            f"{chr(10).join(config_sections)}\n\n"
            "### Database Context ###\n"
            "1. Integrate the database models and methods as per the provided definitions:\n"
            f"{chr(10).join(database_sections)}\n\n"
            "### Model Context ###\n"
            "1. Implement service layer logic according to the following model specifications:\n"
            f"{chr(10).join(model_sections)}\n"
        )

        return formatted

    def _format_for_ControllerAgent(self, filtered_context: Dict[str, List[Dict]]) -> str:
        """Format context for ControllerAgent."""
        config_ctx = filtered_context.get("config_config", [])
        service_ctx = filtered_context.get("service_context", [])

        grouped_config = self._group_related_contexts(config_ctx, "config")
        grouped_services = self._group_related_contexts(service_ctx, "service")

        import_statements = []
        file_type = "python"

        for module, content in grouped_services.items():
            classes = [cls["name"] for cls in content.get("classes", [])]
            if classes:
                import_statements.append(self.format_import_statement(
                    content["module"], classes, file_type, "ControllerAgent"
                ))

        config_sections = []
        for module, content in grouped_config.items():
            section = [f"**Module: {content['module']}**"]
            if content.get("variables"):
                section.append("**Configuration Variables:**")
                for var in sorted(content["variables"]):
                    section.append(f"- **{var}**")
            config_sections.append("\n".join(section))

        service_sections = self._format_module_section(grouped_services, "service")

        formatted = (
            "### Import Instructions ###\n"
            "1. You MUST correctly import and use all of the following provided modules:\n\n"
            f"{chr(10).join(import_statements)}\n\n"
            "### Configuration Context ###\n"
            "1. The following environment variables are available:\n"
            f"{chr(10).join(config_sections)}\n\n"
            "### Service Context ###\n"
            "1. Integrate the service classes and methods as provided:\n"
            f"{chr(10).join(service_sections)}\n\n"
        )

        return formatted

    def _format_default(self, filtered_context: Dict[str, List[Dict]], agent_name: str) -> str:
        """Default formatter for unknown agents."""
        sections = [f"### Code Context for {agent_name} ###\n"]
        
        for context_type, contexts in filtered_context.items():
            if not contexts:
                continue
            sections.append(f"**{context_type}:**\n")
            for ctx in contexts:
                module = ctx.get("module", "unknown")
                sections.append(f"  - Module: {module}")
                if ctx.get("classes"):
                    sections.append(f"    Classes: {len(ctx['classes'])}")
                if ctx.get("functions"):
                    sections.append(f"    Functions: {len(ctx['functions'])}")
            sections.append("")
        
        return "\n".join(sections)


# =========================================================================
# Legacy Compatibility (for existing tools.py)
# =========================================================================

class ContextFormatter:
    """
    Legacy compatibility wrapper.
    Maps intent-based formatting to agent-based formatting.
    """
    
    INTENT_TO_AGENT_MAP = {
        "backend_service_generation": "ServiceAgent",
        "api_routes_generation": "ControllerAgent",
        "imports_check": None,  # Generic
        "symbols_overview": None,  # Generic
    }
    
    def __init__(self, intent_config: Dict[str, Any] = None):
        self.intent_config = intent_config or {}
    
    def format(
        self,
        intent: str,
        contexts: Dict[str, Dict[str, Any]],
        scope: Optional[List[str]] = None,
        max_tokens: Optional[int] = None
    ) -> str:
        """
        Format extracted contexts for a specific intent.
        Maps intent to agent and uses AgentContextFormatter.
        """
        # Convert file-keyed contexts to context_type-keyed
        aggregated = {}
        for file_path, ctx in contexts.items():
            ctx_type = ctx.get("context_type", "raw_context")
            if ctx_type not in aggregated:
                aggregated[ctx_type] = []
            aggregated[ctx_type].append(ctx)
        
        # Map intent to agent
        agent_name = self.INTENT_TO_AGENT_MAP.get(intent)
        if agent_name:
            formatter = AgentContextFormatter(aggregated)
            return formatter.format_for_agent(agent_name)
        
        # Fallback to generic formatting
        return self._format_generic(contexts, intent, max_tokens)
    
    def _format_generic(
        self,
        contexts: Dict[str, Dict[str, Any]],
        intent: str,
        max_tokens: Optional[int]
    ) -> str:
        """Generic formatting for unmapped intents."""
        sections = [f"### Code Context ({intent}) ###\n\n"]
        
        for file_path, context in sorted(contexts.items()):
            sections.append(f"**{file_path}** ({context.get('language', 'unknown')})\n")
            
            if context.get("imports"):
                sections.append("  Imports:\n")
                for imp in context["imports"][:10]:
                    if isinstance(imp, dict):
                        sections.append(f"    - {imp.get('module', imp)}\n")
                    else:
                        sections.append(f"    - {imp}\n")
            
            if context.get("classes"):
                sections.append("  Classes:\n")
                for cls in context["classes"][:10]:
                    name = cls.get("name", "Unknown") if isinstance(cls, dict) else str(cls)
                    sections.append(f"    - {name}\n")
            
            if context.get("functions"):
                sections.append("  Functions:\n")
                for func in context["functions"][:10]:
                    name = func.get("name", "Unknown") if isinstance(func, dict) else str(func)
                    sections.append(f"    - {name}\n")
            
            sections.append("\n")
        
        result = "".join(sections)
        
        if max_tokens:
            max_chars = max_tokens * 4
            if len(result) > max_chars:
                result = result[:max_chars] + "\n\n... (truncated)"
        
        return result


# Default intent definitions (legacy compatibility)
DEFAULT_INTENTS = {
    "backend_service_generation": {
        "includes": ["imports", "symbols", "framework_hints"],
        "symbol_types": ["class", "function"],
        "max_tokens": 8000,
        "format": "structured"
    },
    "api_routes_generation": {
        "includes": ["imports", "symbols", "exports"],
        "symbol_types": ["function", "class"],
        "max_tokens": 6000,
        "format": "structured"
    },
    "frontend_components_generation": {
        "includes": ["imports", "symbols", "exports"],
        "symbol_types": ["component", "function"],
        "max_tokens": 6000,
        "format": "structured"
    },
    "imports_check": {
        "includes": ["imports"],
        "max_tokens": 2000,
        "format": "imports_only"
    },
    "symbols_overview": {
        "includes": ["symbols"],
        "max_tokens": 4000,
        "format": "symbols_only"
    }
}


# Export main classes
__all__ = [
    "AgentContextFormatter",
    "ContextFormatter",  # Legacy
    "DEFAULT_INTENTS"    # Legacy
]
