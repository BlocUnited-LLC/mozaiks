# Instruction Prompt: Create a New Workflow

**Task:** Help the user create a new workflow from scratch

**Complexity:** Medium-High (backend YAML + frontend React)

**Time:** 15-30 minutes depending on complexity

---

## Context for AI Agent

You are helping a user create a new workflow for MozaiksAI. A workflow is a multi-agent conversation system - think of it as defining what AI agents exist, what they can do, and how they work together.

### What a Workflow Consists Of

**Backend (Python/YAML):**
- `orchestrator.yaml` — How the workflow runs (turn limits, startup mode)
- `agents.yaml` — The AI agents (their names, personalities, behaviors)
- `handoffs.yaml` — When one agent passes to another
- `tools.yaml` — Functions agents can call
- `tools/*.py` — Python implementation of those functions

**Frontend (React):**
- `components/*.js` — UI components that tools can render
- `components/index.js` — Registry of available components

### File Structure

```
workflows/
└── MyWorkflow/                    # Backend
    ├── orchestrator.yaml
    ├── agents.yaml
    ├── handoffs.yaml
    ├── tools.yaml
    ├── context_variables.yaml
    ├── structured_outputs.yaml
    ├── hooks.yaml
    └── tools/
        └── my_tool.py

chat-ui/src/workflows/
└── MyWorkflow/                    # Frontend
    └── components/
        ├── index.js
        └── MyComponent.js
```

---

## Step 1: Understand What the User Wants

Before creating anything, gather these details:

**Ask the user:**

1. **"What should this workflow do?"**
   - Example: "Help customers book appointments"
   - Example: "Analyze documents and answer questions"

2. **"What agents do you need?"**
   - Example: "A coordinator that routes requests, and a specialist that handles bookings"
   - Example: "Just one agent that answers questions"

3. **"Should agents use any tools?"**
   - Example: "Yes, a tool to check calendar availability"
   - Example: "No, just conversation"

4. **"Do you need any UI components?"**
   - Example: "Yes, a card that shows available time slots"
   - Example: "No, just chat"

5. **"What should the workflow be called?"**
   - Use PascalCase: `CustomerSupport`, `DocumentAnalyzer`, `BookingAssistant`

---

## Step 2: Create Backend from Template

### 2.1: Copy HelloWorld as starting point

```bash
# Windows PowerShell
Copy-Item -Recurse workflows/HelloWorld workflows/[WorkflowName]

# macOS/Linux
cp -r workflows/HelloWorld workflows/[WorkflowName]
```

### 2.2: Edit orchestrator.yaml

```yaml
workflow_name: [WorkflowName]      # MUST match folder name exactly
max_turns: 20                       # Conversation turn limit
human_in_the_loop: true            # Allow user messages
startup_mode: AgentDriven          # AgentDriven | UserDriven | BackendOnly
initial_agent: [FirstAgentName]    # Which agent starts
initial_message: "[FirstAgentName]: [Initial greeting or action]"
```

**Startup modes explained:**
- `AgentDriven` — Agent speaks first, then user responds
- `UserDriven` — User speaks first, agent responds
- `BackendOnly` — No human messages (automation)

### 2.3: Edit agents.yaml

Define each agent:

```yaml
agents:
  - name: [AgentName]
    prompt_sections:
      - id: role
        heading: "[ROLE]"
        content: |
          You are [describe the agent's role].

      - id: objective
        heading: "[OBJECTIVE]"
        content: |
          Your goal is to [describe what the agent should accomplish].

      - id: guidelines
        heading: "[GUIDELINES]"
        content: |
          - [Guideline 1]
          - [Guideline 2]
          - [Guideline 3]

    max_consecutive_auto_reply: 10    # Max replies before forcing user input
    auto_tool_mode: false             # Auto-call tools without confirmation
    structured_outputs_required: false
```

**For multiple agents, add more entries to the list.**

### 2.4: Edit handoffs.yaml (if multiple agents)

Define when one agent hands off to another:

```yaml
handoffs:
  - from: Coordinator
    to: Specialist
    condition: "when user asks about [specific topic]"

  - from: Specialist
    to: Coordinator
    condition: "when task is complete or user has new request"
```

For single-agent workflows, this can be empty or omitted.

### 2.5: Edit tools.yaml (if agents need tools)

```yaml
tools:
  - agent: [AgentName]
    file: my_tool.py
    function: my_tool
    description: "What this tool does (visible to the agent)"
    tool_type: Standard              # Standard | UI_Tool
    auto_invoke: true
```

For UI tools that render components:

```yaml
tools:
  - agent: [AgentName]
    file: show_results.py
    function: show_results
    description: "Display results in a card"
    tool_type: UI_Tool
    auto_invoke: true
    ui:
      component: ResultCard          # Must match frontend component name
      mode: inline                   # inline | artifact
```

### 2.6: Create tool implementation (if needed)

Create `tools/[tool_name].py`:

```python
async def my_tool(param1: str, param2: int = 10) -> str:
    """
    Brief description of what this tool does.

    Args:
        param1: What this parameter is for
        param2: What this parameter is for (default: 10)

    Returns:
        What the tool returns
    """
    # Your implementation here
    result = f"Processed {param1} with value {param2}"
    return result
```

