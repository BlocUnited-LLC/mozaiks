# Tools

Tools give your agents the ability to take actions — look up data, send emails, show UI components, and more.

---

## Tool Types

| Type | Purpose | Example |
|------|---------|---------|
| **Data Tools** | Fetch or update information | `get_order_status`, `update_ticket` |
| **Action Tools** | Perform operations | `send_email`, `create_ticket` |
| **UI Tools** | Show interactive components | `show_calendar`, `show_form` |

---

!!! tip "New to Development?"

    **Let AI create your tools!** Copy this prompt into Claude Code:

    ```
    I want to add tools to my Mozaiks workflow.

    Please read the instruction prompt at:
    docs/instruction-prompts/adding-workflows/03-tools.md

    My workflow is: [WorkflowName]
    I need tools for: [Describe what actions your agents need]
    ```

---

## Quick Start

### 1. Define in tools.yaml

```yaml
tools:
  - agent: MyAgent
    file: my_tool.py
    function: my_tool
    description: "Does something useful."
    tool_type: Standard
    auto_invoke: false
```

### 2. Create Python Implementation

```python
# workflows/MyWorkflow/tools/my_tool.py
from typing import Any, Dict

async def my_tool(
    param1: str,
    param2: int = 10,
) -> Dict[str, Any]:
    """Does something useful.

    Args:
        param1: Description of param1
        param2: Description of param2
    """
    # Your logic here
    result = do_something(param1, param2)

    return {"status": "success", "data": result}
```

---

## tools.yaml Reference

```yaml
tools:
  - agent: MyAgent           # Which agent can use this tool
    file: my_tool.py         # Python file in tools/ folder
    function: my_tool        # Function name to call
    description: "..."       # What the tool does (shown to LLM)
    tool_type: Standard      # Standard | UI_Tool
    auto_invoke: false       # Auto-call when mentioned?

    # For UI tools only:
    ui:
      component: MyCard      # React component name
      mode: inline           # inline | artifact
```

### Tool Type Options

| Type | When to Use |
|------|-------------|
| `Standard` | Data lookups, API calls, background actions |
| `UI_Tool` | Shows interactive UI in the chat |

---

## Standard Tool Example

A tool that looks up order status:

**tools.yaml:**
```yaml
tools:
  - agent: OrderAgent
    file: get_order_status.py
    function: get_order_status
    description: "Look up the status of a customer order by order ID."
    tool_type: Standard
    auto_invoke: false
```

**tools/get_order_status.py:**
```python
from typing import Any, Dict

async def get_order_status(
    order_id: str,
) -> Dict[str, Any]:
    """Look up order status.

    Args:
        order_id: The order ID to look up
    """
    # In real app, query your database
    # This is a mock example
    order_data = {
        "order_id": order_id,
        "status": "shipped",
        "tracking_number": "1Z999AA10123456784",
        "estimated_delivery": "2024-03-15",
    }

    return {
        "status": "success",
        "data": order_data,
    }
```

---

## UI Tool Example

A tool that shows a date picker:

**tools.yaml:**
```yaml
tools:
  - agent: BookingAgent
    file: show_calendar.py
    function: show_calendar
    description: "Display a calendar for the user to select a date."
    tool_type: UI_Tool
    auto_invoke: true
    ui:
      component: CalendarPicker
      mode: inline
```

**tools/show_calendar.py:**
```python
from typing import Any, Dict, Optional
from mozaiksai.core.workflow.outputs.ui_tools import use_ui_tool

async def show_calendar(
    *,
    available_dates: list[str] = None,
    chat_id: Optional[str] = None,
    workflow_name: str = "MyWorkflow",
) -> Dict[str, Any]:
    """Display a calendar picker.

    Args:
        available_dates: List of available date strings
    """
    response = await use_ui_tool(
        tool_id="CalendarPicker",
        payload={
            "available_dates": available_dates or [],
            "message": "Please select a date:",
        },
        chat_id=chat_id,
        workflow_name=workflow_name,
    )

    return {
        "status": "success",
        "selected_date": response.get("data", {}).get("selected_date"),
    }
```

The `use_ui_tool()` function handles all the WebSocket communication and waits for the user's response.

---

## Accessing Context Variables

Tools can read shared state from context variables:

```python
async def my_tool(
    *,
    context_variables: dict = None,
    chat_id: Optional[str] = None,
    workflow_name: str = "MyWorkflow",
) -> Dict[str, Any]:
    # Extract from context
    user_name = context_variables.get("user_name") if context_variables else None
    order_id = context_variables.get("order_id") if context_variables else None

    # Use in your logic
    ...
```

---

## Tool Parameters

### Required Parameters
Parameters without defaults are required:
```python
async def my_tool(
    order_id: str,           # Required - no default
    include_history: bool,   # Required - no default
) -> Dict[str, Any]:
```

### Optional Parameters
Parameters with defaults are optional:
```python
async def my_tool(
    order_id: str,                    # Required
    include_history: bool = False,    # Optional
    limit: int = 10,                  # Optional
) -> Dict[str, Any]:
```

### Special Parameters
These are injected by the runtime:
```python
async def my_tool(
    *,
    chat_id: Optional[str] = None,         # Current chat session
    workflow_name: str = "MyWorkflow",     # Current workflow
    context_variables: dict = None,        # Shared state
) -> Dict[str, Any]:
```

---

## Error Handling

Return errors gracefully:

```python
async def my_tool(order_id: str) -> Dict[str, Any]:
    try:
        result = await fetch_order(order_id)
        return {"status": "success", "data": result}
    except OrderNotFoundError:
        return {
            "status": "error",
            "error": f"Order {order_id} not found",
        }
    except Exception as e:
        return {
            "status": "error",
            "error": str(e),
        }
```

---

## Next Steps

- [UI Components](04-ui-components.md) — Create the React components for UI tools
- [Testing](05-testing.md) — Verify everything works
