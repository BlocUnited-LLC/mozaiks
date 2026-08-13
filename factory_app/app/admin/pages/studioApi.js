export const API_BASE =
  (typeof import.meta !== 'undefined' && import.meta.env?.VITE_API_URL) || '';

export function getStudioAccessToken() {
  try {
    if (typeof window !== 'undefined' && window.mozaiksAuth?.getAccessToken) {
      return window.mozaiksAuth.getAccessToken();
    }
    if (typeof window !== 'undefined' && window.mozaiksAuth?.token) {
      return window.mozaiksAuth.token;
    }
  } catch {
    // Fall through to storage-backed lookup.
  }

  try {
    if (typeof sessionStorage !== 'undefined') {
      const token = sessionStorage.getItem('mozaiks_access_token');
      if (token) return token;
    }
  } catch {
    // Ignore unavailable storage.
  }

  try {
    if (typeof localStorage !== 'undefined') {
      return (
        localStorage.getItem('mozaiks_access_token') ||
        localStorage.getItem('chatui_token') ||
        localStorage.getItem('access_token')
      );
    }
  } catch {
    // Ignore unavailable storage.
  }

  return null;
}

export function studioAuthHeaders(headers = {}) {
  const token = getStudioAccessToken();
  return {
    ...headers,
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
  };
}

export function studioFetch(path, options = {}) {
  const headers = studioAuthHeaders(options.headers || {});
  return fetch(`${API_BASE}${path}`, {
    ...options,
    headers,
  });
}

export async function studioModuleAction(moduleName, actionName, input = {}) {
  const response = await studioFetch(`/api/modules/${moduleName}/${actionName}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: JSON.stringify(input || {}),
  });
  if (!response.ok) {
    throw new Error(`${moduleName}.${actionName} ${response.status}`);
  }
  return response.json();
}
