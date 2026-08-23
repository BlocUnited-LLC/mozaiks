# UI UI patterns

This document describes standard patterns for tool-UI interactions in the mozaiksai runtime.

---

## Pattern 1: Single-Step UI Interaction

**Use Case**: Tool performs action and immediately presents result to user.

**Flow**:
1. Tool executes business logic
2. Tool emits ONE UI event with complete payload
3. User interacts with result

**Example**: API key input
```python
async def request_api_key(service: str):
    response = await use_ui_tool("AgentAPIKeyInput", {
        "service": service,
        "label": f"{service} API Key"
    })
    return response.get("api_key")
```

**When to Use**:
- No approval needed before action
- Action is cheap/fast
- User just needs to provide input or view result

---

## Pattern 2: Two-Step Confirmation Workflow

**Use Case**: Tool needs user approval before performing expensive/irreversible action.

**Flow**:
1. Tool emits FIRST UI event (confirmation) with minimal payload
2. User confirms or cancels
3. If confirmed: Tool performs action
4. Tool emits SECOND UI event (result) with complete payload
5. User interacts with result

**Example**: File generation with confirmation
```python
async def generate_files(confirmation_only: bool = True):
    if confirmation_only:
        # Step 1: Ask for confirmation (empty payload)
        confirm_response = await use_ui_tool("DownloadCenter", {
            "files": [],  # Empty - just asking permission
            "agent_message": "Ready to generate files?",
            "downloadType": "single"
        })
        
        if confirm_response.get("status") == "cancelled":
            return {"status": "cancelled"}
        
        # Step 2: User confirmed - create files
        files = await create_files()
        
        # Step 3: Show result UI with populated payload
        download_response = await use_ui_tool("DownloadCenter", {
            "files": files,  # Populated - actual data
            "agent_message": "Files ready! Click to download.",
            "downloadType": "bulk" if len(files) > 1 else "single"
        })
        
        return download_response
    else:
        # Single-step mode: create immediately
        files = await create_files()
        response = await use_ui_tool("DownloadCenter", {
            "files": files,
            "agent_message": "Generated files ready!",
            "downloadType": "bulk" if len(files) > 1 else "single"
        })
        return response
```

**When to Use**:
- Action is expensive (API calls, file generation, database writes)
- Action is irreversible
- User should review/approve before proceeding
- Result requires user interaction beyond just viewing

**Key Characteristics**:
- Tool has `confirmation_only` boolean parameter
- Same UI component handles both confirmation and result states
- Component uses payload emptiness to determine which UI to render
- TWO `use_ui_tool()` calls from single tool invocation

---

## Pattern 3: Multi-Step Wizard

**Use Case**: Complex workflow requiring multiple sequential user inputs.

**Flow**:
1. Tool emits UI for Step 1
2. User provides input
3. Tool processes + emits UI for Step 2
4. User provides input
5. ... (repeat for N steps)
6. Tool completes and returns final result

**Example**: Multi-service API key collection
```python
async def collect_api_keys_bundle(services: List[str]):
    # Single UI handles all services via stepper/tabs
    response = await use_ui_tool("AgentAPIKeysBundleInput", {
        "services": [
            {"name": s, "label": f"{s} API Key"} 
            for s in services
        ],
        "description": "Please provide API keys for the following services:"
    })
    
    # Component internally manages steps, returns all data at once
    return response.get("api_keys", {})
```

**When to Use**:
- Multiple related inputs needed
- Inputs depend on each other (step 2 depends on step 1 answer)
- Better UX to guide user through steps vs. showing all at once

**Key Characteristics**:
- Component has internal state management (stepper, tabs, accordion)
- Single `use_ui_tool()` call, component handles multi-step internally
- Returns all collected data when user completes final step

---

## Pattern 4: Artifact vs. Inline Display

**Use Case**: Different UI presentations based on content complexity.

**Artifact Display** (`display="artifact"`):
- Opens in dedicated side panel
- For complex/large content (diagrams, workflows, code)
- User can expand/collapse panel
- Example: ActionPlan, CodeEditor

**Inline Display** (`display="inline"`):
- Appears in message stream
- For simple/quick interactions (forms, confirmations, downloads)
- Doesn't interrupt conversation flow
- Example: API key inputs, file downloads, confirmations

**Configuration**:
```yaml
# tools.yaml
tools:
  - agent: ActionPlanAgent
    file: render_action_plan.py
    function: render_action_plan
    tool_type: UI_Tool
    auto_tool_call: true
    ui:
      component: ActionPlan
      mode: artifact
  - agent: DeliveryAgent
    file: open_download_center.py
    function: open_download_center
    tool_type: UI_Tool
    auto_tool_call: true
    ui:
      component: DownloadCenter
      mode: inline
lifecycle_tools: []
```

