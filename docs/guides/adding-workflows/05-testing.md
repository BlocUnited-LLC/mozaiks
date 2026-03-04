# Testing Your Workflow

Before going live, verify your workflow loads correctly and all components work together.

---

!!! tip "New to Development?"

    **Let AI test your workflow!** Copy this prompt into Claude Code:

    ```
    I want to test my Mozaiks workflow.

    Please read the instruction prompt at:
    docs/instruction-prompts/adding-workflows/05-testing.md

    My workflow is: [WorkflowName]
    ```

---

## Pre-Flight Checklist

### Backend Files

- [ ] `workflows/[Name]/orchestrator.yaml` — `workflow_name` matches folder
- [ ] `workflows/[Name]/agents.yaml` — At least one agent defined
- [ ] `workflows/[Name]/handoffs.yaml` — Valid agent names (if multi-agent)
- [ ] `workflows/[Name]/tools.yaml` — Tool files exist and functions match
- [ ] `workflows/[Name]/tools/*.py` — All tool implementations present

### Frontend Files (if using UI tools)

- [ ] `chat-ui/src/workflows/[Name]/components/*.js` — Components exist
- [ ] `chat-ui/src/workflows/[Name]/components/index.js` — All components exported
- [ ] `chat-ui/src/workflows/index.js` — Workflow registered

---

## Step 1: Verify Workflow Loads

Start the backend server and check the workflows endpoint:

```powershell
# Start server (if not running)
python -m mozaiksai.main

# In another terminal, check workflows
curl http://localhost:8000/api/workflows
```

Your workflow should appear in the list. If missing:

1. Check folder name matches `workflow_name` in orchestrator.yaml
2. Validate YAML syntax (no tabs, proper indentation)
3. Check server logs for loading errors

---

## Step 2: Test Agent Responses

### Via API

```powershell
# Create a test chat
$response = Invoke-RestMethod -Uri "http://localhost:8000/api/chat" -Method POST -ContentType "application/json" -Body '{"workflow_name": "[WorkflowName]", "app_id": "test", "user_id": "test"}'
$chatId = $response.chat_id

# Send a message
Invoke-RestMethod -Uri "http://localhost:8000/api/chat/$chatId/message" -Method POST -ContentType "application/json" -Body '{"content": "Hello"}'
```

### Via Chat UI

1. Start the frontend: `npm start` in `chat-ui/`
2. Open http://localhost:3000
3. Select your workflow
4. Send test messages

Verify:
- Agent responds appropriately
- Handoffs trigger on correct conditions
- Context persists across messages

---

## Step 3: Test Tools

### Standard Tools

1. Send a message that should trigger the tool
2. Check tool executes and returns data
3. Verify agent uses the tool response

### UI Tools

1. Send a message that triggers the UI tool
2. Verify component renders in chat
3. Fill out and submit the component
4. Verify response is received and agent continues

---

## Step 4: Test Handoffs (Multi-Agent)

If your workflow has multiple agents:

1. Start with the initial agent
2. Send messages that trigger handoff conditions
3. Verify the correct agent takes over
4. Test return handoffs back to original agent
5. Test escalation paths

---

## Step 5: Test Edge Cases

- **Empty input:** Does agent handle gracefully?
- **Invalid data:** Does tool error handling work?
- **UI cancellation:** Does cancel button work?
- **Long conversations:** Does context persist?
- **Multiple tabs:** Does each get separate session?

---

## Common Issues

### "Workflow not found"

- Folder name doesn't match `workflow_name`
- YAML syntax error preventing load
- Server not restarted after adding workflow

### "Agent not found"

- Typo in `initial_agent` or handoff rules
- Agent name case mismatch

### "Tool not executing"

- Function name in Python doesn't match `tools.yaml`
- Missing `async` keyword on tool function
- Import error in tool file

### "UI component not rendering"

- Component name doesn't match `ui.component` in tools.yaml
- Component not exported from index.js
- Workflow not registered in workflows/index.js

### "onResponse not working"

- Missing `eventId` or `ui_tool_id` in response
- Not using `await` with onResponse
- Invalid response structure

---

## Verification Complete

Once all tests pass:

```markdown
## Workflow Testing Complete

### Verified
- ✅ Workflow loads at /api/workflows
- ✅ Initial agent responds correctly
- ✅ Tools execute and return data
- ✅ UI components render and submit
- ✅ Handoffs trigger correctly
- ✅ Error handling works

### Ready for
- Integration with your application
- User acceptance testing
- Production deployment
```

---

## Next Steps

Your workflow is ready. Consider:

1. **Adding more tools** — Expand agent capabilities
2. **Refining prompts** — Improve agent responses
3. **Adding telemetry** — Track usage and costs
4. **Documentation** — Document the workflow for your team
