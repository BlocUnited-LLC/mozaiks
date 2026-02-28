const PLACEHOLDER_REGEX = /\{\{\s*([^}]+)\s*\}\}/g;

export const getValueByPath = (obj, path) => {
  if (!obj || !path) return undefined;
  const parts = String(path).split('.').map(p => p.trim()).filter(Boolean);
  return parts.reduce((acc, key) => (acc != null ? acc[key] : undefined), obj);
};

export const interpolateString = (value, context) => {
  if (typeof value !== 'string') return value;
  const trimmed = value.trim();
  const exact = trimmed.match(/^\{\{\s*([^}]+)\s*\}\}$/);
  if (exact) {
    return getValueByPath(context, exact[1].trim());
  }
  return value.replace(PLACEHOLDER_REGEX, (_, key) => {
    const resolved = getValueByPath(context, key.trim());
    if (resolved === undefined || resolved === null) return '';
    return String(resolved);
  });
};

export const interpolateParams = (params, context) => {
  if (Array.isArray(params)) {
    return params.map(item => interpolateParams(item, context));
  }
  if (params && typeof params === 'object') {
    return Object.keys(params).reduce((acc, key) => {
      acc[key] = interpolateParams(params[key], context);
      return acc;
    }, {});
  }
  return interpolateString(params, context);
};

export const deriveArtifactId = (payload, fallbackId = null) => {
  if (!payload || typeof payload !== 'object') return fallbackId;
  return payload.artifact_id || payload.artifactId || payload.id || fallbackId;
};

const cloneDeep = (value) => {
  if (typeof structuredClone === 'function') {
    try {
      return structuredClone(value);
    } catch {}
  }
  try {
    return JSON.parse(JSON.stringify(value));
  } catch {
    return value;
  }
};

const decodeJsonPointer = (segment) =>
  segment.replace(/~1/g, '/').replace(/~0/g, '~');

const ensureContainer = (parent, key, nextKeyIsIndex) => {
  if (parent[key] === undefined) {
    parent[key] = nextKeyIsIndex ? [] : {};
  }
  return parent[key];
};

export const applyJsonPatch = (target, patchOps = []) => {
  if (!Array.isArray(patchOps)) return target;
  let result = cloneDeep(target ?? {});

  for (const op of patchOps) {
    if (!op || typeof op !== 'object') continue;
    const operation = op.op;
    const path = typeof op.path === 'string' ? op.path : '';
    const segments = path.split('/').slice(1).map(decodeJsonPointer);

    if (!segments.length) {
      if (operation === 'remove') {
        result = undefined;
      } else if (operation === 'add' || operation === 'replace') {
        result = cloneDeep(op.value);
      }
      continue;
    }

    let parent = result;
    for (let i = 0; i < segments.length - 1; i += 1) {
      const key = segments[i];
      const nextKey = segments[i + 1];
      const nextKeyIsIndex = nextKey === '-' || !Number.isNaN(Number(nextKey));
      parent = ensureContainer(parent, key, nextKeyIsIndex);
    }

    const last = segments[segments.length - 1];
    if (Array.isArray(parent)) {
      const index = last === '-' ? parent.length : Number(last);
      if (Number.isNaN(index)) continue;
      if (operation === 'remove') {
        parent.splice(index, 1);
      } else if (operation === 'add') {
        parent.splice(index, 0, op.value);
      } else if (operation === 'replace') {
        parent[index] = op.value;
      }
    } else if (parent && typeof parent === 'object') {
      if (operation === 'remove') {
        delete parent[last];
      } else if (operation === 'add' || operation === 'replace') {
        parent[last] = op.value;
      }
    }
  }

  return result;
};

export const applyArtifactUpdate = (payload, update) => {
  if (!update) return payload;
  const mode = update.mode || (Array.isArray(update) ? 'patch' : 'replace');
  if (mode === 'replace') {
    return update.payload !== undefined ? update.payload : update;
  }
  if (mode === 'patch') {
    const patchOps = update.payload || update.patch || update;
    return applyJsonPatch(payload, patchOps);
  }
  return payload;
};

export const applyOptimisticUpdate = (payload, optimistic) => {
  if (!optimistic) return payload;
  if (Array.isArray(optimistic) || optimistic.mode || optimistic.patch) {
    return applyArtifactUpdate(payload, optimistic);
  }
  if (optimistic && typeof optimistic === 'object') {
    return { ...(payload || {}), ...optimistic };
  }
  return payload;
};
