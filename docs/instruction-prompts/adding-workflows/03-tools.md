# Instruction Prompt: Adding Tools to a Workflow

**Task:** Create Python tool implementations and configure tools.yaml

**Complexity:** Medium (Python code + YAML configuration)

---

## Context for AI Agent

You are helping a user add tools to their MozaiksAI workflow. Tools are Python functions that agents can call to take actions like fetching data, calling APIs, or showing interactive UI components.

---

## Step 1: Understand Tool Requirements

Ask the user:

1. **"What actions should your agents be able to perform?"**
   Examples: look up orders, send emails, show forms, query databases

2. **"For each action, what information is needed?"**
   Examples: order_id, customer_email, date range

3. **"Should any actions show interactive UI?"**
   If yes, those need to be UI_Tool type

---

## Step 2: Categorize Tools

Help the user categorize their tools:

### Standard Tools (no UI)
- Data lookups: `get_order_status`, `get_customer_info`
- Actions: `send_email`, `create_ticket`, `update_record`
- Calculations: `calculate_shipping`, `check_availability`

### UI Tools (show interactive components)
- Input collection: `show_form`, `show_calendar`
- Confirmations: `show_confirmation_card`
- Selection: `show_options`, `show_product_picker`

---

## Step 3: Create tools.yaml Entries

For each tool, add an entry:

### Standard Tool Template
```yaml
tools:
  - agent: [AgentName]
    file: [tool_name].py
    function: [tool_name]
    description: "[What the tool does - be specific, this helps the LLM decide when to use it]"
    tool_type: Standard
    auto_invoke: false
```

### UI Tool Template
```yaml
tools:
  - agent: [AgentName]
    file: [tool_name].py
    function: [tool_name]
    description: "[What the tool does]"
    tool_type: UI_Tool
    auto_invoke: true
    ui:
      component: [ReactComponentName]  # Must match export in components/index.js
      mode: inline                      # inline or artifact
```

### Example: Customer Support Tools
```yaml
tools:
  # Standard tools
  - agent: OrderAgent
    file: get_order_status.py
    function: get_order_status
    description: "Look up the current status of an order by order ID. Returns shipping status, tracking number, and estimated delivery."
    tool_type: Standard
    auto_invoke: false

  - agent: OrderAgent
    file: initiate_return.py
    function: initiate_return
    description: "Start a return process for an order. Requires order ID and reason."
    tool_type: Standard
    auto_invoke: false

  # UI tool
  - agent: OrderAgent
    file: show_return_form.py
    function: show_return_form
    description: "Display a form for the customer to fill out return details."
    tool_type: UI_Tool
    auto_invoke: true
    ui:
      component: ReturnForm
      mode: inline
```

---

## Step 4: Create Python Implementations

### Standard Tool Template

```python
# workflows/[WorkflowName]/tools/[tool_name].py
from typing import Any, Dict

async def [tool_name](
    [required_param]: [type],
    [optional_param]: [type] = [default],
) -> Dict[str, Any]:
    """[Description of what the tool does].

    Args:
        [required_param]: [Description]
        [optional_param]: [Description]
    """
    try:
        # Your implementation here
        result = await your_logic([required_param], [optional_param])

        return {
            "status": "success",
            "data": result,
        }
    except Exception as e:
        return {
            "status": "error",
            "error": str(e),
        }
```

### UI Tool Template

```python
# workflows/[WorkflowName]/tools/[tool_name].py
from typing import Any, Dict, Optional
from mozaiksai.core.workflow.outputs.ui_tools import use_ui_tool

async def [tool_name](
    *,
    [param]: [type] = [default],
    chat_id: Optional[str] = None,
    workflow_name: str = "[WorkflowName]",
) -> Dict[str, Any]:
    """[Description].

    Args:
        [param]: [Description]
    """
    # Call use_ui_tool to display component and wait for response
    response = await use_ui_tool(
        tool_id="[ReactComponentName]",      # Must match component export
        payload={
            # Data to pass to the React component
            "[key]": [param],
            "message": "[Instructions for user]",
        },
        chat_id=chat_id,
        workflow_name=workflow_name,
    )

    # Process the user's response
    user_data = response.get("data", {})

    return {
        "status": "success",
        "data": user_data,
    }
```

---

## Step 5: Implementation Examples

### Example 1: Order Lookup Tool

```python
# workflows/SupportBot/tools/get_order_status.py
from typing import Any, Dict

async def get_order_status(
    order_id: str,
) -> Dict[str, Any]:
    """Look up the status of a customer order.

    Args:
        order_id: The order ID to look up (e.g., "ORD-12345")
    """
    # In production, query your database
    # This is a mock implementation
    mock_orders = {
        "ORD-12345": {
            "status": "shipped",
            "tracking": "1Z999AA10123456784",
            "carrier": "UPS",
            "estimated_delivery": "2024-03-15",
        },
        "ORD-67890": {
            "status": "processing",
            "tracking": None,
            "carrier": None,
            "estimated_delivery": "2024-03-18",
        },
    }

    if order_id in mock_orders:
        return {
            "status": "success",
            "data": mock_orders[order_id],
        }
    else:
        return {
            "status": "error",
            "error": f"Order {order_id} not found",
        }
```

### Example 2: Date Picker UI Tool

```python
# workflows/BookingBot/tools/show_date_picker.py
from typing import Any, Dict, List, Optional
from mozaiksai.core.workflow.outputs.ui_tools import use_ui_tool

async def show_date_picker(
    *,
    available_dates: List[str] = None,
    message: str = "Please select a date:",
    chat_id: Optional[str] = None,
    workflow_name: str = "BookingBot",
) -> Dict[str, Any]:
    """Display a date picker for the user to select an appointment date.

    Args:
        available_dates: List of available date strings (YYYY-MM-DD)
        message: Instructions to show the user
    """
    response = await use_ui_tool(
        tool_id="DatePicker",
        payload={
            "available_dates": available_dates or [],
            "message": message,
            "allow_past_dates": False,
        },
        chat_id=chat_id,
        workflow_name=workflow_name,
    )

    selected_date = response.get("data", {}).get("selected_date")

    if selected_date:
        return {
            "status": "success",
            "selected_date": selected_date,
        }
    else:
        return {
            "status": "cancelled",
            "message": "User cancelled date selection",
        }
```

### Example 3: Form UI Tool

```python
# workflows/SupportBot/tools/show_return_form.py
from typing import Any, Dict, Optional
from mozaiksai.core.workflow.outputs.ui_tools import use_ui_tool

async def show_return_form(
    *,
    order_id: str,
    items: list = None,
    chat_id: Optional[str] = None,
    workflow_name: str = "SupportBot",
) -> Dict[str, Any]:
    """Display a return request form.

    Args:
        order_id: The order being returned
        items: List of items eligible for return
    """
    response = await use_ui_tool(
        tool_id="ReturnForm",
        payload={
            "order_id": order_id,
            "items": items or [],
            "return_reasons": [
                "Damaged in shipping",
                "Wrong item received",
                "Changed my mind",
                "Item not as described",
                "Other",
            ],
        },
        chat_id=chat_id,
        workflow_name=workflow_name,
    )

    return {
        "status": "success",
        "return_request": response.get("data"),
    }
```

---

## Step 6: Accessing Context Variables

When tools need shared state:

```python
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
