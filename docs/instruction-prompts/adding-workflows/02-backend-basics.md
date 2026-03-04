# Instruction Prompt: Workflow Backend Configuration

**Task:** Configure the YAML files that define a workflow's behavior

**Complexity:** Low-Medium (file creation and configuration)

---

## Context for AI Agent

You are helping a user configure the backend YAML files for a MozaiksAI workflow. The user has already planned their workflow (agents, tools, handoffs) and now needs the actual configuration files created.

---

## Step 1: Gather Information

Ask the user:

1. **"What is your workflow called?"** (Should be PascalCase like `SupportBot`)
2. **"What agents did you plan?"** (List from planning phase)
3. **"What should each agent's personality be?"** (Helpful, formal, casual, etc.)

If they haven't planned yet, redirect them:
```
Let's plan your workflow first. Please read:
docs/instruction-prompts/adding-workflows/01-overview.md
```

---

## Step 2: Create the Folder Structure

```powershell
# Create from HelloWorld template
Copy-Item -Recurse workflows/HelloWorld workflows/[WorkflowName]
```

Verify the folder structure:
```
workflows/[WorkflowName]/
├── orchestrator.yaml
├── agents.yaml
├── handoffs.yaml
├── context_variables.yaml
├── tools.yaml
├── structured_outputs.yaml
├── hooks.yaml
└── tools/
    └── (Python files go here)
```

---

## Step 3: Configure orchestrator.yaml

Replace the contents with:

```yaml
workflow_name: [WorkflowName]          # MUST match folder name exactly
max_turns: 20
human_in_the_loop: true
startup_mode: AgentDriven              # or UserDriven if user speaks first
initial_agent: [FirstAgentName]
initial_message: "[FirstAgentName]: [Opening message the agent should say]"
```

### Startup Mode Options

| Mode | When to Use |
|------|-------------|
| `AgentDriven` | Agent greets user first (most common) |
| `UserDriven` | User initiates conversation |
| `BackendOnly` | No user interaction, background processing |

---

## Step 4: Configure agents.yaml

Create an entry for each agent:

```yaml
agents:
  - name: [AgentName]
    prompt_sections:
      - id: role
        heading: "[ROLE]"
        content: |
          You are [describe the agent's role].
          [Add personality traits: helpful, professional, casual, etc.]

      - id: objective
        heading: "[OBJECTIVE]"
        content: |
          Your goal is to [main purpose].
          [Add specific behaviors or constraints]

      - id: constraints
        heading: "[CONSTRAINTS]"
        content: |
          - [Rule 1: e.g., Always ask for order number before looking up orders]
          - [Rule 2: e.g., Never share customer data with other customers]
          - [Rule 3: e.g., Escalate if customer is frustrated]

    max_consecutive_auto_reply: 10
    auto_tool_mode: false
    structured_outputs_required: false
```

### Agent Configuration Tips

**For Routing/Coordinator Agents:**
```yaml
- id: routing
  heading: "[ROUTING]"
  content: |
    Route users to the appropriate specialist:
    - [AgentA] for [topic A]
    - [AgentB] for [topic B]
    When in doubt, ask clarifying questions.
```

**For Specialist Agents:**
```yaml
- id: capabilities
  heading: "[CAPABILITIES]"
  content: |
    You can:
    - [Action 1]
    - [Action 2]
    You cannot:
    - [Limitation 1]
```

**For Escalation Agents:**
```yaml
- id: escalation
  heading: "[ESCALATION PROTOCOL]"
  content: |
    You handle situations that other agents cannot resolve.
    Collect: [required information]
    Then: [what to do, e.g., create a ticket]
```

---

## Step 5: Configure handoffs.yaml

Create routing rules between agents:

