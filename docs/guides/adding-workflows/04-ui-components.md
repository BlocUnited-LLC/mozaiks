# UI Components

UI components are React elements that appear in the chat when UI tools are triggered. They collect user input and send responses back to your Python tools.

---

## How It Works

```
Python Tool                  React Component              Python Tool
    │                              │                           │
    ├─── use_ui_tool() ──────────►│                           │
    │    (sends payload)           │                           │
    │                              │◄── User interacts         │
    │                              │                           │
    │◄─────── onResponse() ────────┤                           │
    │         (returns data)       │                           │
```

The runtime handles all WebSocket communication — you just define what to show and what to return.

---

!!! tip "New to Development?"

    **Let AI create your components!** Copy this prompt into Claude Code:

    ```
    I want to create UI components for my Mozaiks workflow.

    Please read the instruction prompt at:
    docs/instruction-prompts/adding-workflows/04-ui-components.md

    My workflow is: [WorkflowName]
    I need these UI components: [List components like "date picker", "form", "confirmation"]
    ```

---

## Quick Start

### 1. Create the Component

```jsx
// chat-ui/src/workflows/MyWorkflow/components/MyCard.js
import React from 'react';

export default function MyCard({ payload, onResponse, onCancel, eventId, ui_tool_id }) {
  const [value, setValue] = React.useState('');

  async function handleSubmit() {
    await onResponse({
      status: 'success',
      data: { value },
      eventId,
      ui_tool_id,
    });
  }

  return (
    <div className="rounded-xl border p-4">
      <p>{payload?.message ?? 'Enter a value:'}</p>
      <input
        value={value}
        onChange={(e) => setValue(e.target.value)}
        className="border rounded p-2 w-full mt-2"
      />
      <div className="flex gap-2 mt-4">
        <button onClick={handleSubmit} className="bg-blue-500 text-white px-4 py-2 rounded">
          Submit
        </button>
        <button onClick={onCancel} className="border px-4 py-2 rounded">
          Cancel
        </button>
      </div>
    </div>
  );
}
```

### 2. Export from Index

```js
// chat-ui/src/workflows/MyWorkflow/components/index.js
import MyCard from './MyCard';

const MyWorkflowComponents = {
  MyCard,  // Key must match ui.component in tools.yaml
};

export default MyWorkflowComponents;
```

### 3. Register Workflow

```js
// chat-ui/src/workflows/index.js
import MyWorkflowComponents from './MyWorkflow/components';

const WORKFLOW_REGISTRY = {
  MyWorkflow: { components: MyWorkflowComponents },
};
```

---

## Component Props

Every UI component receives these props from the runtime:

| Prop | Source | Purpose |
|------|--------|---------|
| `payload` | Your Python tool | Data you passed to `use_ui_tool()` |
| `onResponse` | Runtime | Send result back to Python |
| `onCancel` | Runtime | Signal cancellation |
| `eventId` | Runtime | Correlation ID (required in response) |
| `ui_tool_id` | Runtime | Tool identifier (required in response) |

---

## Calling onResponse

When the user completes their interaction, call `onResponse` with this structure:

```js
await onResponse({
  status: 'success',           // or 'cancelled', 'error'
  data: {
    // Your structured data
    selectedDate: '2024-03-15',
    quantity: 3,
  },
  eventId,                     // Pass through from props
  ui_tool_id,                  // Pass through from props
});
```

**Important:** Always include `eventId` and `ui_tool_id` — these are required for response routing.

---

## Component Examples

### Date Picker

```jsx
import React from 'react';

export default function DatePicker({ payload, onResponse, onCancel, eventId, ui_tool_id }) {
  const [selectedDate, setSelectedDate] = React.useState('');

  async function handleSelect() {
    await onResponse({
      status: 'success',
      data: { selected_date: selectedDate },
      eventId,
      ui_tool_id,
    });
  }

  return (
    <div className="p-4 border rounded-xl">
      <p className="mb-2">{payload?.message ?? 'Select a date:'}</p>
      <input
        type="date"
        value={selectedDate}
        onChange={(e) => setSelectedDate(e.target.value)}
        className="border rounded p-2"
      />
      <div className="flex gap-2 mt-4">
        <button onClick={handleSelect} disabled={!selectedDate}>
          Confirm
        </button>
        <button onClick={onCancel}>Cancel</button>
      </div>
    </div>
  );
}
```

