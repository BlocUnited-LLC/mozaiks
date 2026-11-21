# Trigger Mechanisms

Beyond the default ChatWidget behavior, mozaiksai supports multiple ways to trigger workflows.

These examples use the `CustomerSupport` workflow configured in the [Setup Guide](setup-guide.md).

---

## 1. Button/UI Trigger

Use the `useMozaiks` hook to trigger workflows from any UI element:

```jsx
import { useMozaiks } from '@mozaiks/chat-ui';

function OrderPage({ order }) {
  const { startWorkflow } = useMozaiks();

  return (
    <div>
      <h1>Order {order.id}</h1>

      <button onClick={() => startWorkflow('CustomerSupport', {
        context: { order_id: order.id }
      })}>
        Get Help with This Order
      </button>
    </div>
  );
}
```

The workflow name `'CustomerSupport'` is passed directly to `startWorkflow()`. Context variables are passed as the second argument.

---

## 2. Route-Based Trigger

Dedicate a page or section to a workflow using `WorkflowChat`:

```jsx
import { WorkflowChat } from '@mozaiks/chat-ui';

// /support → opens CustomerSupport workflow
function SupportPage() {
  return (
    <WorkflowChat
      workflow="CustomerSupport"
      userId={user.id}
      onClose={() => navigate('/')}
    />
  );
}

// /order/:id/help → opens CustomerSupport with order context
function OrderHelpPage({ params }) {
  return (
    <WorkflowChat
      workflow="CustomerSupport"
      userId={user.id}
      initialContext={{ order_id: params.id }}
      onClose={() => navigate(`/order/${params.id}`)}
    />
  );
}
```

### Embed the Studio chat and a private preview

`WorkflowChat` accepts `presentation="studio"` to use the same transcript, composer,
streaming messages and status bar as Factory. Supply `userId` from the authenticated
viewer, an authorized `appId`, a `getAccessToken` function and the
workflow's declared `initialContext`. Use `freshSession` for an isolated conversation.
Changing the viewer, workflow, app or context closes the previous conversation.
After an unexpected socket interruption the chat reconnects to the same session
with the current credential and requests resume from the last sequence. If that
attempt fails, the status bar offers Reconnect. Unsent input stays in the
composer. Only the user can send a turn again.

For a workflow with a preview, declare a one-way artifact UI tool. Its authenticated
WebSocket delivers `chat.tool_call` with `data.display` set to `artifact` and a unique
`data.tool_call_id`. The embed calls `onArtifact(frame, {chatId, afterUserInput})`
once for that event in the mounted conversation. `afterUserInput` identifies the
turn following a local submission. The application can use `onSessionChange` to
clear the displayed draft when the conversation changes and `onReplyActivity` for
notifications outside the chat.

Validate the expected workflow, component, tool payload and resource identity in
the consumer. A module can verify the latest draft against
`AG2PersistenceManager.fetch_owned_workflow_artifact` using the authenticated
app and viewer, the expected workflow and the conversation ID. Compare its
persisted tool call ID and resource binding before attributing an action to that
draft. A newer artifact replaces the earlier one.

Ask the owning module to prepare the draft and recheck the viewer
before fetching either version. The module decides whether an operation may use a
sandbox. Keep ordinary conversational replies and opening greetings free of render
requests. Store and display a completed render only while its identity and expiry
match the selected private draft.

Render the authorized HTML from the private document compiler with
`<ArtifactPreviewPane documentHtml={documentHtml} title="Draft preview" />`,
imported from `@mozaiks/chat-ui`. This is the document mode of Factory's preview
pane. Its nested sandbox displays the self-contained app UI in an opaque origin.
The saved-build workbench uses the URL mode for its running app. Publishing or
accepting a private draft remains an explicit action in the consuming application.

---

## 3. CRUD Event Trigger (Backend)

Trigger a workflow when something happens in your backend by calling the
runtime host's trigger endpoint. This is an HTTP call — there is no
Python helper function; the trigger goes over the wire to the running server.

```python
# your_services/routes/orders.py
import httpx

MOZAIKS_BASE_URL = "http://localhost:8000"

@router.post("/orders")
async def create_order(order: Order):
    # Save order to your DB
    saved = await db.orders.insert_one(order.dict())

    # Trigger CustomerSupport workflow for follow-up
    async with httpx.AsyncClient() as client:
        await client.post(
            f"{MOZAIKS_BASE_URL}/api/workflows/CustomerSupport/trigger",
            json={
                "user_id": order.user_id,
                "context": {"order_id": str(saved.inserted_id), "trigger": "new_order"},
            },
        )

    return saved
```

---

## 4. Webhook/External Trigger

External systems (payment provider, Zapier, etc.) can call the canonical host directly:

```http
POST /api/workflows/CustomerSupport/trigger
Content-Type: application/json

{
  "user_id": "user_123",
  "context": {
    "order_id": "order_456",
    "issue": "payment_failed"
  }
}
```

This endpoint is available on `mozaiksai.hosts.runtime` and all hosts that
layer on top of it (`platform`, `studio`, `mozaiks`).

---

## API Reference

### Frontend (`@mozaiks/chat-ui`)

```javascript
import { ChatWidget, WorkflowChat, useMozaiks } from '@mozaiks/chat-ui';

// ChatWidget: Floating overlay button
<ChatWidget
  endpoint="ws://localhost:8000/ai"
  userId={user.id}
  brandName="Acme Support"  // optional
  logo="/logo.svg"          // optional
/>

// WorkflowChat: Embeddable workflow chat
<WorkflowChat
  workflow="CustomerSupport"
  userId={user.id}
  onClose={() => {}}
  initialContext={{}}
  brandName="Acme Support"   // optional
  logo="/logo.svg"           // optional
  backgroundImage="/bg.png"  // optional
/>

// useMozaiks hook: Trigger workflows programmatically
const { startWorkflow } = useMozaiks();
startWorkflow(workflowName: string, options?: { context?: object })
```

### REST Endpoints

```
POST /api/workflows/{name}/trigger
     → Trigger a workflow from a backend or external system

GET  /api/workflows
     → List available workflows

GET  /api/workflows/{name}/runs?user_id=xxx
     → Get user's conversation history
```

---

## Summary

| Trigger | How |
|---------|-----|
| Button | `startWorkflow('CustomerSupport', {context})` |
| Route/Embed | `<WorkflowChat workflow="CustomerSupport" />` |
| Backend | `POST /api/workflows/CustomerSupport/trigger` |
| External | `POST /api/workflows/CustomerSupport/trigger` |

