# Instruction Prompt: Adding Tools to a Workflow

!!! tip "New to Development?"

    Copy this into Claude Code, Cursor, or Copilot:

    ```
    I want to add tools to my Mozaiks workflow.

    Please read the instruction prompt at:
    docs/instruction-prompts/adding-workflows/03-tools.md

    My workflow is: [WorkflowName]
    I need tools for: [Describe what actions your agents need]
    ```

---

**Task:** Create Python tool implementations and configure `tools.yaml`

**Complexity:** Medium

---

## Context for AI Agent

You are helping a user add tools to a workflow under
`platform/workflows/[WorkflowName]/`.

Tool declarations belong in:

- `platform/workflows/[WorkflowName]/tools.yaml`

Tool implementations belong in:

- `platform/workflows/[WorkflowName]/tools/*.py`

Workflow UI components referenced by UI tools belong in:

- `platform/workflows/[WorkflowName]/ui/`

---

## Step 1: Understand Tool Requirements

Ask the user:

1. What actions should the workflow agents take?
2. What inputs does each action need?
3. Which actions require inline or artifact UI?

---

## Step 2: Categorize the Tools

Use two categories:

- `Standard` for lookups, mutations, integrations, or calculations
- `UI_Tool` for tools that surface a workflow UI component to the user

---

## Step 3: Create `tools.yaml` Entries

### Standard tool template

```yaml
tools:
  - agent: [AgentName]
    file: [tool_name].py
    function: [tool_name]
    description: "[What the tool does]"
    tool_type: Standard
    auto_invoke: false
```

### UI tool template

```yaml
tools:
  - agent: [AgentName]
    file: [tool_name].py
    function: [tool_name]
    description: "[What the tool does]"
    tool_type: UI_Tool
    auto_invoke: true
    ui:
      component: [ComponentName]
      mode: inline
```

The `ui.component` value must match the exported key from
`platform/workflows/[WorkflowName]/ui/index.js`.

---

## Step 4: Implement Standard Tools

```python
from typing import Any, Dict

async def [tool_name](
    [required_param]: [type],
    [optional_param]: [type] = [default],
) -> Dict[str, Any]:
    """[Description]."""
    try:
        result = await your_logic([required_param], [optional_param])
        return {
            "status": "success",
            "data": result,
        }
    except Exception as exc:
        return {
            "status": "error",
            "error": str(exc),
        }
```

Use JSON-serializable return payloads.

---

## Step 5: Implement UI Tools Carefully

UI tool implementations need to match the current runtime contract used by the
repo.

Because that contract can evolve, do not invent a helper import path from old
docs. Instead:

1. Mirror the current live workflow examples already in the repo.
2. Keep `tools.yaml -> ui.component` aligned with the workflow's `ui/index.js`.
3. Return or emit only the payload shape the current runtime expects.

If unsure, inspect an existing workflow before generating a new UI tool.

---

## Step 6: Validation Checklist

- tool file names match `tools.yaml`
- function names match `tools.yaml`
- tool payloads are JSON-serializable
- UI tools reference components exported from `platform/workflows/[WorkflowName]/ui/index.js`
- the workflow still loads in `/api/workflows`
async def my_tool(
    *,
    context_variables: dict = None,
    chat_id: Optional[str] = None,
    workflow_name: str = "MyWorkflow",
) -> Dict[str, Any]:
    """Tool that uses context variables."""

    # Safely extract context values
    ctx = context_variables or {}
    user_name = ctx.get("user_name")
    order_id = ctx.get("current_order_id")

    # Use in your logic
    if not order_id:
        return {
            "status": "error",
            "error": "No order ID in context. Please ask the user for their order ID.",
        }

    # Continue with logic...
```

---

## Step 7: Verify Tools

### Check 1: YAML Syntax
```powershell
python -c "import yaml; print(yaml.safe_load(open('workflows/[WorkflowName]/tools.yaml')))"
```

### Check 2: Python Imports
```powershell
cd workflows/[WorkflowName]/tools
python -c "from [tool_name] import [tool_name]; print('OK')"
```

### Check 3: Function Signature
Ensure async functions return `Dict[str, Any]`:
```python
# Good
async def my_tool() -> Dict[str, Any]:

# Bad - missing async
def my_tool() -> Dict[str, Any]:

# Bad - wrong return type
async def my_tool() -> str:
```

---

## Step 8: Summary Template

After creating tools:

```markdown
## Tools Created

### Standard Tools
| Tool | Agent | Description |
|------|-------|-------------|
| `[tool_name]` | [Agent] | [Description] |

### UI Tools
| Tool | Agent | Component | Mode |
|------|-------|-----------|------|
| `[tool_name]` | [Agent] | [Component] | [Mode] |

### Files Created
- ✅ `workflows/[WorkflowName]/tools.yaml` — Updated with tool definitions
- ✅ `workflows/[WorkflowName]/tools/[tool_name].py` — Tool implementation

### Next Steps
For UI Tools, you need to create the React components.
See: docs/instruction-prompts/adding-workflows/04-ui-components.md
```

---

## Troubleshooting

### "Tool not found" error
1. Check `file` in tools.yaml points to correct file
2. Check `function` matches the Python function name exactly
3. Verify file is in `tools/` subfolder

### "Missing required argument" error
1. Check function signature matches what agent is calling
2. Ensure required parameters have no default values

### UI Tool not rendering
1. Verify `ui.component` matches React export exactly
2. Check React component is exported from `components/index.js`
3. Verify workflow is registered in `chat-ui/src/workflows/index.js`

### Tool timing out
1. Check for blocking I/O (use async/await)
2. Add timeout handling for external API calls
3. Check database connection pool

