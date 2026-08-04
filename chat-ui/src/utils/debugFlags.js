export const debugFlag = (key) => {
  try {
    return ['1', 'true', 'on', 'yes'].includes((localStorage.getItem(key) || '').toLowerCase());
  } catch {
    return false;
  }
};

export const uiEventDebugEnabled = () => (
  debugFlag('mozaiks.debug_ui_events') || debugFlag('mozaiks.debug_pipeline')
);
