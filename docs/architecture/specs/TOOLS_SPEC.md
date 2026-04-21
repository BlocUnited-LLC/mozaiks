# Tools Specification

**Status:** Specification
**Created:** 2026-04-06
**Depends on:** MODULAR_ARCHITECTURE_V2.md, WORKFLOW_TRIGGERS_SPEC.md

This document specifies the tool model for AI workflows in the Mozaiks system.

---

## Overview

Tools are the mechanism by which AI agents interact with the outside world. They bridge the gap between natural language understanding and concrete actions.

### Core Principle

**Tools are interfaces, not implementations.**

Tools should:
- Invoke modules, external APIs, or AI capabilities
- NOT contain business logic themselves
- Maintain clear separation between AI orchestration and data operations

---

## 1. Tool Categories

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           TOOL CATEGORIES                                    │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  SYSTEM TOOLS                  INTEGRATION TOOLS           AI TOOLS         │
│  (Module Interaction)          (External Services)         (LLM-Driven)     │
│                                                                              │
│  ├── get_contacts              ├── search_web              ├── summarize    │
│  ├── create_deal               ├── send_email              ├── analyze      │
│  ├── update_note               ├── fetch_weather           ├── generate     │
│  ├── list_tasks                ├── query_database          ├── classify     │
│  └── delete_contact            └── call_api                └── extract      │
│                                                                              │
│  Characteristic:               Characteristic:             Characteristic:  │
│  • Thin wrapper over           • Wraps external APIs       • Uses LLM for   │
│    module executor             • Handles auth              •   processing   │
│  • No business logic           • Maps data formats         • Stateless      │
│  • Context-aware               • Error handling            • Declarative    │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### Category Definitions

#### System Tools

System tools interact with the application's modules. They are thin wrappers that:
- Call module executor with proper context
- Transform results for agent consumption
- DO NOT contain business logic (that belongs in modules)

```python
# workflows/assistant/tools/contacts.py

from mozaiks_core.interfaces import ExecutionRequest, ExecutorType


async def get_contacts(context, limit: int = 10, status: str = None):
    """
    Get contacts from the CRM.

    Args:
        limit: Maximum number of contacts to return
        status: Filter by status (active, inactive, all)

    Returns:
        List of contacts with name, email, and company
    """
    # Get module executor from context (injected by runtime)
    module_executor = context.executors.get("modules")

    if not module_executor:
        return {"error": "Modules not available in this execution mode"}

    # Execute via module
    result = await module_executor.execute(
        ExecutionRequest(
            executor_type=ExecutorType.MODULE,
            target="contacts",
            action="list",
            params={"limit": limit, "status": status},
            app_id=context.app_id,
            user_id=context.user_id,
        )
    )

    if not result.success:
        return {"error": result.error}

    # Transform for agent (simplify structure)
    return {
        "contacts": [
            {
                "id": c["id"],
                "name": c["name"],
                "email": c["email"],
                "company": c.get("company"),
            }
            for c in result.data.get("items", [])
        ],
        "total": result.data.get("total", 0),
    }
```

#### Integration Tools

Integration tools connect to external services. They handle authentication, data transformation, and error handling.

```python
# workflows/assistant/tools/search.py

import httpx


async def search_web(context, query: str, max_results: int = 5):
    """
    Search the web using a search API.

    Args:
        query: Search query
        max_results: Maximum number of results

    Returns:
        Search results with title, snippet, and URL
    """
    api_key = context.get_secret("SEARCH_API_KEY")

    async with httpx.AsyncClient() as client:
        response = await client.get(
            "https://api.search.example.com/search",
            params={"q": query, "count": max_results},
            headers={"Authorization": f"Bearer {api_key}"},
        )
        response.raise_for_status()

        data = response.json()

        return {
            "results": [
                {
                    "title": r["title"],
                    "snippet": r["snippet"],
                    "url": r["url"],
                }
                for r in data.get("results", [])
            ]
        }
```

#### AI Tools

AI tools use the LLM itself for processing. They are declarative and rely on the agent's language capabilities.

