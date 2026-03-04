# Adding Workflows

> **Guide:** Adding Workflows · Overview

---

## What's a Workflow?

A workflow is your AI application's brain. It defines:

- **Who** — The AI agents (like team members with different skills)
- **What** — What those agents can do (tools, actions)
- **How** — How they coordinate (who talks to who, when to hand off)

Think of it like setting up a customer service team:
- You have a **greeter** who welcomes people
- A **specialist** who handles technical questions
- A **manager** who escalates complex issues

In Mozaiks, each of these would be an "agent" in your workflow.

---

## What Does a Workflow Look Like?

Every workflow is a folder with configuration files:

```
workflows/
└── CustomerSupport/           # Your workflow folder
    ├── orchestrator.yaml      # How the conversation runs
    ├── agents.yaml            # Your AI agents
    ├── tools.yaml             # What agents can do
    ├── handoffs.yaml          # When to switch agents
    └── tools/                 # Python code for tools
        └── check_order.py
```

The **HelloWorld** workflow in the repo is a complete example — we'll copy it as our starting point.

---

## This Guide's Structure

| Step | What You'll Learn |
|------|------------------|
| [**Basics**](02-backend-basics.md) | Create your workflow folder, set up orchestrator and agents |
| [**Tools**](03-tools.md) | Give your agents abilities (call APIs, fetch data, etc.) |
| [**UI Components**](04-ui-components.md) | Show interactive cards in the chat |
| [**Testing**](05-testing.md) | Verify your workflow works |

---

!!! tip "New to Development?"

    **Let AI create your workflow!** Copy this prompt into Claude Code:

    ```
    I want to create a new Mozaiks workflow.

    Please read the instruction prompt at:
    docs/instruction-prompts/adding-workflows/01-overview.md

    My workflow should:
    - Be called: [YourWorkflowName]
    - Do this: [Describe what it should do in plain English]

    Start by helping me understand what agents and tools I need,
    then create everything for me.
    ```

---

## Quick Concepts

### Agents

An agent is an AI with a specific personality and job. You define:
- **Name** — What to call it
- **Prompt** — Its personality and instructions
- **Tools** — What it can do

### Tools

A tool is a function an agent can call. Examples:
- `check_order_status` — Look up an order in your database
- `send_email` — Send an email to a customer
- `show_calendar` — Display a calendar picker in the chat

### Handoffs

When one agent should pass the conversation to another:
- "When the user asks about billing, hand off to BillingAgent"
- "When the task is complete, hand back to Coordinator"

---

## Before You Start

You should have:
- Mozaiks running locally ([Getting Started](../../getting-started.md))
- An idea of what your workflow should do

---

**Next:** [Backend Basics](02-backend-basics.md)
