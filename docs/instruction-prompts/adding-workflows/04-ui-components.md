# Instruction Prompt: Creating UI Components

**Task:** Create React components for interactive UI tools

**Complexity:** Medium (React/JavaScript code)

---

## Context for AI Agent

You are helping a user create React components for their MozaiksAI workflow. These components render in the chat when UI tools are triggered, collect user input, and send responses back to the Python tool.

---

## Step 1: Understand Requirements

Ask the user:

1. **"What UI components do you need?"**
   Examples: date picker, form, confirmation card, product selector

2. **"What data will each component receive?"**
   This becomes the `payload` prop

3. **"What data should each component return?"**
   This is what `onResponse()` sends back

---

## Step 2: Set Up Folder Structure

Ensure the structure exists:

```powershell
# Create workflow components folder
New-Item -ItemType Directory -Force -Path "chat-ui/src/workflows/[WorkflowName]/components"
```

Expected structure:
```
chat-ui/src/workflows/
├── index.js                         # Workflow registry
└── [WorkflowName]/
    └── components/
        ├── index.js                 # Component exports
        ├── [Component1].js
        └── [Component2].js
```

---

## Step 3: Create Component Template

For each component:

```jsx
// chat-ui/src/workflows/[WorkflowName]/components/[ComponentName].js
import React from 'react';

export default function [ComponentName]({ payload, onResponse, onCancel, eventId, ui_tool_id }) {
  // State for user input
  const [value, setValue] = React.useState(payload?.initialValue ?? '');

  // Handle successful submission
  async function handleSubmit() {
    await onResponse({
      status: 'success',
      data: {
        // Return the collected data
        value: value,
      },
      eventId,      // Required - pass through
      ui_tool_id,   // Required - pass through
    });
  }

  // Handle cancellation
  async function handleCancel() {
    await onCancel();  // or call onResponse with status: 'cancelled'
  }

  return (
    <div className="p-4 border rounded-xl bg-white shadow-sm">
      {/* Component UI */}
      <p className="mb-4">{payload?.message ?? 'Default message'}</p>

      {/* Input elements */}
      <input
        type="text"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        className="w-full border rounded p-2 mb-4"
      />

      {/* Action buttons */}
      <div className="flex gap-2">
        <button
          onClick={handleSubmit}
          className="bg-blue-500 hover:bg-blue-600 text-white px-4 py-2 rounded"
        >
          Submit
        </button>
        <button
          onClick={handleCancel}
          className="border hover:bg-gray-100 px-4 py-2 rounded"
        >
          Cancel
        </button>
      </div>
    </div>
  );
}
```

---

## Step 4: Component Examples

### Date Picker Component

```jsx
// chat-ui/src/workflows/[WorkflowName]/components/DatePicker.js
import React from 'react';

export default function DatePicker({ payload, onResponse, onCancel, eventId, ui_tool_id }) {
  const [selectedDate, setSelectedDate] = React.useState('');

  // Filter to only allowed dates if provided
  const availableDates = payload?.available_dates ?? [];
  const minDate = payload?.min_date ?? '';
  const maxDate = payload?.max_date ?? '';

  async function handleSelect() {
    if (!selectedDate) return;

    await onResponse({
      status: 'success',
      data: { selected_date: selectedDate },
      eventId,
      ui_tool_id,
    });
  }

  return (
    <div className="p-4 border rounded-xl bg-white">
      <h3 className="font-semibold mb-2">{payload?.title ?? 'Select a Date'}</h3>
      <p className="text-gray-600 mb-4">{payload?.message ?? 'Choose your preferred date'}</p>

      <input
        type="date"
        value={selectedDate}
        onChange={(e) => setSelectedDate(e.target.value)}
        min={minDate}
        max={maxDate}
        className="w-full border rounded p-2 mb-4"
      />

      {availableDates.length > 0 && (
        <p className="text-sm text-gray-500 mb-4">
          Available: {availableDates.join(', ')}
        </p>
      )}

      <div className="flex gap-2">
        <button
          onClick={handleSelect}
          disabled={!selectedDate}
          className="bg-blue-500 disabled:bg-gray-300 text-white px-4 py-2 rounded"
        >
          Confirm Date
        </button>
        <button onClick={onCancel} className="border px-4 py-2 rounded">
          Cancel
        </button>
      </div>
    </div>
  );
}
```