```python
# workflows/assistant/tools/analyze.py

async def analyze_text(context, text: str, aspects: list[str]):
    """
    Analyze text for specific aspects.

    This is a "thinking" tool - the agent will analyze the text
    using its language understanding capabilities.

    Args:
        text: Text to analyze
        aspects: What to analyze (sentiment, topics, entities, etc.)

    Returns:
        Analysis results for each aspect
    """
    # This is a "meta" tool - the agent handles the analysis
    # The tool just structures the request
    return {
        "instruction": f"Analyze the following text for: {', '.join(aspects)}",
        "text": text,
        "expected_output": {
            aspect: "your analysis here" for aspect in aspects
        }
    }
```

---

## 2. Tool Definition

### YAML Definition

```yaml
# workflows/assistant/tools.yaml

tools:
  # System tool
  - name: get_contacts
    description: Get contacts from the CRM
    category: system
    module: contacts
    action: list
    parameters:
      - name: limit
        type: integer
        description: Maximum number of contacts to return
        default: 10
      - name: status
        type: string
        description: Filter by status
        enum: [active, inactive, all]
        default: all
    returns:
      type: object
      properties:
        contacts:
          type: array
          items:
            type: object
            properties:
              id: {type: string}
              name: {type: string}
              email: {type: string}
              company: {type: string}
        total:
          type: integer

  # Integration tool
  - name: search_web
    description: Search the web for information
    category: integration
    handler: tools/search.py:search_web
    parameters:
      - name: query
        type: string
        description: Search query
        required: true
      - name: max_results
        type: integer
        description: Maximum results
        default: 5
    secrets:
      - SEARCH_API_KEY

  # AI tool
  - name: summarize
    description: Summarize text content
    category: ai
    builtin: summarize
    parameters:
      - name: text
        type: string
        description: Text to summarize
        required: true
      - name: max_length
        type: integer
        description: Maximum summary length in words
        default: 100
```

### Python Implementation Pattern

```python
# workflows/assistant/tools/base.py

from typing import Protocol, Dict, Any, Optional
from dataclasses import dataclass


@dataclass
class ToolParameter:
    """Tool parameter definition."""
    name: str
    type: str
    description: str
    required: bool = False
    default: Any = None
    enum: Optional[list] = None


@dataclass
class ToolDefinition:
    """Complete tool definition."""
    name: str
    description: str
    category: str  # system | integration | ai
    parameters: list[ToolParameter]

    # For system tools
    module: Optional[str] = None
    action: Optional[str] = None

    # For integration tools
    handler: Optional[str] = None
    secrets: list[str] = None

    # For AI tools
    builtin: Optional[str] = None


class Tool(Protocol):
    """Tool protocol."""

    @property
    def definition(self) -> ToolDefinition:
        """Return tool definition."""
        ...

    async def execute(
        self,
        context: 'ExecutionContext',
        **params,
    ) -> Dict[str, Any]:
        """Execute the tool."""
        ...
```

---

## 3. Tool Registration

### Tool Registry

```python
# packages/ai/src/mozaiks_ai/tools/registry.py

class ToolRegistry:
    """Central registry for all tools."""

    def __init__(self):
        self._tools: Dict[str, Tool] = {}
        self._by_category: Dict[str, List[str]] = {
            "system": [],
            "integration": [],
            "ai": [],
        }

    def register(self, tool: Tool):
        """Register a tool."""
        name = tool.definition.name
        if name in self._tools:
            raise ValueError(f"Tool {name} already registered")

        self._tools[name] = tool
        self._by_category[tool.definition.category].append(name)

    def get(self, name: str) -> Optional[Tool]:
        """Get a tool by name."""
        return self._tools.get(name)

    def list_tools(self, category: str = None) -> List[ToolDefinition]:
        """List all tools or tools in a category."""
        if category:
            names = self._by_category.get(category, [])
        else:
            names = self._tools.keys()

        return [self._tools[n].definition for n in names]

    def get_openai_schema(self, tool_names: List[str] = None) -> List[Dict]:
        """Get OpenAI-compatible tool schemas."""
        tools = tool_names or self._tools.keys()
        return [
            self._to_openai_schema(self._tools[n].definition)
            for n in tools
            if n in self._tools
        ]

    def _to_openai_schema(self, definition: ToolDefinition) -> Dict:
        """Convert to OpenAI function schema."""
        return {
            "type": "function",
            "function": {
                "name": definition.name,
                "description": definition.description,
                "parameters": {
                    "type": "object",
                    "properties": {
                        p.name: self._param_to_schema(p)
                        for p in definition.parameters
                    },
                    "required": [
                        p.name for p in definition.parameters if p.required
                    ],
                },
            },
        }
```

