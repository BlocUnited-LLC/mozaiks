import platform from "../../platform/index.js";

const jsonHeaders = () => {
  const headers = { Accept: "application/json" };
  let token = null;
  try {
    token = platform.getAccessToken();
  } catch {
    token = null;
  }
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }
  return headers;
};

export const fetchNotificationCount = async ({ signal } = {}) => {
  const headers = jsonHeaders();
  if (!headers.Authorization) return null;

  const response = await fetch("/api/notifications/count", {
    signal,
    headers,
  });
  if (!response.ok) return null;
  return response.json();
};

export const clearNotifications = async () => {
  const headers = jsonHeaders();
  if (!headers.Authorization) return null;

  return fetch("/api/notifications", {
    method: "DELETE",
    headers,
  });
};