```yaml
handoffs:
  # From coordinator to specialists
  - from_agent: [CoordinatorAgent]
    to_agent: [SpecialistA]
    condition: "[natural language condition, e.g., 'user asks about orders']"

  - from_agent: [CoordinatorAgent]
    to_agent: [SpecialistB]
    condition: "[condition]"

  # Return to coordinator
  - from_agent: [SpecialistA]
    to_agent: [CoordinatorAgent]
    condition: "[condition, e.g., 'issue is resolved']"

  # Escalation paths
  - from_agent: [SpecialistA]
    to_agent: [EscalationAgent]
    condition: "[condition, e.g., 'customer requests manager']"
```

### Handoff Best Practices

1. **Be specific** — Vague conditions cause routing errors
2. **Always have return paths** — Specialists should route back when done
3. **Define escalation triggers** — Frustration, high value, complexity

---

## Step 6: Configure context_variables.yaml

Define shared state:

```yaml
context_variables:
  # User identification
  - name: user_name
    type: string
    default: null
    description: "Customer name once identified"

  # Conversation context
  - name: current_topic
    type: string
    default: null
    description: "What the user is asking about"

  # Business data
  - name: order_id
    type: string
    default: null
    description: "Order being discussed"

  # Flags
  - name: is_escalated
    type: boolean
    default: false
    description: "Whether conversation has been escalated"
```

### Common Context Variables

| Use Case | Variables |
|----------|-----------|
| Customer Support | `user_name`, `order_id`, `ticket_id`, `escalation_reason` |
| Booking | `selected_date`, `selected_time`, `service_type` |
| Document Analysis | `document_id`, `current_page`, `extracted_data` |

---

## Step 7: Verify Configuration

Run these checks:

### 1. YAML Syntax Check
```powershell
# Use a YAML linter or just try to load
python -c "import yaml; yaml.safe_load(open('workflows/[WorkflowName]/orchestrator.yaml'))"
```

### 2. Name Consistency Check
Verify:
- `orchestrator.yaml` → `workflow_name` matches folder name
- `orchestrator.yaml` → `initial_agent` exists in `agents.yaml`
- `handoffs.yaml` → All agent names exist in `agents.yaml`

### 3. Server Load Check
```powershell
# Start server and check
# Visit: http://localhost:8000/api/workflows
# Your workflow should appear in the list
```

---

## Step 8: Summary Template

After configuration, provide this summary:

```markdown
## Backend Configuration Complete

### Files Created
- ✅ `workflows/[WorkflowName]/orchestrator.yaml`
- ✅ `workflows/[WorkflowName]/agents.yaml`
- ✅ `workflows/[WorkflowName]/handoffs.yaml`
- ✅ `workflows/[WorkflowName]/context_variables.yaml`

### Agents Configured
1. **[AgentName]** — [Role description]
2. **[AgentName]** — [Role description]

### Handoff Routes
- [From] → [To]: [Condition]

### Next Steps
Your backend is configured! Now you need:
1. **Tools** — Give agents capabilities (see 03-tools.md)
2. **UI Components** — Add interactive elements (see 04-ui-components.md)

Ready to add tools? Say "configure tools" and I'll help you set them up.
```

---

## Troubleshooting

### "Workflow not appearing in /api/workflows"

1. Check `workflow_name` matches folder name exactly (case-sensitive)
2. Verify all YAML files have valid syntax
3. Check server logs for loading errors
4. Restart the server

### "Agent not found" errors

1. Verify agent names match exactly across all YAML files
2. Check for typos in `initial_agent` and `handoffs.yaml`

### "Handoff not triggering"

1. Make conditions more specific
2. Check agent prompts include handoff instructions
3. Verify the target agent exists

---

## Quick Reference

### Required Files
```
orchestrator.yaml  — workflow_name, initial_agent, startup_mode
agents.yaml        — agent definitions with prompts
handoffs.yaml      — routing rules (optional for single-agent)
```

### Optional Files
```
context_variables.yaml   — shared state
structured_outputs.yaml  — output schemas
hooks.yaml               — lifecycle hooks
tools.yaml               — tool definitions (covered in next guide)
```