### Loading Tools from Workflow

```python
# packages/ai/src/mozaiks_ai/tools/loader.py

class ToolLoader:
    """Loads tools from workflow definition."""

    def __init__(self, workflow_path: str):
        self._path = Path(workflow_path)

    async def load(self) -> List[Tool]:
        """Load all tools for a workflow."""
        tools_yaml = self._path / "tools.yaml"

        if not tools_yaml.exists():
            return []

        with open(tools_yaml) as f:
            config = yaml.safe_load(f)

        tools = []
        for tool_def in config.get("tools", []):
            tool = await self._create_tool(tool_def)
            tools.append(tool)

        return tools

    async def _create_tool(self, config: Dict) -> Tool:
        """Create a tool from config."""
        category = config.get("category", "system")

        if category == "system":
            return SystemTool(
                name=config["name"],
                description=config["description"],
                module=config["module"],
                action=config["action"],
                parameters=self._parse_params(config.get("parameters", [])),
            )

        elif category == "integration":
            handler = await self._load_handler(config["handler"])
            return IntegrationTool(
                name=config["name"],
                description=config["description"],
                handler=handler,
                parameters=self._parse_params(config.get("parameters", [])),
                secrets=config.get("secrets", []),
            )

        elif category == "ai":
            return AITool(
                name=config["name"],
                description=config["description"],
                builtin=config.get("builtin"),
                parameters=self._parse_params(config.get("parameters", [])),
            )

        else:
            raise ValueError(f"Unknown tool category: {category}")
```

---

## 4. Tool Execution

### Execution Context

```python
# packages/ai/src/mozaiks_ai/tools/context.py

@dataclass
class ToolContext:
    """Context available to tools during execution."""

    # Identity
    app_id: str
    user_id: Optional[str]
    session_id: Optional[str]

    # Executors (injected by runtime)
    executors: Dict[str, 'Executor']

    # Secrets accessor
    _secrets: Dict[str, str]

    def get_executor(self, name: str) -> Optional['Executor']:
        """Get an executor by name."""
        return self.executors.get(name)

    def get_secret(self, name: str) -> Optional[str]:
        """Get a secret value."""
        return self._secrets.get(name)

    @property
    def module_executor(self) -> Optional['ModuleExecutor']:
        """Shortcut for module executor."""
        return self.executors.get("modules")
```

### Tool Executor

```python
# packages/ai/src/mozaiks_ai/tools/executor.py

class ToolExecutor:
    """Executes tools with proper context."""

    def __init__(
        self,
        registry: ToolRegistry,
        context_provider: Callable[[], ToolContext],
    ):
        self._registry = registry
        self._get_context = context_provider

    async def execute(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Execute a tool by name."""
        tool = self._registry.get(tool_name)
        if not tool:
            return {"error": f"Unknown tool: {tool_name}"}

        context = self._get_context()

        try:
            # Validate arguments
            validated = self._validate_arguments(tool.definition, arguments)

            # Execute
            result = await tool.execute(context, **validated)

            # Emit tool execution event
            await self._emit_event(tool_name, arguments, result)

            return result

        except Exception as e:
            return {"error": str(e)}

    def _validate_arguments(
        self,
        definition: ToolDefinition,
        arguments: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Validate and fill defaults."""
        validated = {}

        for param in definition.parameters:
            if param.name in arguments:
                value = arguments[param.name]
                # Type coercion could go here
                validated[param.name] = value
            elif param.default is not None:
                validated[param.name] = param.default
            elif param.required:
                raise ValueError(f"Missing required parameter: {param.name}")

        return validated
```

---

## 5. System Tools (Module Interaction)

### Auto-Generated System Tools

For each module, system tools can be auto-generated:

