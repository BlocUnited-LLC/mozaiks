function formatActionLabel(actionId) {
  const parts = String(actionId || '')
    .replace(/-/g, '_')
    .split('_')
    .filter(Boolean);
  if (parts.length === 0) {
    return 'Action';
  }
  return parts.map((part) => part.charAt(0).toUpperCase() + part.slice(1)).join(' ');
}


export function normalizeActions(rawActions, fallbackActions = []) {
  const source = Array.isArray(rawActions) && rawActions.length > 0 ? rawActions : fallbackActions;
  return source
    .filter((action) => action && typeof action === 'object')
    .map((action, index) => ({
      id: String(action.id || `action_${index + 1}`),
      label: String(action.label || action.title || formatActionLabel(action.id || `action_${index + 1}`)),
      description: action.description ? String(action.description) : '',
      variant: String(action.variant || 'secondary'),
      approved: typeof action.approved === 'boolean' ? action.approved : undefined,
      closes: action.closes !== false,
      payload: action.payload && typeof action.payload === 'object' ? action.payload : {},
      payload_schema: action.payload_schema && typeof action.payload_schema === 'object' ? action.payload_schema : {},
    }));
}


export function normalizePrimitiveActions(payload = {}, fallbackActions = []) {
  const explicitActions = Array.isArray(payload?.actions) ? payload.actions : [];
  const contractActions = Array.isArray(payload?.ui_contract?.actions_schema)
    ? payload.ui_contract.actions_schema
    : [];
  const source = explicitActions.length > 0
    ? explicitActions
    : (contractActions.length > 0 ? contractActions : fallbackActions);
  return normalizeActions(source, fallbackActions);
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


export function getPrimaryPrimitiveAction(payload = {}, fallbackAction = null) {
  const fallbackActions = fallbackAction ? [fallbackAction] : [];
  return normalizePrimitiveActions(payload, fallbackActions)[0] || null;
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
