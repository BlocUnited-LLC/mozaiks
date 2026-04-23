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

Trigger a workflow when something happens in your backend:

```python
# your_backend/routes/orders.py
from mozaiksai import trigger_workflow

@router.post("/orders")
async def create_order(order: Order):
    # Save order to your DB
    saved = await db.orders.insert_one(order.dict())

    # Trigger CustomerSupport workflow for follow-up
    await trigger_workflow(
        workflow_name="CustomerSupport",
        user_id=order.user_id,
        context={"order_id": str(saved.inserted_id), "trigger": "new_order"}
    )

    return saved
```

---

## 4. Webhook/External Trigger

External systems (Stripe, Zapier, etc.) can call either the embedded runtime
factory or the canonical repo hosts directly.

Embedded runtime mode (`create_mozaiks_app()` mounted at `/ai`):

```http
POST /ai/workflows/CustomerSupport/trigger
Content-Type: application/json

{
  "user_id": "user_123",
  "context": {
    "order_id": "order_456",
    "issue": "payment_failed"
  }
}
```

Canonical repo host mode (`runtime_app.py`, `platform_app.py`, `studio_app.py`, or `mozaiks_app.py`):

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

### Backend (`mozaiksai`)

```python
from mozaiksai import trigger_workflow

# trigger_workflow signature:
await trigger_workflow(workflow_name, user_id, context={})
```

`create_mozaiks_app()` is the runtime-only convenience factory for external
embedding. It is not required when you run the canonical repo hosts directly.

### REST Endpoints

```
Embedded runtime factory:
POST /workflows/{name}/trigger
GET /workflows

Canonical repo hosts:
POST /api/workflows/{name}/trigger
GET /api/workflows

GET /api/workflows/{name}/runs?user_id=xxx
     → Get user's conversation history
```

---

## Summary

| Trigger | How |
|---------|-----|
| Button | `startWorkflow('CustomerSupport', {context})` |
| Route/Embed | `<WorkflowChat workflow="CustomerSupport" />` |
| Backend | `await trigger_workflow('CustomerSupport', ...)` |
| External | `POST /api/workflows/CustomerSupport/trigger` |