```python
# packages/ai/src/mozaiks_ai/tools/system.py

class SystemToolGenerator:
    """Generates system tools from module definitions."""

    def generate_for_module(self, module_config: Dict) -> List[Tool]:
        """Generate CRUD tools for a module."""
        module_name = module_config["name"]
        tools = []

        # List action
        if "list" in module_config.get("actions", ["list", "get", "create", "update", "delete"]):
            tools.append(self._create_list_tool(module_name, module_config))

        # Get action
        if "get" in module_config.get("actions", []):
            tools.append(self._create_get_tool(module_name, module_config))

        # Create action
        if "create" in module_config.get("actions", []):
            tools.append(self._create_create_tool(module_name, module_config))

        # etc.

        return tools

    def _create_list_tool(self, module_name: str, config: Dict) -> SystemTool:
        """Create a list tool."""
        return SystemTool(
            name=f"list_{module_name}",
            description=f"List {module_name} from the database",
            module=module_name,
            action="list",
            parameters=[
                ToolParameter(name="limit", type="integer", description="Max results", default=10),
                ToolParameter(name="offset", type="integer", description="Skip results", default=0),
                # Add filters based on module schema
            ],
        )
```

### SystemTool Implementation

```python
# packages/ai/src/mozaiks_ai/tools/system.py

class SystemTool:
    """Tool that wraps module execution."""

    def __init__(
        self,
        name: str,
        description: str,
        module: str,
        action: str,
        parameters: List[ToolParameter],
    ):
        self._name = name
        self._description = description
        self._module = module
        self._action = action
        self._parameters = parameters

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self._name,
            description=self._description,
            category="system",
            module=self._module,
            action=self._action,
            parameters=self._parameters,
        )

    async def execute(self, context: ToolContext, **params) -> Dict[str, Any]:
        """Execute via module executor."""
        executor = context.module_executor

        if not executor:
            return {
                "error": "Module executor not available",
                "hint": "This tool requires modules to be enabled",
            }

        result = await executor.execute(
            ExecutionRequest(
                executor_type=ExecutorType.MODULE,
                target=self._module,
                action=self._action,
                params=params,
                app_id=context.app_id,
                user_id=context.user_id,
            )
        )

        if result.success:
            return result.data
        else:
            return {"error": result.error}
```

---

## 6. Tool Constraints

### What Tools Should NOT Do

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                       TOOL ANTI-PATTERNS                                     │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ❌ TOOLS SHOULD NOT:                                                        │
│                                                                              │
│  1. Contain business logic                                                  │
│     ┌───────────────────────────────────────────────────────────────────┐  │
│     │ # BAD - business logic in tool                                     │  │
│     │ async def create_contact(context, name, email):                    │  │
│     │     # Validation should be in module                               │  │
│     │     if not email.endswith("@company.com"):                         │  │
│     │         return {"error": "Invalid email"}                          │  │
│     │                                                                    │  │
│     │     # This logic belongs in module                                 │  │
│     │     contact = Contact(name=name, email=email)                      │  │
│     │     await db.insert(contact)                                       │  │
│     │     return {"id": contact.id}                                      │  │
│     └───────────────────────────────────────────────────────────────────┘  │
│                                                                              │
│  2. Directly access the database                                            │
│     ┌───────────────────────────────────────────────────────────────────┐  │
│     │ # BAD - direct DB access                                           │  │
│     │ async def get_contacts(context):                                   │  │
│     │     return await db.contacts.find({})                              │  │
│     └───────────────────────────────────────────────────────────────────┘  │
│                                                                              │
│  3. Handle their own authentication                                         │
│     ┌───────────────────────────────────────────────────────────────────┐  │
│     │ # BAD - tool does auth                                             │  │
│     │ async def get_contacts(context, token):                            │  │
│     │     user = verify_token(token)  # Don't do this!                   │  │
│     │     ...                                                            │  │
│     └───────────────────────────────────────────────────────────────────┘  │
│                                                                              │
│  4. Maintain state between calls                                            │
│     ┌───────────────────────────────────────────────────────────────────┐  │
│     │ # BAD - stateful tool                                              │  │
│     │ class ContactTool:                                                 │  │
│     │     def __init__(self):                                            │  │
│     │         self.cache = {}  # Don't cache in tools!                   │  │
│     └───────────────────────────────────────────────────────────────────┘  │
│                                                                              │
│  5. Call other tools directly                                               │
│     ┌───────────────────────────────────────────────────────────────────┐  │
│     │ # BAD - tool calls tool                                            │  │
│     │ async def analyze_contacts(context):                               │  │
│     │     contacts = await get_contacts(context)  # Don't!               │  │
│     │     return analyze(contacts)                                       │  │
│     └───────────────────────────────────────────────────────────────────┘  │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

