/**
 * Core Bridge — HTTP client for mozaikscore application services
 *
 * Provides frontend access to mozaikscore endpoints (default port 8000):
 *   - Navigation config
 *   - Settings CRUD
 *   - Notifications
 *   - User profile
 *   - Subscription info
 *   - Admin APIs (users, analytics)
 *   - Health check
 *
 * Complements runtimeBridge.js which handles mozaiksai (port 8000) connections.
 *
 * @module @mozaiks/chat-ui/coreBridge
 */

import platform from './platform/index.js';

// ---------------------------------------------------------------------------
// Base URL resolution
// ---------------------------------------------------------------------------

function getCoreBaseUrl() {
  return platform.resolveHttpUrl({
    explicitUrl: import.meta.env.VITE_CORE_URL,
    // Default to 8000 because this repo commonly runs a unified backend on 8000.
    // Set VITE_CORE_URL or VITE_CORE_PORT to override for split deployments.
    port: import.meta.env.VITE_CORE_PORT || '8000',
  });
}

// Optional service capability flags. When an endpoint family is missing in the
// current backend, degrade once and avoid repeated failing requests.
let notificationsApiUnavailable = false;

// ---------------------------------------------------------------------------
// Auth header helpers
// ---------------------------------------------------------------------------

function getAccessToken() {
  return platform.getAccessToken();
}

function buildAuthHeaders(token) {
  const headers = { 'Content-Type': 'application/json' };
  const t = token || getAccessToken();
  if (t) {
    headers['Authorization'] = `Bearer ${t}`;
  }
  return headers;
}

// ---------------------------------------------------------------------------
// Fetch wrapper with auth
// ---------------------------------------------------------------------------

async function coreFetch(path, options = {}) {
  const url = `${getCoreBaseUrl()}${path}`;
  const { token, ...fetchOpts } = options;

  const response = await fetch(url, {
    ...fetchOpts,
    headers: {
      ...buildAuthHeaders(token),
      ...(fetchOpts.headers || {}),
    },
  });

  if (!response.ok) {
    const body = await response.text().catch(() => '');
    const err = new Error(`CoreBridge: ${response.status} ${response.statusText}`);
    err.status = response.status;
    err.body = body;
    throw err;
  }

  // 204 No Content
  if (response.status === 204) return null;

  return response.json();
}

// ---------------------------------------------------------------------------
// Navigation API
// ---------------------------------------------------------------------------

export async function fetchNavigation() {
  return coreFetch('/api/navigation');
}

export async function fetchAdminConfig() {
  return coreFetch('/api/admin-config');
}

// ---------------------------------------------------------------------------
// Settings API
// ---------------------------------------------------------------------------

export async function fetchSettingsConfig() {
  return coreFetch('/api/settings-config');
}

export async function fetchSettings(pluginName = null) {
  const qs = pluginName ? `?plugin_name=${encodeURIComponent(pluginName)}` : '';
  return coreFetch(`/api/settings${qs}`);
}

export async function saveSettings(pluginName, values) {
  return coreFetch('/api/settings', {
    method: 'PUT',
    body: JSON.stringify({ plugin_name: pluginName, values }),
  });
}

export async function resetSettings(pluginName) {
  return coreFetch(`/api/settings/${encodeURIComponent(pluginName)}`, {
    method: 'DELETE',
  });
}

// ---------------------------------------------------------------------------
// Notifications API
// ---------------------------------------------------------------------------

export async function fetchNotifications(limit = 20, skip = 0) {
  if (notificationsApiUnavailable) return { notifications: [], count: 0 };
  try {
    return await coreFetch(`/api/notifications?limit=${limit}&skip=${skip}`);
  } catch (err) {
    if (err?.status === 404) {
      notificationsApiUnavailable = true;
      return { notifications: [], count: 0 };
    }
    throw err;
  }
}

export async function fetchNotificationCount() {
  if (notificationsApiUnavailable) return { count: 0 };
  try {
    return await coreFetch('/api/notifications/count');
  } catch (err) {
    if (err?.status === 404) {
      notificationsApiUnavailable = true;
      return { count: 0 };
    }
    throw err;
  }
}

export async function markNotificationRead(notificationId) {
  if (notificationsApiUnavailable) return { success: false };
  return coreFetch(`/api/notifications/${encodeURIComponent(notificationId)}/read`, {
    method: 'PUT',
  });
}

