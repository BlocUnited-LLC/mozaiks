export const resolveArtifactType = (payload) => {
  if (!payload || typeof payload !== 'object') return null;
  const direct = payload.artifact_type || payload.artifactType || payload.type || null;
  if (direct) return direct;
  const nested = payload.data && typeof payload.data === 'object'
    ? (payload.data.artifact_type || payload.data.artifactType || payload.data.type || null)
    : null;
  return nested || null;
};

export const isCoreArtifact = (payload) => {
  const type = resolveArtifactType(payload);
  return typeof type === 'string' && type.startsWith('core.');
};

export const getArtifactValue = (payload, key, fallback = undefined) => {
  if (!payload || typeof payload !== 'object') return fallback;
  if (payload[key] !== undefined) return payload[key];
  if (payload.data && typeof payload.data === 'object' && payload.data[key] !== undefined) {
    return payload.data[key];
  }
  return fallback;
};

export const getArtifactArray = (payload, key) => {
  const value = getArtifactValue(payload, key, []);
  return Array.isArray(value) ? value : [];
};

export const normalizeActions = (actions) =>
  Array.isArray(actions) ? actions.filter(Boolean).filter(action => action.tool) : [];