### Confirmation Card

```jsx
import React from 'react';

export default function ConfirmationCard({ payload, onResponse, onCancel, eventId, ui_tool_id }) {
  async function handleConfirm() {
    await onResponse({
      status: 'success',
      data: { confirmed: true },
      eventId,
      ui_tool_id,
    });
  }

  async function handleDecline() {
    await onResponse({
      status: 'success',
      data: { confirmed: false },
      eventId,
      ui_tool_id,
    });
  }

  return (
    <div className="p-4 border rounded-xl">
      <h3 className="font-bold mb-2">{payload?.title ?? 'Confirm'}</h3>
      <p>{payload?.message ?? 'Are you sure?'}</p>

      {payload?.details && (
        <ul className="mt-2 text-sm">
          {payload.details.map((item, i) => (
            <li key={i}>• {item}</li>
          ))}
        </ul>
      )}

      <div className="flex gap-2 mt-4">
        <button onClick={handleConfirm} className="bg-green-500 text-white px-4 py-2 rounded">
          Yes, Confirm
        </button>
        <button onClick={handleDecline} className="bg-red-500 text-white px-4 py-2 rounded">
          No, Cancel
        </button>
      </div>
    </div>
  );
}
```

### Form Component

```jsx
import React from 'react';

export default function ReturnForm({ payload, onResponse, onCancel, eventId, ui_tool_id }) {
  const [reason, setReason] = React.useState('');
  const [comments, setComments] = React.useState('');

  async function handleSubmit(e) {
    e.preventDefault();
    await onResponse({
      status: 'success',
      data: {
        order_id: payload?.order_id,
        reason,
        comments,
      },
      eventId,
      ui_tool_id,
    });
  }

  return (
    <form onSubmit={handleSubmit} className="p-4 border rounded-xl">
      <h3 className="font-bold mb-4">Return Request</h3>
      <p className="text-sm mb-4">Order: {payload?.order_id}</p>

      <div className="mb-4">
        <label className="block mb-1">Reason for Return</label>
        <select
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          className="w-full border rounded p-2"
          required
        >
          <option value="">Select a reason...</option>
          {payload?.return_reasons?.map((r) => (
            <option key={r} value={r}>{r}</option>
          ))}
        </select>
      </div>

      <div className="mb-4">
        <label className="block mb-1">Additional Comments</label>
        <textarea
          value={comments}
          onChange={(e) => setComments(e.target.value)}
          className="w-full border rounded p-2"
          rows={3}
        />
      </div>

      <div className="flex gap-2">
        <button type="submit" className="bg-blue-500 text-white px-4 py-2 rounded">
          Submit Return
        </button>
        <button type="button" onClick={onCancel} className="border px-4 py-2 rounded">
          Cancel
        </button>
      </div>
    </form>
  );
}
```

---

## Folder Structure

```
chat-ui/src/workflows/
├── index.js                    # Registry of all workflows
└── MyWorkflow/
    └── components/
        ├── index.js            # Exports all components
        ├── DatePicker.js
        ├── ConfirmationCard.js
        └── ReturnForm.js
```

---

## What the Runtime Handles

You don't need to implement:

| Concern | Handled By |
|---------|------------|
| WebSocket connection | `SimpleTransport` |
| Event routing | `WorkflowUIRouter` |
| Response correlation | `event_id` matching |
| Reconnection | Persistence replay |
| Logging | Auto-instrumented |

Your only job: render UI and call `onResponse()` when done.

---

## Next Steps

- [Testing](05-testing.md) — Verify your workflow works end-to-end
