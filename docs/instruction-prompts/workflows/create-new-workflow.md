# Instruction Prompt: Create a New Workflow

!!! tip "New to Development?"

    Copy this into Claude Code, Cursor, or Copilot:

    ```
    I want to create a new Mozaiks workflow from scratch.

    Please read the instruction prompt at:
    docs/instruction-prompts/workflows/create-new-workflow.md

    My workflow should:
    - Be called: [YourWorkflowName]
    - Do this: [Describe what it should do in plain English]
    ```

---

**Task:** Help the user create a new workflow from scratch

**Complexity:** Medium-High (workflow YAML + Python tools + workflow UI)

**Time:** 15-30 minutes depending on complexity

---

## Context for AI Agent

You are helping a user create a new workflow for MozaiksAI.

In the current repo, a workflow is a declarative runtime input under
`platform/workflows/[WorkflowName]/`.

Do not instruct the user to build workflow UI under `chat-ui/src/workflows/`.
Workflow UI components now live with the workflow bundle and are auto-discovered
from `platform/workflows/*/ui/index.js`.

### What a Workflow Consists Of

**Workflow bundle files:**
- `orchestrator.yaml` — workflow execution bootstrap
- `agents.yaml` — agent roster and prompts
- `handoffs.yaml` — agent-to-agent routing rules
- `context_variables.yaml` — shared workflow state
- `structured_outputs.yaml` — typed outputs used by validation and MFJ
- `tools.yaml` — declared tool bindings and UI tool metadata
- `ui_config.yaml` — frontend exposure metadata
- `hooks.yaml` — lifecycle hooks
- `tools/*.py` — tool implementations
- `ui/*.js` or `ui/*.jsx` — workflow-specific UI components
- `ui/index.js` — component export surface used by auto-discovery

### File Structure

```text
platform/
└── workflows/
    └── MyWorkflow/
        ├── orchestrator.yaml
        ├── agents.yaml
        ├── handoffs.yaml
        ├── context_variables.yaml
        ├── structured_outputs.yaml
        ├── tools.yaml
        ├── ui_config.yaml
        ├── hooks.yaml
        ├── tools/
        │   └── my_tool.py
        └── ui/
            ├── index.js
            └── MyComponent.js
```

---

## Step 1: Understand What the User Wants

Before creating anything, gather these details:

1. What should this workflow do?
2. What agents are needed?
3. Should agents use any tools?
4. Does the workflow need any inline or artifact UI?
5. What should the workflow be called?

Use PascalCase names such as `CustomerSupport`, `DocumentAnalyzer`, or
`BookingAssistant`.

---

## Step 2: Create the Workflow Bundle

### 2.1 Copy a live workflow as a starting point

```powershell
Copy-Item -Recurse platform/workflows/GreenRoom platform/workflows/[WorkflowName]
```

### 2.2 Configure `orchestrator.yaml`

```yaml
workflow_name: [WorkflowName]
max_turns: 20
human_in_the_loop: true
startup_mode: AgentDriven
orchestration_pattern: DefaultPattern
initial_agent: [FirstAgentName]
initial_message: "[FirstAgentName]: [Initial greeting or action]"
```

Notes:
- `workflow_name` must match the folder name exactly.
- `startup_mode` here is workflow-local execution behavior.
- App-level boot defaults like the entry workflow belong in
  `platform/config/ai.json`, not `platform/app.json`.

### 2.3 Configure the remaining workflow files

- `agents.yaml` for the agent roster and prompts
- `handoffs.yaml` for routing between agents
- `context_variables.yaml` for shared state
- `structured_outputs.yaml` for typed outputs when needed
- `tools.yaml` for declared tools and UI tool metadata
- `ui_config.yaml` for workflow frontend exposure metadata
- `hooks.yaml` for lifecycle hooks if the workflow needs them

### 2.4 Add tool implementations

Create Python implementations under:

```text
platform/workflows/[WorkflowName]/tools/
```

Tool function names must match the `function` entries in `tools.yaml`, and the
return payload must remain JSON-serializable.

---

## Step 3: Add Workflow UI When Needed

If the workflow uses UI tools, create the workflow UI under:

```text
platform/workflows/[WorkflowName]/ui/
```

### 3.1 Create a component

```jsx
import React from 'react';

export default function ResultCard({ payload, onResponse, onCancel, eventId, ui_tool_id }) {
  async function handleConfirm() {
    await onResponse({
      status: 'success',
      data: { confirmed: true },
      eventId,
      ui_tool_id,
    });
  }

  return (
    <div className="rounded-xl border p-4 bg-white shadow-sm">
      <h3 className="text-lg font-semibold mb-2">{payload?.title ?? 'Result'}</h3>
      <p className="text-gray-600 mb-4">{payload?.message ?? 'No data available'}</p>
      <div className="flex gap-2">
        <button onClick={handleConfirm} className="px-4 py-2 rounded bg-cyan-600 text-white">
          Confirm
        </button>
        <button onClick={onCancel} className="px-4 py-2 rounded border">
          Cancel
        </button>
      </div>
    </div>
  );
}
```

### 3.2 Export components from `ui/index.js`

```js
import ResultCard from './ResultCard';

export default {
  ResultCard,
};
```

Do not manually edit `chat-ui/src/@chat-workflows/index.js`.
The shared shell auto-discovers workflow UI from `platform/workflows/*/ui/index.js`.

---

## Step 4: Test the Workflow

1. Restart the backend if needed.
2. Check `http://localhost:8000/api/workflows` and verify the workflow appears.
3. Open the frontend and select the workflow.
4. Verify agent routing, tool execution, and any workflow UI components.

If the app should default into this workflow, set:

```json
{
  "workflows": {
    "entry_point": "[WorkflowName]"
  }
}
```

in `platform/config/ai.json`.

---

## Step 5: Debug Common Issues

### Workflow not appearing in `/api/workflows`

- Check that `workflow_name` matches the folder name exactly.
- Check for YAML syntax errors.
- Check server logs for workflow loading errors.

### Agent not responding

- Check that `initial_agent` exists in `agents.yaml`.
- Check that the agent has prompt sections with real content.

### Tool not executing

- Check that `tools.yaml` points to the right Python file and function.
- Check that the implementation exists under `platform/workflows/[WorkflowName]/tools/`.

### UI component not rendering

- Check that `tools.yaml` `ui.component` matches the exported key in `ui/index.js`.
- Check that `platform/workflows/[WorkflowName]/ui/index.js` exists.
- Check the browser console for render errors.

---

## Summary Checklist

- [ ] Workflow created under `platform/workflows/[WorkflowName]/`
- [ ] `orchestrator.yaml` has the correct `workflow_name`
- [ ] agents, handoffs, tools, and context files are aligned
- [ ] Python tools exist under `tools/`
- [ ] workflow UI exists under `ui/` when needed
- [ ] `ui/index.js` exports any declared UI tool components
- [ ] workflow appears in `/api/workflows`
- [ ] optional app entry workflow is configured in `platform/config/ai.json`

