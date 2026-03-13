# Instruction Prompt: Testing a Workflow

!!! tip "New to Development?"

    Copy this into Claude Code, Cursor, or Copilot:

    ```
    I want to test my Mozaiks workflow.

    Please read the instruction prompt at:
    docs/instruction-prompts/adding-workflows/05-testing.md

    My workflow is: [WorkflowName]
    ```

---

**Task:** Verify a workflow is configured correctly and works end to end

**Complexity:** Low

---

## Context for AI Agent

You are helping a user test a workflow that lives under
`platform/workflows/[WorkflowName]/`.

Workflow UI components, if any, live under
`platform/workflows/[WorkflowName]/ui/` and are auto-discovered by the shared
shell.

---

## Step 1: Validate File Structure

```powershell
Get-ChildItem -Path "platform/workflows/[WorkflowName]" -Recurse
```

Expected core files:

- `orchestrator.yaml`
- `agents.yaml`
- `handoffs.yaml`
- `context_variables.yaml`
- `structured_outputs.yaml`
- `tools.yaml`
- `ui_config.yaml`
- `hooks.yaml`
- `tools/*.py`

---

## Step 2: Validate Workflow Config

Check:

```powershell
Get-Content "platform/workflows/[WorkflowName]/orchestrator.yaml"
Get-Content "platform/workflows/[WorkflowName]/agents.yaml"
Get-Content "platform/workflows/[WorkflowName]/handoffs.yaml"
Get-Content "platform/workflows/[WorkflowName]/tools.yaml"
```

Verify:

- `workflow_name` matches the folder name exactly
- `initial_agent` exists in `agents.yaml`
- handoff agent names exist
- tool declarations point to real Python files and functions

Optional syntax check:

```powershell
python -m py_compile "platform/workflows/[WorkflowName]/tools/[tool_name].py"
```

---

## Step 3: Validate Workflow UI

If the workflow uses UI tools:

```powershell
Get-ChildItem "platform/workflows/[WorkflowName]/ui/"
Get-Content "platform/workflows/[WorkflowName]/ui/index.js"
Get-Content "chat-ui/src/@chat-workflows/index.js"
```

Verify:

- `ui/index.js` exists
- every UI tool component is exported
- the shared workflow registry is using auto-discovery, not manual registration

---

## Step 4: Test Workflow Loading

```powershell
Invoke-RestMethod -Uri "http://localhost:8000/api/workflows"
```

Expected result: the workflow appears in the list.

If the app should default into it, verify `platform/config/ai.json` contains the
correct `workflows.entry_point` value.

---

## Step 5: Test Conversation Flow

1. Start or restart the backend.
2. Open the frontend.
3. Select the workflow or boot through the configured entry workflow.
4. Send a message that exercises the main path.
5. Verify agent replies, handoffs, and tool execution.

For UI tools:

1. Trigger the tool.
2. Verify the component from `platform/workflows/[WorkflowName]/ui/` renders.
3. Submit or cancel it.
4. Verify the workflow receives the response.

---

## Step 6: Report Results

Summarize:

- whether the workflow loaded
- whether agents responded correctly
- whether tools executed correctly
- whether workflow UI rendered correctly
- any file-level issues that still need fixing