**Declarative type rules**:
- `UI_Tool` = interactive surface that waits for a user response via `use_ui_tool(...)`
- `UI_Surface` = one-way artifact/status surface emitted via `emit_ui_surface(...)`
- `Agent_Tool` = backend-only logic; omit the `ui` block entirely

**Component naming convention**:
- Only components registered in the workflow's `ui/index.js` and referenced by
  a `ui.component` entry in `tools.yaml` are artifacts or inline components.
- Internal sub-components that render *inside* a registered artifact (tabs,
  split panes, status strips) are **panes** — name them `*Pane.js` (e.g.
  `PreviewPane`, `FileTreePane`), never `*Artifact.js`. An `Artifact` suffix
  on an unregistered component misleads readers (and future generator agents)
  into treating it as an independently registered surface.

---

## Implementation Guidelines for Generator Agents

When creating tools that emit UI events:

### 1. Single-Step Tools
```python
# No confirmation needed - just do it
async def simple_action():
    result = await do_work()
    response = await use_ui_tool("ResultComponent", {"data": result})
    return response
```

**System Message Guidance**:
```
This tool performs [action] and shows result immediately.
No confirmation required.
Emits ONE UI event with complete payload.
```

### 2. Two-Step Confirmation Tools
```python
async def action_with_confirmation(confirmation_only: bool = True):
    if confirmation_only:
        # Confirmation step
        confirm = await use_ui_tool("Component", {"preview": "...", "data": []})
        if confirm.get("status") == "cancelled":
            return {"status": "cancelled"}
        
        # Action step
        result = await do_work()
        
        # Result step
        response = await use_ui_tool("Component", {"data": result})
        return response
    else:
        # Skip confirmation
        result = await do_work()
        response = await use_ui_tool("Component", {"data": result})
        return response
```

**System Message Guidance**:
```
This tool supports two modes:

confirmation_only=True (default):
  - Emits TWO UI events
  - First: Confirmation dialog (empty/minimal payload)
  - Second: Result UI (full payload)
  
confirmation_only=False:
  - Emits ONE UI event
  - Immediate action + result

Component must handle both empty (confirmation) and full (result) payloads.
```

### 3. UI Component Requirements

**For components used in two-step workflows:**

```jsx
function MyComponent({ payload }) {
  const isEmpty = !payload.data || payload.data.length === 0;
  
  if (isEmpty) {
    // Confirmation mode
    return (
      <div>
        <p>{payload.preview || "Ready to proceed?"}</p>
        <button onClick={() => onResponse({status: "confirmed"})}>Yes</button>
        <button onClick={() => onResponse({status: "cancelled"})}>No</button>
      </div>
    );
  } else {
    // Result mode
    return (
      <div>
        <p>Results:</p>
        {payload.data.map(item => <div>{item}</div>)}
      </div>
    );
  }
}
```

---

## Summary Table

| Pattern | UI Events | Use Case | Example |
|---------|-----------|----------|---------|
| Single-Step | 1 | Simple input/output | API key input |
| Two-Step Confirmation | 2 | Expensive action requiring approval | File generation |
| Multi-Step Wizard | 1 (with internal steps) | Sequential related inputs | Multi-service setup |
| Artifact vs Inline | N/A | Display complexity | ActionPlan (artifact) vs API key (inline) |

---

## Best Practices

1. **Default to confirmation for expensive operations**
   - File generation → `confirmation_only=True` by default
   - API calls → `confirmation_only=True` by default
   - Simple inputs → Single-step

2. **Use consistent payload patterns**
   - Empty array/object → Confirmation state
   - Populated array/object → Result state
   - Component logic should check payload emptiness

3. **Provide clear agent messages**
   - Confirmation: "Ready to [action]? Yes/No"
   - Result: "[Action] complete! Here are the results."

4. **Handle cancellation gracefully**
   - Always check `status == "cancelled"` after confirmation
   - Return early with clear status message
   - Don't perform action if user cancelled

5. **Document in `tools.yaml`**
   ```yaml
   tools:
     - agent: PlannerAgent
       file: my_tool.py
       function: my_tool
       tool_type: UI_Tool
       auto_tool_call: true
       description: "If confirmation_only=true, do a two-step UI flow."
       ui:
         component: MyComponent
         mode: inline
   lifecycle_tools: []
   ```
