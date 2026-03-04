# Backend Basics

This guide covers the core YAML configuration files that define your workflow's behavior.

---

## File Structure

Every workflow lives in `workflows/<YourWorkflow>/` and contains:

| File | Purpose |
|------|---------|
| `orchestrator.yaml` | How the conversation runs |
| `agents.yaml` | Agent definitions and prompts |
| `handoffs.yaml` | Agent-to-agent routing rules |
| `context_variables.yaml` | Shared state across agents |

---

!!! tip "New to Development?"

    **Let AI configure your backend!** Copy this prompt into Claude Code:

    ```
    I want to configure the backend for my Mozaiks workflow.

    Please read the instruction prompt at:
    docs/instruction-prompts/adding-workflows/02-backend-basics.md

    My workflow is called: [YourWorkflowName]
    I need these agents: [List your agents]
    ```

---

## Quick Start: Copy HelloWorld

```powershell
Copy-Item -Recurse workflows/HelloWorld workflows/MyWorkflow
```

Rename the folder to **PascalCase** (e.g., `SupportBot`, `OnboardingFlow`).

---

## orchestrator.yaml

This file controls the conversation flow.

```yaml
workflow_name: MyWorkflow          # must match folder name exactly
max_turns: 20                      # conversation length limit
human_in_the_loop: true            # user participates in conversation
startup_mode: AgentDriven          # AgentDriven | UserDriven | BackendOnly
initial_agent: MyFirstAgent        # which agent speaks first
initial_message: "MyFirstAgent: start the workflow."
```

### Key Fields Explained

| Field | Description | Options |
|-------|-------------|---------|
| `workflow_name` | Must match folder name | PascalCase string |
| `max_turns` | Prevents runaway conversations | 10-50 typical |
| `startup_mode` | Who initiates? | `AgentDriven`, `UserDriven`, `BackendOnly` |
| `initial_agent` | First agent to speak | Agent name from agents.yaml |

---

## agents.yaml

Defines your AI personalities.

### Minimal Example

```yaml
agents:
  - name: MyFirstAgent
    prompt_sections:
      - id: role
        heading: "[ROLE]"
        content: "You are a helpful assistant."
      - id: objective
        heading: "[OBJECTIVE]"
        content: "Help the user accomplish their goal."
    max_consecutive_auto_reply: 10
    auto_tool_mode: false
    structured_outputs_required: false
```

### Multi-Agent Example

```yaml
agents:
  - name: GreetingAgent
    prompt_sections:
      - id: role
        heading: "[ROLE]"
        content: "You greet users and understand their intent."
      - id: routing
        heading: "[ROUTING]"
        content: |
          Route users to:
          - OrderAgent for order questions
          - TechnicalAgent for product questions
    max_consecutive_auto_reply: 5

  - name: OrderAgent
    prompt_sections:
      - id: role
        heading: "[ROLE]"
        content: "You handle order-related inquiries."
      - id: capabilities
        heading: "[CAPABILITIES]"
        content: "You can look up orders, track shipments, and process returns."
    max_consecutive_auto_reply: 10
```

### Agent Fields

| Field | Description | Default |
|-------|-------------|---------|
| `name` | Unique identifier | Required |
| `prompt_sections` | System prompt broken into sections | Required |
| `max_consecutive_auto_reply` | Auto-responses before pause | 10 |
| `auto_tool_mode` | Automatically call tools | false |
| `structured_outputs_required` | Enforce output schema | false |

---

## handoffs.yaml

Controls agent-to-agent routing.

```yaml
handoffs:
  - from_agent: GreetingAgent
    to_agent: OrderAgent
    condition: "user asks about orders, shipping, or returns"

  - from_agent: GreetingAgent
    to_agent: TechnicalAgent
    condition: "user asks about product features or bugs"

  - from_agent: OrderAgent
    to_agent: GreetingAgent
    condition: "order issue is resolved"

  - from_agent: OrderAgent
    to_agent: EscalationAgent
    condition: "refund requested over $100"
```

### Handoff Fields

| Field | Description |
|-------|-------------|
| `from_agent` | Source agent name |
| `to_agent` | Destination agent name |
| `condition` | Natural language trigger |

---

## context_variables.yaml

Shared state accessible to all agents.

```yaml
context_variables:
  - name: user_name
    type: string
    default: null
    description: "Customer's name once identified"

  - name: order_id
    type: string
    default: null
    description: "Active order being discussed"

  - name: escalation_reason
    type: string
    default: null
    description: "Why the conversation was escalated"
```

Context variables persist across the conversation and can be read/written by tools.

---

## Verification

After creating your files:

1. Start the server
2. Check [http://localhost:8000/api/workflows](http://localhost:8000/api/workflows)
3. Your workflow should appear in the list

If missing, check:
- `workflow_name` matches folder name exactly
- All YAML files are valid (no syntax errors)
- Server logs for loading errors

---

## Next Steps

- [Tools](03-tools.md) — Add capabilities to your agents
- [UI Components](04-ui-components.md) — Create interactive elements
