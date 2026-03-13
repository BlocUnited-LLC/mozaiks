# Instruction Prompt: Creating Workflow UI Components

**Task:** Create React components for workflow UI tools

**Complexity:** Medium

---

## Context for AI Agent

You are helping a user create UI components for a workflow.

Current repo target:

- workflow UI lives under `platform/workflows/[WorkflowName]/ui/`
- `platform/workflows/[WorkflowName]/ui/index.js` is auto-discovered by the shared shell
- do not create workflow component registries under `chat-ui/src/workflows/`

---

## Step 1: Understand the Component Requirements

Ask the user:

1. What component is needed?
2. What data will it receive in `payload`?
3. What data should it return through `onResponse()`?
4. Should it render inline in chat or as an artifact surface?

---

## Step 2: Set Up the Folder Structure

```powershell
New-Item -ItemType Directory -Force -Path "platform/workflows/[WorkflowName]/ui"
```

Expected structure:

```text
platform/workflows/[WorkflowName]/
└── ui/
    ├── index.js
    ├── [Component1].js
    └── [Component2].js
```

---

## Step 3: Create a Component Template

```jsx
import React from 'react';

export default function ExampleCard({ payload, onResponse, onCancel, eventId, ui_tool_id }) {
  const [value, setValue] = React.useState(payload?.initialValue ?? '');

  async function handleSubmit() {
    await onResponse({
      status: 'success',
      data: { value },
      eventId,
      ui_tool_id,
    });
  }

  return (
    <div className="p-4 border rounded-xl bg-white shadow-sm">
      <p className="mb-4">{payload?.message ?? 'Provide input'}</p>
      <input
        type="text"
        value={value}
        onChange={(event) => setValue(event.target.value)}
        className="w-full border rounded p-2 mb-4"
      />
      <div className="flex gap-2">
        <button onClick={handleSubmit} className="px-4 py-2 rounded bg-cyan-600 text-white">
          Submit
        </button>
        <button onClick={onCancel} className="px-4 py-2 rounded border">
          Cancel
        </button>
      </div>
    </div>
  );
}
```

Always pass `eventId` and `ui_tool_id` back through `onResponse()`.

---

## Step 4: Export Components for Auto-Discovery

`platform/workflows/[WorkflowName]/ui/index.js` should export an object of
component names to components.

```js
import ExampleCard from './ExampleCard';

export default {
  ExampleCard,
};
```

The export key must match the `ui.component` value from `tools.yaml`.

---

## Step 5: Validation Checklist

- components live under `platform/workflows/[WorkflowName]/ui/`
- `ui/index.js` exports every component used by `tools.yaml`
- `onResponse()` includes `eventId` and `ui_tool_id`
- the component handles cancel or unsupported input cleanly
- the component renders correctly in the browser shell
            className={`w-full border rounded p-2 ${errors.name ? 'border-red-500' : ''}`}
          />
          {errors.name && <p className="text-red-500 text-sm mt-1">{errors.name}</p>}
        </div>

        {/* Email field */}
        <div>
          <label className="block text-sm font-medium mb-1">Email *</label>
          <input
            type="email"
            value={formData.email}
            onChange={(e) => updateField('email', e.target.value)}
            className={`w-full border rounded p-2 ${errors.email ? 'border-red-500' : ''}`}
          />
          {errors.email && <p className="text-red-500 text-sm mt-1">{errors.email}</p>}
        </div>

        {/* Phone field */}
        <div>
          <label className="block text-sm font-medium mb-1">Phone</label>
          <input
            type="tel"
            value={formData.phone}
            onChange={(e) => updateField('phone', e.target.value)}
            className="w-full border rounded p-2"
          />
        </div>

        {/* Message field */}
        <div>
          <label className="block text-sm font-medium mb-1">Message</label>
          <textarea
            value={formData.message}
            onChange={(e) => updateField('message', e.target.value)}
            rows={3}
            className="w-full border rounded p-2"
          />
        </div>
      </div>

      <div className="flex gap-2 mt-6">
        <button
          type="submit"
          className="bg-blue-500 hover:bg-blue-600 text-white px-6 py-2 rounded"
        >
          Submit
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="border hover:bg-gray-100 px-4 py-2 rounded"
        >
          Cancel
        </button>
      </div>
    </form>
  );
}
```

### Selection/Options Component

```jsx
// chat-ui/src/workflows/[WorkflowName]/components/OptionSelector.js
import React from 'react';