### What Tools SHOULD Do

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                         TOOL BEST PRACTICES                                  │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ✅ TOOLS SHOULD:                                                            │
│                                                                              │
│  1. Be thin wrappers                                                        │
│     ┌───────────────────────────────────────────────────────────────────┐  │
│     │ # GOOD - thin wrapper over module                                  │  │
│     │ async def get_contacts(context, limit=10):                         │  │
│     │     result = await context.module_executor.execute(                │  │
│     │         ExecutionRequest(                                          │  │
│     │             target="contacts",                                     │  │
│     │             action="list",                                         │  │
│     │             params={"limit": limit}                                │  │
│     │         )                                                          │  │
│     │     )                                                              │  │
│     │     return result.data                                             │  │
│     └───────────────────────────────────────────────────────────────────┘  │
│                                                                              │
│  2. Transform data for agent consumption                                    │
│     ┌───────────────────────────────────────────────────────────────────┐  │
│     │ # GOOD - simplify response for agent                               │  │
│     │ async def get_contact_summary(context, contact_id):                │  │
│     │     result = await context.module_executor.execute(...)            │  │
│     │     contact = result.data                                          │  │
│     │                                                                    │  │
│     │     # Transform for agent (don't expose internal IDs, etc.)        │  │
│     │     return {                                                       │  │
│     │         "name": contact["name"],                                   │  │
│     │         "status": contact["status"],                               │  │
│     │         "recent_activity": contact["activity"][:5],                │  │
│     │     }                                                              │  │
│     └───────────────────────────────────────────────────────────────────┘  │
│                                                                              │
│  3. Handle errors gracefully                                                │
│     ┌───────────────────────────────────────────────────────────────────┐  │
│     │ # GOOD - meaningful error messages                                 │  │
│     │ async def get_contact(context, contact_id):                        │  │
│     │     result = await context.module_executor.execute(...)            │  │
│     │                                                                    │  │
│     │     if not result.success:                                         │  │
│     │         if "not found" in result.error.lower():                    │  │
│     │             return {"error": f"Contact {contact_id} not found"}    │  │
│     │         return {"error": "Unable to fetch contact"}                │  │
│     │                                                                    │  │
│     │     return result.data                                             │  │
│     └───────────────────────────────────────────────────────────────────┘  │
│                                                                              │
│  4. Use context for identity                                                │
│     ┌───────────────────────────────────────────────────────────────────┐  │
│     │ # GOOD - identity from context                                     │  │
│     │ async def get_my_tasks(context):                                   │  │
│     │     return await context.module_executor.execute(                  │  │
│     │         ExecutionRequest(                                          │  │
│     │             target="tasks",                                        │  │
│     │             action="list",                                         │  │
│     │             params={"user_id": context.user_id},  # From context   │  │
│     │             app_id=context.app_id,                                 │  │
│     │             user_id=context.user_id,                               │  │
│     │         )                                                          │  │
│     │     )                                                              │  │
│     └───────────────────────────────────────────────────────────────────┘  │
│                                                                              │
│  5. Be stateless and idempotent                                             │
│     ┌───────────────────────────────────────────────────────────────────┐  │
│     │ # GOOD - no state, can be called multiple times safely             │  │
│     │ async def get_weather(context, city: str):                         │  │
│     │     # Pure function - same input always gives same output          │  │
│     │     response = await http.get(f"{API}/weather?city={city}")        │  │
│     │     return response.json()                                         │  │
│     └───────────────────────────────────────────────────────────────────┘  │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 7. Built-in Tools

### Core Built-in Tools

```yaml
# Built-in tools available in all workflows

builtin_tools:
  # Memory tools
  - name: remember
    description: Store information for later recall
    category: ai
    builtin: memory_store

  - name: recall
    description: Retrieve previously stored information
    category: ai
    builtin: memory_retrieve

  # UI tools
  - name: show_artifact
    description: Display a rich artifact to the user
    category: system
    builtin: ui_artifact

  - name: ask_confirmation
    description: Ask user for confirmation before proceeding
    category: system
    builtin: ui_confirm

  - name: show_progress
    description: Show progress to the user
    category: system
    builtin: ui_progress

  # Utility tools
  - name: wait
    description: Wait for a specified duration
    category: system
    builtin: util_wait

  - name: current_time
    description: Get the current date and time
    category: system
    builtin: util_time
```

### UI Tools (Artifact Generation)

