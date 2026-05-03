# Workflow Authoring Rules

**Reference:** See [/CLAUDE.md](/CLAUDE.md) for patterns and [/ARCHITECTURE.md](/ARCHITECTURE.md) for context.

Use these rules when touching:
- `app/workflows/**`
- `factory_app/workflows/**`
- `factory_app/app/workflows/**`
- Any workflow YAML files or tool implementations

## Core Principles

### Tools Are Dumb, LLMs Reason

Never write inference or heuristic logic in tools:

```python
# BAD
if "automat" in feature.lower():
    needs_ai = True

# GOOD
data = context_variables.get("structured_output")  # LLM already reasoned
await persist(data)
```

### Structured Outputs Drive UI Artifacts

When an agent needs to produce a UI artifact:
1. Define model in `structured_outputs.yaml`
2. Register agent to model in `registry`
3. Set `structured_outputs_required: true` in agent config
4. Set `auto_tool_call: true` on the tool
5. Tool reads from `context_variables["structured_output"]`

### Context Flows Downstream

- Parent workflows store data in `context_variables`
- Child workflows read from `context_variables`
- Check `is_child_workflow` to determine behavior
- Pass context via `value_manifest`, `concept_overview`, `build_plan`

## File Conventions

| File | Purpose |
|------|---------|
| `orchestrator.yaml` | Workflow bootstrap (name, initial_agent, max_turns) |
| `agents.yaml` | Agent definitions with prompts |
| `handoffs.yaml` | Agent-to-agent routing rules |
| `structured_outputs.yaml` | Output models + agent→model registry |
| `tools.yaml` | Tool bindings, auto_tool_call, UI metadata |
| `context_variables.yaml` | Initial context state |
| `tools/*.py` | Python tool implementations |
| `ui/{WorkflowName}/` | React UI components |

## Agent Configuration

### Conversational Agent
```yaml
- name: InterviewAgent
  structured_outputs_required: false
```

### Structured Output Agent (with auto-invoke tool)
```yaml
- name: OutputAgent
  structured_outputs_required: true
  # No auto-tool field lives in agents.yaml; auto-tool execution is derived from tools.yaml.
```

## Tool Configuration

### Auto-Invoke Tool (for structured output → UI)
```yaml
- agent: OutputAgent
  function: save_output
  auto_tool_call: true
  ui:
    component: MyComponent
    mode: artifact
```

### Manual Tool
```yaml
- agent: SomeAgent
  function: get_data
  auto_tool_call: false
  ui:
    component: null
    mode: null
```

## Constraints

Do not:
- Put reasoning/heuristic logic in tools
- Hardcode workflow-specific behavior in runtime
- Skip `structured_outputs.yaml` when agent needs typed output
- Forget `is_child_workflow` check when workflow can be spawned by parent

Do:
- Keep tools simple (read context → persist → emit)
- Define clear output models for agents that produce artifacts
- Use `auto_tool_call: true` for structured output → UI flows
- Document context variable contracts between workflows