**Important:**
- Function name must match `function` in tools.yaml
- Use type hints for parameters
- Return a string or JSON-serializable dict
- The docstring becomes the tool description visible to agents

---

## Step 3: Create Frontend (if using UI tools)

### 3.1: Create component directory

```bash
# Windows PowerShell
New-Item -ItemType Directory -Path "chat-ui/src/workflows/[WorkflowName]/components" -Force

# macOS/Linux
mkdir -p chat-ui/src/workflows/[WorkflowName]/components
```

### 3.2: Create the component

Create `chat-ui/src/workflows/[WorkflowName]/components/[ComponentName].js`:

```jsx
import React from 'react';

export default function [ComponentName]({ data }) {
  // `data` is the return value from your tool function

  return (
    <div className="rounded-xl border border-gray-200 p-4 bg-white shadow-sm">
      <h3 className="text-lg font-semibold mb-2">
        {data?.title ?? 'Result'}
      </h3>
      <p className="text-gray-600">
        {data?.message ?? 'No data available'}
      </p>
    </div>
  );
}
```

### 3.3: Create component index

Create `chat-ui/src/workflows/[WorkflowName]/components/index.js`:

```javascript
import [ComponentName] from './[ComponentName]';

const [WorkflowName]Components = {
  [ComponentName],    // Key must match `ui.component` in tools.yaml
};

export default [WorkflowName]Components;
```

### 3.4: Register in workflow registry

Edit `chat-ui/src/workflows/index.js`:

```javascript
import HelloWorldComponents from './HelloWorld/components';
import [WorkflowName]Components from './[WorkflowName]/components';  // Add this

const WORKFLOW_REGISTRY = {
  HelloWorld: { components: HelloWorldComponents },
  [WorkflowName]: { components: [WorkflowName]Components },          // Add this
};

export default WORKFLOW_REGISTRY;
```

---

## Step 4: Test the Workflow

### 4.1: Restart the backend

```bash
# Stop current server (Ctrl+C) and restart
python run_server.py
```

### 4.2: Verify workflow loaded

```bash
curl http://localhost:8000/api/workflows
```

Your workflow should appear in the list.

### 4.3: Test in browser

1. Open http://localhost:5173
2. Select your workflow from the workflow picker
3. Start a conversation
4. Verify agents respond correctly
5. If using tools, verify they execute
6. If using UI components, verify they render

---

## Step 5: Debug Common Issues

### "Workflow not appearing in /api/workflows"

**Cause:** `workflow_name` doesn't match folder name, or YAML syntax error.

**Fix:**
1. Check `orchestrator.yaml` has `workflow_name: [ExactFolderName]`
2. Validate YAML syntax: https://yamlchecker.com
3. Check server logs for errors

### "Agent not responding"

**Cause:** Agent configuration issue.

**Fix:**
1. Check `initial_agent` matches an agent name in `agents.yaml`
2. Check agent has `prompt_sections` with content
3. Check `max_consecutive_auto_reply` isn't 0

### "Tool not executing"

**Cause:** Tool not registered or import error.

**Fix:**
1. Check `tools.yaml` has correct `file` and `function`
2. Check Python file exists in `tools/` folder
3. Check for Python syntax errors: `python -c "import workflows.[WorkflowName].tools.[tool_file]"`
4. Check server logs for import errors

### "UI component not rendering"

**Cause:** Component not registered or name mismatch.

**Fix:**
1. Check `ui.component` in tools.yaml matches key in `components/index.js`
2. Check workflow is registered in `chat-ui/src/workflows/index.js`
3. Check browser console for React errors (F12 → Console)

---

## Example: Complete Simple Workflow

### Goal: A customer support bot with one agent

**orchestrator.yaml:**
```yaml
workflow_name: CustomerSupport
max_turns: 30
human_in_the_loop: true
startup_mode: AgentDriven
initial_agent: SupportAgent
initial_message: "SupportAgent: Hello! I'm here to help. How can I assist you today?"
```

**agents.yaml:**
```yaml
agents:
  - name: SupportAgent
    prompt_sections:
      - id: role
        heading: "[ROLE]"
        content: |
          You are a friendly customer support agent for a software company.

      - id: objective
        heading: "[OBJECTIVE]"
        content: |
          Help customers with questions, troubleshooting, and account issues.
          Be patient, empathetic, and solution-oriented.

      - id: guidelines
        heading: "[GUIDELINES]"
        content: |
          - Always greet the customer warmly
          - Ask clarifying questions when needed
          - Provide step-by-step instructions
          - If you can't help, offer to escalate

    max_consecutive_auto_reply: 10
    auto_tool_mode: false
    structured_outputs_required: false
```

No tools, no UI components, no handoffs needed for this simple example.

---

## Summary Checklist

- [ ] Backend folder created: `workflows/[WorkflowName]/`
- [ ] `orchestrator.yaml` has correct `workflow_name`
- [ ] `agents.yaml` defines at least one agent
- [ ] `handoffs.yaml` configured (if multiple agents)
- [ ] `tools.yaml` configured (if using tools)
- [ ] Tool Python files created (if using tools)
- [ ] Frontend components created (if using UI tools)
- [ ] Components registered in `index.js`
- [ ] Workflow registered in `chat-ui/src/workflows/index.js`
- [ ] Backend restarted
- [ ] Workflow appears in `/api/workflows`
- [ ] Conversation works in browser
