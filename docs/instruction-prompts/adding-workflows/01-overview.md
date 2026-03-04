# Instruction Prompt: Workflow Planning & Overview

**Task:** Help the user understand what workflow they need and plan the structure

**Complexity:** Low (conversation and planning)

**Time:** 5-10 minutes

---

## Context for AI Agent

You are helping a user plan a new workflow for MozaiksAI. Before writing any code, you need to understand:

1. What the workflow should accomplish
2. What agents are needed
3. What tools those agents need
4. How agents should hand off to each other

This is a **planning conversation**, not implementation yet.

---

## Step 1: Understand the Goal

Ask the user: **"What should this workflow do? Describe it like you're explaining to a colleague."**

Listen for:
- The main purpose (customer support, document analysis, booking, etc.)
- Who the "users" are (customers, employees, admins)
- What actions need to happen (look up data, send emails, show forms)

---

## Step 2: Identify Agents

Based on their description, suggest agents. Use this pattern:

**Single Agent Workflows** (simple):
- One agent that does everything
- Good for: Q&A bots, simple assistants

**Multi-Agent Workflows** (complex):
- **Coordinator/Router** — Understands intent, routes to specialists
- **Specialists** — Handle specific domains (billing, technical, orders)
- **Escalation** — Handles edge cases or human handoff

Ask: **"Does this sound right? Do you need more or fewer agents?"**

### Example Agent Suggestions

**Customer Support:**
```
- GreetingAgent: Welcomes users, understands their issue
- OrderAgent: Handles order-related questions
- TechnicalAgent: Handles technical/product questions
- EscalationAgent: Handles complaints or complex issues
```

**Document Analysis:**
```
- AnalyzerAgent: Reads and understands documents
- SummarizerAgent: Creates summaries
- QAAgent: Answers questions about the document
```

**Booking System:**
```
- AssistantAgent: Guides through booking process
(Single agent is often enough for linear flows)
```

---

## Step 3: Identify Tools

For each agent, ask: **"What actions does [AgentName] need to perform?"**

Categorize tools:

**Data Tools** (fetch/update information):
- `get_customer_info` — Look up customer data
- `check_order_status` — Query order database
- `update_ticket` — Modify a support ticket

**Action Tools** (do something):
- `send_email` — Send an email
- `create_ticket` — Create a support ticket
- `schedule_appointment` — Book a time slot

**UI Tools** (show interactive components):
- `show_calendar` — Display a date picker
- `show_form` — Display a form for user input
- `show_confirmation` — Display a confirmation card

---

## Step 4: Plan Handoffs

If multiple agents, ask: **"When should one agent hand off to another?"**

Create a handoff map:

```
GreetingAgent:
  → OrderAgent: when user asks about orders, shipping, returns
  → TechnicalAgent: when user asks about product features, bugs
  → EscalationAgent: when user is frustrated or requests manager

OrderAgent:
  → GreetingAgent: when order issue is resolved
  → EscalationAgent: when refund > $100 requested

TechnicalAgent:
  → GreetingAgent: when technical question answered
  → EscalationAgent: when bug requires engineering escalation
```

---

## Step 5: Confirm the Plan

Summarize what you've learned:

```
## Workflow: [Name]

### Purpose
[One sentence description]

### Agents
1. [AgentName] — [Role]
2. [AgentName] — [Role]

### Tools
- [tool_name] — [What it does] — Used by: [Agent]
- [tool_name] — [What it does] — Used by: [Agent]

### Handoffs
- [From] → [To]: [Condition]

### UI Components Needed
- [ComponentName] — [What it shows]
```

Ask: **"Does this plan look right? Should we adjust anything before I create the files?"**

---

## Step 6: Recommend Next Steps

Once the plan is confirmed, tell the user:

```
Great! Here's what we'll create:

1. workflows/[Name]/orchestrator.yaml — Workflow configuration
2. workflows/[Name]/agents.yaml — Agent definitions
3. workflows/[Name]/tools.yaml — Tool registry
4. workflows/[Name]/handoffs.yaml — Handoff rules
5. workflows/[Name]/tools/*.py — Tool implementations

For UI components:
6. chat-ui/src/workflows/[Name]/components/*.js

Ready to start building? Say "create the workflow" and I'll generate all the files.
```

---

## Common Workflow Patterns

### Pattern 1: Simple Q&A Bot
```
Agents: 1 (AssistantAgent)
Tools: None or data lookup only
Handoffs: None
```

### Pattern 2: Customer Support
```
Agents: 3-4 (Coordinator, Specialists, Escalation)
Tools: Customer lookup, ticket creation, order status
Handoffs: Based on intent classification
```

### Pattern 3: Data Entry / Forms
```
Agents: 1 (FormAssistant)
Tools: UI tools for forms, validation, submission
Handoffs: None
```

### Pattern 4: Document Processing
```
Agents: 2-3 (Analyzer, Summarizer, QA)
Tools: Document parsing, search, extraction
Handoffs: Sequential (analyze → summarize → answer questions)
```

---

## Questions to Ask If User Is Stuck

- "What problem are you trying to solve?"
- "Who will use this workflow?"
- "What's the most common scenario?"
- "What data does the workflow need access to?"
- "Does the user need to fill out any forms or make choices?"
