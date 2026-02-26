const NAV_CACHE_PREFIX = 'mozaiks.nav_cache.';

const isObject = (value) => value && typeof value === 'object' && !Array.isArray(value);

const stableStringify = (value) => {
  if (value === null || value === undefined) return 'null';
  if (typeof value === 'string') return JSON.stringify(value);
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  if (Array.isArray(value)) {
    return `[${value.map(stableStringify).join(',')}]`;
  }
  if (isObject(value)) {
    const keys = Object.keys(value).sort();
    return `{${keys.map((key) => `${JSON.stringify(key)}:${stableStringify(value[key])}`).join(',')}}`;
  }
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
};

const hashString = (input) => {
  let hash = 5381;
  for (let i = 0; i < input.length; i += 1) {
    hash = ((hash << 5) + hash) ^ input.charCodeAt(i);
  }
  return (hash >>> 0).toString(36);
};

export const buildNavigationCacheKey = (workflow, input) => {
  const safeWorkflow = workflow || 'unknown';
  const normalizedInput = input === undefined ? {} : input;
  const inputHash = hashString(stableStringify(normalizedInput));
  return `artifact:${safeWorkflow}:${inputHash}`;
};

export const readNavigationCache = (workflow, input) => {
  if (!workflow) return null;
  const key = `${NAV_CACHE_PREFIX}${buildNavigationCacheKey(workflow, input)}`;
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return null;
    const payload = JSON.parse(raw);
    if (payload?.expires_at && Date.now() > payload.expires_at) {
      localStorage.removeItem(key);
      return null;
    }
    return { key, ...payload };
  } catch (err) {
    console.warn('[navigationCache] Failed to read cache', err);
    return null;
  }
};

export const writeNavigationCache = (workflow, input, artifact, ttlSeconds) => {
  if (!workflow || !artifact) return null;
  const ttlMs = Number.isFinite(ttlSeconds) ? Math.max(0, ttlSeconds) * 1000 : 0;
  if (!ttlMs) return null;

  const key = `${NAV_CACHE_PREFIX}${buildNavigationCacheKey(workflow, input)}`;
  const now = Date.now();
  const payload = {
    artifact,
    workflow,
    input: input ?? null,
    cached_at: now,
    expires_at: now + ttlMs,
  };
  try {
    localStorage.setItem(key, JSON.stringify(payload));
    return { key, ...payload };
  } catch (err) {
    console.warn('[navigationCache] Failed to write cache', err);
    return null;
  }
};
