export function findHeaderAction(headerConfig, actionId) {
  if (!headerConfig || typeof headerConfig !== 'object') return null;
  if (!Array.isArray(headerConfig.actions)) return null;
  if (typeof actionId !== 'string' || !actionId.trim()) return null;

  return (
    headerConfig.actions.find((action) => action && action.id === actionId) || null
  );
}

export default findHeaderAction;