### Confirmation Card Component

```jsx
// chat-ui/src/workflows/[WorkflowName]/components/ConfirmationCard.js
import React from 'react';

export default function ConfirmationCard({ payload, onResponse, onCancel, eventId, ui_tool_id }) {
  const [isLoading, setIsLoading] = React.useState(false);

  async function handleConfirm() {
    setIsLoading(true);
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
    <div className="p-4 border rounded-xl bg-white">
      <h3 className="font-bold text-lg mb-2">
        {payload?.title ?? 'Please Confirm'}
      </h3>

      <p className="text-gray-700 mb-4">
        {payload?.message ?? 'Are you sure you want to proceed?'}
      </p>

      {/* Optional details list */}
      {payload?.details && (
        <div className="bg-gray-50 p-3 rounded mb-4">
          <ul className="text-sm space-y-1">
            {payload.details.map((item, index) => (
              <li key={index} className="flex items-center">
                <span className="mr-2">•</span>
                {item}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Warning message if present */}
      {payload?.warning && (
        <div className="bg-yellow-50 border border-yellow-200 p-3 rounded mb-4 text-sm">
          ⚠️ {payload.warning}
        </div>
      )}

      <div className="flex gap-3">
        <button
          onClick={handleConfirm}
          disabled={isLoading}
          className="bg-green-500 hover:bg-green-600 disabled:bg-gray-300 text-white px-6 py-2 rounded font-medium"
        >
          {isLoading ? 'Processing...' : (payload?.confirmText ?? 'Yes, Confirm')}
        </button>
        <button
          onClick={handleDecline}
          disabled={isLoading}
          className="bg-red-500 hover:bg-red-600 text-white px-6 py-2 rounded font-medium"
        >
          {payload?.declineText ?? 'No, Cancel'}
        </button>
      </div>
    </div>
  );
}
```

### Multi-Field Form Component

```jsx
// chat-ui/src/workflows/[WorkflowName]/components/ContactForm.js
import React from 'react';

export default function ContactForm({ payload, onResponse, onCancel, eventId, ui_tool_id }) {
  const [formData, setFormData] = React.useState({
    name: payload?.defaults?.name ?? '',
    email: payload?.defaults?.email ?? '',
    phone: payload?.defaults?.phone ?? '',
    message: '',
  });
  const [errors, setErrors] = React.useState({});

  function updateField(field, value) {
    setFormData(prev => ({ ...prev, [field]: value }));
    // Clear error when user types
    if (errors[field]) {
      setErrors(prev => ({ ...prev, [field]: null }));
    }
  }

  function validate() {
    const newErrors = {};
    if (!formData.name.trim()) newErrors.name = 'Name is required';
    if (!formData.email.trim()) newErrors.email = 'Email is required';
    if (formData.email && !formData.email.includes('@')) {
      newErrors.email = 'Invalid email format';
    }
    setErrors(newErrors);
    return Object.keys(newErrors).length === 0;
  }

  async function handleSubmit(e) {
    e.preventDefault();
    if (!validate()) return;

    await onResponse({
      status: 'success',
      data: formData,
      eventId,
      ui_tool_id,
    });
  }

  return (
    <form onSubmit={handleSubmit} className="p-4 border rounded-xl bg-white">
      <h3 className="font-bold text-lg mb-4">
        {payload?.title ?? 'Contact Information'}
      </h3>

      <div className="space-y-4">
        {/* Name field */}
        <div>
          <label className="block text-sm font-medium mb-1">Name *</label>
          <input
            type="text"
            value={formData.name}
            onChange={(e) => updateField('name', e.target.value)}
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
import HelloWorldComponents from './HelloWorld/components';
import [WorkflowName]Components from './[WorkflowName]/components';  // Add import

const WORKFLOW_REGISTRY = {
  HelloWorld: { components: HelloWorldComponents },
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
