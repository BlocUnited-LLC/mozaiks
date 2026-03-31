---
name: create-workflow
description: Create a new AI workflow from scratch. Generates workflow YAML files, tools, and optional UI components.
argument-hint: "[WorkflowName] [description of what it should do]"
---

Help the user create a new workflow named $ARGUMENTS.

## Before Starting

Gather from the user:
1. What should this workflow do?
2. What agents are needed?
3. Should agents use any tools?
4. Does the workflow need any UI components?
5. Workflow name (PascalCase like `CustomerSupport`)

## Workflow Structure

Create under `platform/workflows/[WorkflowName]/`:

```
platform/workflows/[WorkflowName]/
├── orchestrator.yaml      # Workflow execution bootstrap
├── agents.yaml            # Agent roster and prompts
├── handoffs.yaml          # Agent-to-agent routing
├── context_variables.yaml # Shared workflow state
├── structured_outputs.yaml # Typed outputs (optional)
├── tools.yaml             # Tool bindings and UI tool metadata
├── ui_config.yaml         # Frontend exposure metadata
├── hooks.yaml             # Lifecycle hooks (optional)
├── tools/                 # Python tool implementations
│   └── my_tool.py
└── ui/                    # Workflow-specific UI (optional)
    ├── index.js
    └── MyComponent.js
```

**Event routing:** Events that trigger workflows are configured in `platform/automations/event_catalog.json`, not per-workflow files. The event transport relays domain events to the mozaiksai AI runtime.

## Steps

### 1. Copy an existing workflow as template
```powershell
Copy-Item -Recurse platform/workflows/GreenRoom platform/workflows/[WorkflowName]
```

### 2. Configure orchestrator.yaml
```yaml
workflow_name: [WorkflowName]  # Must match folder name
max_turns: 20
human_in_the_loop: true
workflow_startup_mode: AgentDriven
orchestration_pattern: DefaultPattern
initial_agent: [FirstAgentName]
initial_message: "[FirstAgentName]: [Initial greeting]"
```

### 3. Configure agents.yaml
Define agents with their prompts and capabilities.

### 4. Configure handoffs.yaml
Define routing rules between agents.

### 5. Add tool implementations
Create Python files under `platform/workflows/[WorkflowName]/tools/`. Function names must match `tools.yaml` entries.

### 6. Add UI components (if needed)
Create components under `ui/` and export from `ui/index.js`:
```js
import ResultCard from './ResultCard';
export default { ResultCard };
```

## Verification

1. Restart backend if needed
2. Check `http://localhost:8000/api/workflows` - workflow should appear
3. Test in frontend

## To make this the entry workflow

Set in `platform/config/ai.json`:
```json
{
  "workflows": {
    "entry_point": "[WorkflowName]"
  }
}
```

## Common Issues

- **Workflow not appearing**: Check `workflow_name` matches folder name exactly
- **Agent not responding**: Check `initial_agent` exists in `agents.yaml`
- **Tool not executing**: Check `tools.yaml` points to correct Python file/function
- **UI not rendering**: Check `ui/index.js` exports match `tools.yaml` component names
