import platform from "../../platform/index.js";
import { authFetch } from "../../adapters/api.js";

const hasAccessToken = () => {
  try {
    return Boolean(platform.getAccessToken());
  } catch {
    return false;
  }
};

export const fetchNotificationCount = async ({ signal } = {}) => {
  if (!hasAccessToken()) return null;

  const response = await authFetch("/api/notifications/count", {
    signal,
    headers: { Accept: "application/json" },
  });
  if (!response.ok) return null;
  return response.json();
};

export const clearNotifications = async () => {
  if (!hasAccessToken()) return null;

  return authFetch("/api/notifications", {
    method: "DELETE",
    headers: { Accept: "application/json" },
  });
};
