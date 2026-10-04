import { moduleAction } from './moduleApi.js';

export async function socialAction(action, params = {}) {
  const result = await moduleAction('user_posts', action, params);
  const mutation = ['create_post', 'delete_post', 'react_to_post', 'add_comment', 'delete_comment'].includes(action);
  if (!result || result.success === false || (mutation && result.success !== true)) {
    throw new Error(result?.error || 'The action could not be completed.');
  }
  return result;
}

export function errorMessage(error, fallback) {
  if (error?.status === 401) return 'Your session has ended. Sign in again to continue.';
  if (error?.status === 403) return 'You do not have permission to do that.';
  if (error?.status >= 500 || error instanceof TypeError) return fallback;
  return error?.message || fallback;
}

export function postPath(postId) {
  return `/community/${encodeURIComponent(postId)}`;
}

export function authorLabel(authorId, user) {
  if (authorId === user?.id) return user.displayName || user.name || 'You';
  return 'Community member';
}

export function formatDate(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleString(undefined, {
    month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit',
  });
}
