export function normalizeActions(rawActions, fallbackActions = []) {
  const source = Array.isArray(rawActions) && rawActions.length > 0 ? rawActions : fallbackActions;
  return source
    .filter((action) => action && typeof action === 'object')
    .map((action, index) => ({
      id: String(action.id || `action_${index + 1}`),
      label: String(action.label || action.title || action.id || `Action ${index + 1}`),
      variant: String(action.variant || 'secondary'),
      approved: typeof action.approved === 'boolean' ? action.approved : undefined,
      closes: action.closes !== false,
      payload: action.payload && typeof action.payload === 'object' ? action.payload : {},
    }));
}


export async function sendPrimitiveResponse(onResponse, action, extra = {}) {
  if (!onResponse) {
    return;
  }

  const payload = {
    status: 'submitted',
    action: action.id,
    ...action.payload,
    ...extra,
  };

  if (typeof action.approved === 'boolean' && payload.approved === undefined) {
    payload.approved = action.approved;
  }

  await onResponse(payload);
}


export function normalizeOptions(rawOptions) {
  if (!Array.isArray(rawOptions)) {
    return [];
  }
  return rawOptions
    .filter((option) => option && typeof option === 'object')
    .map((option, index) => ({
      id: String(option.id || option.value || `option_${index + 1}`),
      value: option.value ?? option.id ?? `option_${index + 1}`,
      label: String(option.label || option.title || option.value || option.id || `Option ${index + 1}`),
      description: option.description ? String(option.description) : '',
      disabled: Boolean(option.disabled),
    }));
}


export function normalizeSummaryItems(rawItems) {
  if (!Array.isArray(rawItems)) {
    return [];
  }
  return rawItems
    .filter((item) => item !== null && item !== undefined)
    .map((item, index) => {
      if (typeof item === 'string') {
        return { id: `item_${index + 1}`, label: `Item ${index + 1}`, value: item };
      }
      if (typeof item === 'object') {
        return {
          id: String(item.id || `item_${index + 1}`),
          label: String(item.label || item.title || `Item ${index + 1}`),
          value: item.value ?? item.summary ?? item.description ?? '',
        };
      }
      return { id: `item_${index + 1}`, label: `Item ${index + 1}`, value: String(item) };
    });
}
