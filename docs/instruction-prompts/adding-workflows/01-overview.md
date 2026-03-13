# Instruction Prompt: Workflow Planning & Overview

**Task:** Help the user understand what workflow they need and plan the shape

**Complexity:** Low

**Time:** 5-10 minutes

---

## Context for AI Agent

You are helping a user plan a new workflow for MozaiksAI before writing any
files.

Current repo targets:

- workflow files live under `platform/workflows/[WorkflowName]/`
- workflow UI components live under `platform/workflows/[WorkflowName]/ui/`
- workflow boot defaults at the app level live in `platform/config/ai.json`
- workflow-local execution startup lives in `orchestrator.yaml`

This is a planning conversation, not implementation yet.

---

## Step 1: Understand the Goal

Ask the user:

"What should this workflow do? Describe it like you're explaining it to a teammate."

Listen for:

- the main outcome
- who uses it
- what data it needs
- whether it is mostly conversational, mostly deterministic, or mixed

---

## Step 2: Identify Agents

Suggest a shape based on complexity:

**Single-agent workflows**
- good for straightforward Q&A, intake, or guided support

**Multi-agent workflows**
- coordinator/router for intent routing
- specialists for domain work
- optional escalation or review agent

Ask the user whether that split feels right.

---

## Step 3: Identify Tools and UI

Ask:

- What actions should the agents be able to take?
- What data lookups or mutations are required?
- Does the workflow need inline or artifact UI?

Classify likely tools as:

- standard tools for lookups or actions
- UI tools for forms, selection cards, confirmations, or artifact renderers

---

## Step 4: Plan Handoffs

If the workflow uses multiple agents, define when one agent should hand off to
another and when control should return.

Capture:

- who routes first
- who handles specialist work
- what conditions trigger escalation
- what conditions end the flow

---

## Step 5: Confirm the Plan

Summarize the workflow as:

```markdown
## Workflow: [Name]

### Purpose
[One sentence]

### Agents
1. [AgentName] — [Role]
2. [AgentName] — [Role]

### Tools
- [tool_name] — [What it does] — Used by: [Agent]

### Handoffs
- [From] -> [To]: [Condition]

### Workflow UI
- [ComponentName] — [What it shows or collects]
```

Ask whether anything should change before implementation starts.

---

## Step 6: Recommend Next Steps

Once the user approves the plan, tell them the implementation will typically
create:

1. `platform/workflows/[Name]/orchestrator.yaml`
2. `platform/workflows/[Name]/agents.yaml`
3. `platform/workflows/[Name]/handoffs.yaml`
4. `platform/workflows/[Name]/tools.yaml`
5. `platform/workflows/[Name]/context_variables.yaml`
6. `platform/workflows/[Name]/structured_outputs.yaml` when needed
7. `platform/workflows/[Name]/tools/*.py`
8. `platform/workflows/[Name]/ui/*.js` when the workflow uses UI tools

Mention separately that if the app should launch into this workflow by default,
the entry workflow is configured in `platform/config/ai.json`.