export async function markAllNotificationsRead() {
  if (notificationsApiUnavailable) return { success: false };
  return coreFetch('/api/notifications/read-all', { method: 'PUT' });
}

export async function deleteNotification(notificationId) {
  if (notificationsApiUnavailable) return { success: false };
  return coreFetch(`/api/notifications/${encodeURIComponent(notificationId)}`, {
    method: 'DELETE',
  });
}

// ---------------------------------------------------------------------------
// Profile & Subscription API
// ---------------------------------------------------------------------------

export async function fetchUserProfile() {
  return coreFetch('/api/profile');
}

export async function fetchSubscription() {
  return coreFetch('/api/subscription');
}

// ---------------------------------------------------------------------------
// Admin API
// ---------------------------------------------------------------------------

export async function adminListUsers(page = 1, limit = 20) {
  return coreFetch(`/__mozaiks/admin/users?page=${page}&limit=${limit}`);
}

export async function adminGetUser(userId) {
  return coreFetch(`/__mozaiks/admin/users/${encodeURIComponent(userId)}`);
}

export async function adminUserAction(userId, action) {
  return coreFetch('/__mozaiks/admin/users/action', {
    method: 'POST',
    body: JSON.stringify({ user_id: userId, action }),
  });
}

export async function adminGetAnalytics() {
  return coreFetch('/__mozaiks/admin/analytics');
}

export async function adminGetAppMetadata() {
  return coreFetch('/__mozaiks/admin/app');
}

// ---------------------------------------------------------------------------
// Health
// ---------------------------------------------------------------------------

export async function checkCoreHealth() {
  return coreFetch('/health');
}

// ---------------------------------------------------------------------------
// Modules — dynamic module discovery & execution
// ---------------------------------------------------------------------------

export async function fetchAvailableModules() {
  return coreFetch('/api/available-modules');
}

export async function executeModule(moduleName, data = {}) {
  return coreFetch(`/api/execute/${encodeURIComponent(moduleName)}`, {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export async function checkModuleAccess(moduleName) {
  return coreFetch(`/api/check-module-access/${encodeURIComponent(moduleName)}`);
}

export async function fetchModuleSettings(moduleName) {
  return coreFetch(`/api/module-settings/${encodeURIComponent(moduleName)}`);
}

export async function saveModuleSettings(moduleName, settings) {
  return coreFetch(`/api/module-settings/${encodeURIComponent(moduleName)}`, {
    method: 'POST',
    body: JSON.stringify(settings),
  });
}

// ---------------------------------------------------------------------------
// Theme
// ---------------------------------------------------------------------------

export async function fetchThemeConfig() {
  return coreFetch('/api/theme-config');
}

export async function fetchCurrentTheme() {
  return coreFetch('/api/current-theme');
}

export async function changeTheme(themeName) {
  return coreFetch('/api/change-theme', {
    method: 'POST',
    body: JSON.stringify({ theme_name: themeName }),
  });
}

// ---------------------------------------------------------------------------
// Profile
// ---------------------------------------------------------------------------

export async function updateUserProfile(fields) {
  return coreFetch('/api/update-profile', {
    method: 'POST',
    body: JSON.stringify(fields),
  });
}

// ---------------------------------------------------------------------------
// Default export — grouped namespace
// ---------------------------------------------------------------------------

const coreBridge = {
  getCoreBaseUrl,
  coreFetch,
  // Navigation
  fetchNavigation,
  fetchAdminConfig,
  // Settings
  fetchSettingsConfig,
  fetchSettings,
  saveSettings,
  resetSettings,
  // Notifications
  fetchNotifications,
  fetchNotificationCount,
  markNotificationRead,
  markAllNotificationsRead,
  deleteNotification,
  // Profile & Subscription
  fetchUserProfile,
  fetchSubscription,
  updateUserProfile,
  // Modules
  fetchAvailableModules,
  executeModule,
  checkModuleAccess,
  fetchModuleSettings,
  saveModuleSettings,
  // Theme
  fetchThemeConfig,
  fetchCurrentTheme,
  changeTheme,
  // Admin
  adminListUsers,
  adminGetUser,
  adminUserAction,
  adminGetAnalytics,
  adminGetAppMetadata,
  // Health
  checkCoreHealth,
};

export default coreBridge;