```python
# packages/ai/src/mozaiks_ai/tools/builtin/ui.py

class ShowArtifactTool:
    """Tool for displaying rich artifacts in chat."""

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="show_artifact",
            description="Display a rich artifact (chart, table, code) to the user",
            category="system",
            parameters=[
                ToolParameter(
                    name="type",
                    type="string",
                    description="Artifact type",
                    required=True,
                    enum=["chart", "table", "code", "markdown", "image"],
                ),
                ToolParameter(
                    name="title",
                    type="string",
                    description="Artifact title",
                ),
                ToolParameter(
                    name="data",
                    type="object",
                    description="Artifact data (structure depends on type)",
                    required=True,
                ),
            ],
        )

    async def execute(self, context: ToolContext, **params) -> Dict:
        """Emit artifact for UI rendering."""
        artifact = {
            "id": f"art_{uuid.uuid4().hex[:8]}",
            "type": params["type"],
            "title": params.get("title"),
            "data": params["data"],
            "created_at": datetime.utcnow().isoformat(),
        }

        # Emit via transport (UI will render)
        await context.emit_event("chat.artifact", artifact)

        return {
            "artifact_id": artifact["id"],
            "displayed": True,
        }
```

---

## 8. Tool Events

### Tool Execution Events

```yaml
# Event: Tool Called
type: "Orchestration.ToolCalled"
payload:
  workflow: assistant
  run_id: run_123
  tool_name: get_contacts
  arguments:
    limit: 10
  timestamp: "2026-04-06T10:30:15Z"

# Event: Tool Completed
type: "Orchestration.ToolCompleted"
payload:
  workflow: assistant
  run_id: run_123
  tool_name: get_contacts
  duration_ms: 45
  result_size: 10  # Number of items returned
  success: true

# Event: Tool Failed
type: "Orchestration.ToolFailed"
payload:
  workflow: assistant
  run_id: run_123
  tool_name: get_contacts
  error: "Module not available"
  duration_ms: 5
```

---

## 9. Tool Security

### Permission Model

```yaml
# workflows/assistant/orchestrator.yaml

tools:
  permissions:
    # Which tools require explicit user consent
    require_consent:
      - send_email
      - delete_contact
      - export_data

    # Rate limits per tool
    rate_limits:
      search_web: 10/minute
      send_email: 5/minute

    # Tools disabled for certain roles
    role_restrictions:
      admin_only:
        - delete_all_contacts
        - export_all_data
      owner_only:
        - billing_operations
```

### Input Validation

```python
# packages/ai/src/mozaiks_ai/tools/security.py

class ToolSecurityValidator:
    """Validates tool execution for security."""

    def validate_execution(
        self,
        tool: Tool,
        context: ToolContext,
        arguments: Dict,
    ) -> Tuple[bool, Optional[str]]:
        """Validate if tool execution is allowed."""

        # Check consent requirements
        if tool.definition.name in self._require_consent:
            if not context.has_consent(tool.definition.name):
                return False, "User consent required"

        # Check rate limits
        if not self._check_rate_limit(tool.definition.name, context.user_id):
            return False, "Rate limit exceeded"

        # Check role restrictions
        if not self._check_role(tool.definition.name, context.user.roles):
            return False, "Insufficient permissions"

        # Validate argument types
        if not self._validate_arguments(tool.definition, arguments):
            return False, "Invalid arguments"

        return True, None
```

---

## Summary

### Tool Categories

| Category | Purpose | Data Source | Example |
|----------|---------|-------------|---------|
| **System** | Module interaction | Module executor | `get_contacts`, `create_deal` |
| **Integration** | External APIs | HTTP/SDK | `search_web`, `send_email` |
| **AI** | LLM processing | Agent itself | `summarize`, `classify` |

### Key Principles

1. **Tools are interfaces** - They invoke, not implement
2. **Business logic belongs in modules** - Tools are thin wrappers
3. **Context provides identity** - No auth handling in tools
4. **Stateless execution** - Tools don't maintain state
5. **Events for observability** - All executions are logged
6. **Security is layered** - Consent, rate limits, role checks

### Tool Checklist

- [ ] Is this tool a thin wrapper? (No business logic)
- [ ] Does it use context for identity? (No auth handling)
- [ ] Is it stateless? (No caching or state)
- [ ] Does it transform data appropriately? (Simplified for agent)
- [ ] Does it handle errors gracefully? (Meaningful messages)
- [ ] Is it properly categorized? (system/integration/ai)
