# Instruction Prompt: Workflow Backend Configuration

**Task:** Configure the declarative files that define a workflow

**Complexity:** Low-Medium

---

## Context for AI Agent

You are helping a user configure a workflow bundle under
`platform/workflows/[WorkflowName]/`.

If they have not planned the workflow yet, redirect them to
`docs/instruction-prompts/adding-workflows/01-overview.md` first.

---

## Step 1: Gather Information

Ask the user:

1. What is the workflow called?
2. Which agents should exist?
3. Which agent starts?
4. What is the workflow-local startup behavior?

---

## Step 2: Create the Folder Structure

```powershell
Copy-Item -Recurse platform/workflows/GreenRoom platform/workflows/[WorkflowName]
```

Expected structure:

```text
platform/workflows/[WorkflowName]/
├── orchestrator.yaml
├── agents.yaml
├── handoffs.yaml
├── context_variables.yaml
├── structured_outputs.yaml
├── tools.yaml
├── ui_config.yaml
├── hooks.yaml
└── tools/
```

---

## Step 3: Configure `orchestrator.yaml`

```yaml
workflow_name: [WorkflowName]
max_turns: 20
human_in_the_loop: true
startup_mode: AgentDriven
orchestration_pattern: DefaultPattern
initial_agent: [FirstAgentName]
initial_message: "[FirstAgentName]: [Opening message]"
```

Notes:

- `workflow_name` must match the folder name exactly.
- `startup_mode` here is workflow-local and should not be confused with
  `platform/config/ai.json -> chat.startup_mode`.
- App-level workflow defaulting belongs in `platform/config/ai.json -> workflows.entry_point`.

---

## Step 4: Configure `agents.yaml`

Create one entry per agent with:

- `name`
- `prompt_sections`
- any routing or constraint sections needed for the workflow

Keep prompts focused on role, objective, constraints, and handoff expectations.

---

## Step 5: Configure `handoffs.yaml`

For multi-agent workflows, define clear handoff conditions between agents.

Best practices:

1. Keep conditions specific.
2. Add return paths where needed.
3. Define escalation routes explicitly.

---

## Step 6: Configure Shared Runtime Files

Use:

- `context_variables.yaml` for typed shared state
- `structured_outputs.yaml` for schemas or MFJ-triggered outputs
- `tools.yaml` for tool declarations
- `ui_config.yaml` for workflow frontend exposure metadata
- `hooks.yaml` for lifecycle behavior only when needed

---

## Step 7: Verify Configuration

Run checks such as:

```powershell
python -c "import yaml; yaml.safe_load(open('platform/workflows/[WorkflowName]/orchestrator.yaml'))"
python -c "import yaml; yaml.safe_load(open('platform/workflows/[WorkflowName]/agents.yaml'))"
```

Then verify:

- `workflow_name` matches the folder name
- `initial_agent` exists in `agents.yaml`
- handoff agent names exist
- the workflow appears at `http://localhost:8000/api/workflows`

---

## Summary Template

```markdown
## Workflow Backend Configuration Complete

### Files Created or Updated
- `platform/workflows/[WorkflowName]/orchestrator.yaml`
- `platform/workflows/[WorkflowName]/agents.yaml`
- `platform/workflows/[WorkflowName]/handoffs.yaml`
- `platform/workflows/[WorkflowName]/context_variables.yaml`

### Agents
1. [AgentName] — [Role]
2. [AgentName] — [Role]

### Next Steps
1. Add tools in `tools.yaml` and `tools/*.py`
2. Add workflow UI under `ui/` if the workflow uses UI tools
3. Set `platform/config/ai.json -> workflows.entry_point` if the app should default into this workflow
```

