# Instruction Prompt: Testing a Workflow

**Task:** Verify a workflow is configured correctly and works end-to-end

**Complexity:** Low (verification and debugging)

---

## Context for AI Agent

You are helping a user test their MozaiksAI workflow. This involves checking configuration, verifying the workflow loads, and testing all components work together.

---

## Step 1: Gather Information

Ask the user:

1. **"What is your workflow name?"**
2. **"Does it have UI tools?"** (Components that render in chat)
3. **"Is it single-agent or multi-agent?"**

---

## Step 2: Validate Configuration Files

Run these checks in order:

### Check 1: File Structure
```powershell
# List workflow files
Get-ChildItem -Path "workflows/[WorkflowName]" -Recurse

# Expected structure:
# workflows/[WorkflowName]/
# ├── orchestrator.yaml
# ├── agents.yaml
# ├── handoffs.yaml
# ├── tools.yaml
# ├── context_variables.yaml
# └── tools/
#     └── *.py files
```

### Check 2: orchestrator.yaml
```powershell
# Read and validate
Get-Content "workflows/[WorkflowName]/orchestrator.yaml"

# Verify:
# - workflow_name matches folder name exactly (case-sensitive)
# - initial_agent exists in agents.yaml
# - startup_mode is valid (AgentDriven, UserDriven, BackendOnly)
```

### Check 3: agents.yaml
```powershell
Get-Content "workflows/[WorkflowName]/agents.yaml"

# Verify:
# - At least one agent defined
# - Each agent has name and prompt_sections
# - Agent names are unique
```

### Check 4: handoffs.yaml (if multi-agent)
```powershell
Get-Content "workflows/[WorkflowName]/handoffs.yaml"

# Verify:
# - from_agent and to_agent match agent names exactly
# - No circular handoffs without exit conditions
```

### Check 5: tools.yaml
```powershell
Get-Content "workflows/[WorkflowName]/tools.yaml"

# For each tool, verify:
# - agent matches an agent name
# - file exists in tools/ folder
# - function matches Python function name
```

### Check 6: Tool Python Files
```powershell
# List tool files
Get-ChildItem "workflows/[WorkflowName]/tools/*.py"

# For each file, verify:
# - Function is async
# - Function returns Dict[str, Any]
# - No syntax errors
python -c "from workflows.[WorkflowName].tools.[tool_name] import [function_name]; print('OK')"
```

---

## Step 3: Check Frontend (UI Tools Only)

If the workflow has UI tools:

### Check 7: Components Exist
```powershell
Get-ChildItem "chat-ui/src/workflows/[WorkflowName]/components/"

# Should see:
# - index.js
# - [ComponentName].js for each UI tool
```

### Check 8: Components Exported
```powershell
Get-Content "chat-ui/src/workflows/[WorkflowName]/components/index.js"

# Verify:
# - Each component imported
# - Each component exported with key matching tools.yaml ui.component
```

### Check 9: Workflow Registered
```powershell
Get-Content "chat-ui/src/workflows/index.js"

# Verify:
# - Import statement for [WorkflowName]Components
# - Entry in WORKFLOW_REGISTRY
```

---

## Step 4: Test Workflow Loading

```powershell
# Start server if not running
python -m mozaiksai.main

# Check workflows endpoint
Invoke-RestMethod -Uri "http://localhost:8000/api/workflows"
```

Expected: Your workflow appears in the list

If missing, check server logs:
```powershell
# Look for loading errors
# Common issues:
# - YAML syntax errors
# - workflow_name mismatch
# - Missing required files
```

---

## Step 5: Test Conversation Flow

### Create Test Chat
```powershell
$body = @{
    workflow_name = "[WorkflowName]"
    app_id = "test-app"
    user_id = "test-user"
} | ConvertTo-Json

$response = Invoke-RestMethod -Uri "http://localhost:8000/api/chat" -Method POST -ContentType "application/json" -Body $body
$chatId = $response.chat_id
Write-Host "Chat ID: $chatId"
```

### Test Messages
```powershell
# Send test message
$message = @{ content = "Hello, I need help" } | ConvertTo-Json
Invoke-RestMethod -Uri "http://localhost:8000/api/chat/$chatId/message" -Method POST -ContentType "application/json" -Body $message
```

### Verify Agent Response
Check that:
- Agent responds appropriately to the message
- Response matches agent's personality/role
- No error messages in response

---

## Step 6: Test Tools

### Standard Tools
1. Identify a message that should trigger the tool
2. Send the message
3. Verify tool is called (check server logs)
4. Verify agent uses tool response

### UI Tools
1. Identify trigger message
2. Send message
3. Open chat UI in browser
4. Verify component renders
5. Submit the component
6. Verify agent receives response

---

## Step 7: Test Handoffs (Multi-Agent)

For each handoff rule:

1. Start fresh conversation
2. Send message matching handoff condition
3. Verify different agent responds
4. Check handoff logged correctly

Example test sequence:
```
User: "I have a question about my order"
→ Should handoff to OrderAgent

User: "Actually, I need technical help"
→ Should handoff to TechnicalAgent

User: "Never mind, it's resolved"
→ Should handoff back to GreetingAgent
```

---

## Step 8: Generate Test Report

After testing, provide this summary:

```markdown
## Workflow Test Report: [WorkflowName]

### Configuration Validation
| Check | Status | Notes |
|-------|--------|-------|
| orchestrator.yaml | ✅/❌ | [details] |
| agents.yaml | ✅/❌ | [details] |
| handoffs.yaml | ✅/❌ | [details] |
| tools.yaml | ✅/❌ | [details] |
| Tool implementations | ✅/❌ | [details] |
| UI components | ✅/❌ | [details] |
| Workflow registry | ✅/❌ | [details] |

### Functional Tests
| Test | Status | Notes |
|------|--------|-------|
| Workflow loads | ✅/❌ | |
| Initial agent responds | ✅/❌ | |
| Standard tools work | ✅/❌ | |
| UI tools render | ✅/❌ | |
| UI tools submit | ✅/❌ | |
| Handoffs trigger | ✅/❌ | |

### Issues Found
1. [Issue description]
   - **File:** [file path]
   - **Fix:** [what to change]

### Recommendations
- [Any improvements suggested]
```

---

## Common Issues & Fixes

### "Workflow not found in /api/workflows"

**Cause:** workflow_name doesn't match folder
**Fix:** Update orchestrator.yaml:
```yaml
workflow_name: [ExactFolderName]  # Case-sensitive
```

### "Agent 'X' not found"

**Cause:** Typo or case mismatch
**Fix:** Ensure exact match in:
- orchestrator.yaml → initial_agent
- handoffs.yaml → from_agent, to_agent
- tools.yaml → agent

### "Tool function not found"

**Cause:** Function name mismatch
**Fix:** Ensure tools.yaml `function` matches Python function name exactly

### "UI component not rendering"

**Cause:** Component not exported or registered
**Fix:**
1. Export from components/index.js with correct key
2. Register workflow in chat-ui/src/workflows/index.js

### "onResponse timeout"

**Cause:** Missing eventId or ui_tool_id
**Fix:** Include both in onResponse call:
```js
await onResponse({
  status: 'success',
  data: { ... },
  eventId,      // Must include
  ui_tool_id,   // Must include
});
```

### "YAML parse error"

**Cause:** Invalid YAML syntax
**Fix:**
- Use spaces, not tabs
- Check indentation consistency
- Quote strings with special characters
- Validate with: `python -c "import yaml; yaml.safe_load(open('file.yaml'))"`
