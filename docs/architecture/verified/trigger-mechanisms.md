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

