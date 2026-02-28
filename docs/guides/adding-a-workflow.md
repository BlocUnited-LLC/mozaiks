# Adding a Workflow

A workflow has two parts that must match: a **backend config** (YAML files) and a **frontend component** (React). The `HelloWorld` workflow in the repo is a complete, working example — copy it as your starting point.

---

## Backend — `workflows/<YourWorkflow>/`

### 1. Copy the example

```powershell
Copy-Item -Recurse workflows/HelloWorld workflows/MyWorkflow
```

Rename the folder to your workflow name in **PascalCase** (e.g. `SupportBot`, `OnboardingFlow`).

### 2. File reference

| File | What to edit |
|------|-------------|
| `orchestrator.yaml` | `workflow_name`, `max_turns`, `startup_mode`, `initial_message`, `initial_agent` |
| `agents.yaml` | Agent names, system prompts, `max_consecutive_auto_reply` |
| `handoffs.yaml` | Which agent hands off to which, and under what condition |
| `tools.yaml` | Tools each agent can call; whether they render a UI component |
| `context_variables.yaml` | Shared state across agents (DB-backed or computed) |
| `structured_outputs.yaml` | Pydantic-style output schemas per agent |
| `hooks.yaml` | Lifecycle hooks (`update_agent_state`, `process_message_before_send`) |
| `tools/<fn>.py` | Python tool implementations |

### 3. `orchestrator.yaml` — key fields

```yaml
workflow_name: MyWorkflow          # must match folder name exactly
max_turns: 20
human_in_the_loop: true
startup_mode: AgentDriven          # AgentDriven | UserDriven | BackendOnly
initial_agent: MyFirstAgent
initial_message: "MyFirstAgent: start the workflow."
```

### 4. `agents.yaml` — minimal example

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

### 5. `tools.yaml` — tool that renders a UI component

```yaml
tools:
  - agent: MyFirstAgent
    file: my_tool.py
    function: my_tool
    description: "Does something and renders a card."
    tool_type: UI_Tool
    auto_invoke: true
    ui:
      component: MyCard     # must match the key in your frontend components/index.js
      mode: inline          # inline | artifact
```

### 6. `tools/my_tool.py`

```python
async def my_tool(name: str) -> str:
    """Does something useful."""
    return f"Hello, {name}!"
```

The runtime auto-discovers this file from `tools.yaml` — no manual registration needed.

---

## Auto-discovery

The server scans `workflows/` at startup. Any folder containing `orchestrator.yaml` is loaded automatically. No registration, no restarts needed during development (if running with `watchdog`).

Check [http://localhost:8000/api/workflows](http://localhost:8000/api/workflows) after starting the server to confirm your workflow loaded.

---

## Frontend — `chat-ui/src/workflows/<YourWorkflow>/`

### 1. Copy the example

```powershell
Copy-Item -Recurse chat-ui/src/workflows/HelloWorld chat-ui/src/workflows/MyWorkflow
```

### 2. Edit `components/MyCard.js`

```jsx
import React from 'react';

export default function MyCard({ data }) {
  return (
    <div className="rounded-xl border p-4">
      <p>{data?.message ?? 'No message yet.'}</p>
    </div>
  );
}
```

The component receives `data` — the return value of the matching tool function.

### 3. Export from `components/index.js`

```js
import MyCard from './MyCard';

const MyWorkflowComponents = {
  MyCard,           // key must match `ui.component` in tools.yaml
};

export default MyWorkflowComponents;
```

### 4. Register in `chat-ui/src/workflows/index.js`

```js
import HelloWorldComponents from './HelloWorld/components';
import MyWorkflowComponents from './MyWorkflow/components';   // ← add import

const WORKFLOW_REGISTRY = {
  HelloWorld: { components: HelloWorldComponents },
  MyWorkflow: { components: MyWorkflowComponents },           // ← add entry
};
```

That's it — the ChatUI runtime resolves the component by name when the tool fires.

---

## WebSocket session URL

Once a chat is started via the API, the frontend connects to:

```
ws://localhost:8000/ws/{workflow_name}/{app_id}/{chat_id}/{user_id}
```

The frontend handles this automatically — no manual wiring needed.

---

## Checklist

- [ ] `workflows/MyWorkflow/orchestrator.yaml` exists and `workflow_name` matches folder
- [ ] `/api/workflows` shows your workflow after server restart
- [ ] `tools.yaml` `ui.component` matches the key in `components/index.js`
- [ ] Workflow registered in `chat-ui/src/workflows/index.js`