export default function OptionSelector({ payload, onResponse, onCancel, eventId, ui_tool_id }) {
  const [selected, setSelected] = React.useState(null);
  const options = payload?.options ?? [];
  const allowMultiple = payload?.allowMultiple ?? false;
  const [multiSelected, setMultiSelected] = React.useState([]);

  function toggleMulti(option) {
    setMultiSelected(prev =>
      prev.includes(option.id)
        ? prev.filter(id => id !== option.id)
        : [...prev, option.id]
    );
  }

  async function handleSubmit() {
    const selection = allowMultiple ? multiSelected : selected;
    if (!selection || (Array.isArray(selection) && selection.length === 0)) return;

    await onResponse({
      status: 'success',
      data: { selection },
      eventId,
      ui_tool_id,
    });
  }

  return (
    <div className="p-4 border rounded-xl bg-white">
      <h3 className="font-bold mb-2">{payload?.title ?? 'Select an Option'}</h3>
      <p className="text-gray-600 mb-4">{payload?.message}</p>

      <div className="space-y-2 mb-4">
        {options.map((option) => (
          <div
            key={option.id}
            onClick={() => allowMultiple ? toggleMulti(option) : setSelected(option.id)}
            className={`p-3 border rounded cursor-pointer transition-colors ${
              (allowMultiple ? multiSelected.includes(option.id) : selected === option.id)
                ? 'border-blue-500 bg-blue-50'
                : 'hover:bg-gray-50'
            }`}
          >
            <div className="font-medium">{option.label}</div>
            {option.description && (
              <div className="text-sm text-gray-500">{option.description}</div>
            )}
          </div>
        ))}
      </div>

      <div className="flex gap-2">
        <button
          onClick={handleSubmit}
          disabled={allowMultiple ? multiSelected.length === 0 : !selected}
          className="bg-blue-500 disabled:bg-gray-300 text-white px-4 py-2 rounded"
        >
          Continue
        </button>
        <button onClick={onCancel} className="border px-4 py-2 rounded">
          Cancel
        </button>
      </div>
    </div>
  );
}
```

---

## Step 5: Create Component Index

Export all components:

```js
// chat-ui/src/workflows/[WorkflowName]/components/index.js
import DatePicker from './DatePicker';
import ConfirmationCard from './ConfirmationCard';
import ContactForm from './ContactForm';
import OptionSelector from './OptionSelector';

const [WorkflowName]Components = {
  DatePicker,           // Key must match tools.yaml ui.component
  ConfirmationCard,
  ContactForm,
  OptionSelector,
};

export default [WorkflowName]Components;
```

---

## Step 6: Register in Workflow Registry

Update the main registry:

```js
// chat-ui/src/workflows/index.js
import GreenRoomComponents from './GreenRoom/components';
import [WorkflowName]Components from './[WorkflowName]/components';  // Add import

const WORKFLOW_REGISTRY = {
  GreenRoom: { components: GreenRoomComponents },
  [WorkflowName]: { components: [WorkflowName]Components },         // Add entry
};

export default WORKFLOW_REGISTRY;
```

---

## Step 7: Verify Setup

### Check 1: Component Export Names Match tools.yaml

```yaml
# In tools.yaml
ui:
  component: DatePicker  # Must match key in components/index.js
```

```js
// In components/index.js
const Components = {
  DatePicker,  // This key must match
};
```

### Check 2: Required Props Used

Every component must:
1. Receive `{ payload, onResponse, onCancel, eventId, ui_tool_id }`
2. Call `onResponse({ status, data, eventId, ui_tool_id })` with all four fields
3. Have Cancel button that calls `onCancel()`

### Check 3: Test in Browser

1. Start the chat-ui: `npm start` in chat-ui folder
2. Start a conversation with your workflow
3. Trigger the tool that uses your component
4. Verify it renders and submits correctly

---

## Step 8: Summary Template

```markdown
## UI Components Created

### Components
| Component | Purpose | Payload Fields | Returns |
|-----------|---------|----------------|---------|
| `DatePicker` | Select date | `message`, `min_date`, `max_date` | `selected_date` |
| `ConfirmationCard` | Confirm action | `title`, `message`, `details` | `confirmed` |

### Files Created
- ✅ `chat-ui/src/workflows/[WorkflowName]/components/DatePicker.js`
- ✅ `chat-ui/src/workflows/[WorkflowName]/components/ConfirmationCard.js`
- ✅ `chat-ui/src/workflows/[WorkflowName]/components/index.js`
- ✅ `chat-ui/src/workflows/index.js` (updated)

### Verification
- [ ] Component names match tools.yaml `ui.component` values
- [ ] All components exported from index.js
- [ ] Workflow registered in workflows/index.js
- [ ] Components render correctly in browser
```

---

## Troubleshooting

### Component doesn't render
1. Check component name in `tools.yaml` matches export key exactly
2. Verify workflow is in `chat-ui/src/workflows/index.js`
3. Check browser console for import errors

### onResponse not working
1. Ensure you're passing `eventId` and `ui_tool_id`
2. Check you're using `await` with `onResponse()`
3. Verify `status` is one of: `'success'`, `'cancelled'`, `'error'`

### Styling issues
1. Tailwind classes should work by default
2. Check if parent container has conflicting styles
3. Add explicit width/height if needed

