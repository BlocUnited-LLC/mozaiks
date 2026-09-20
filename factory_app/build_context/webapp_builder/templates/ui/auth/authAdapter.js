import { createAuthAdapter as createSharedAuthAdapter } from '@mozaiks/chat-ui/auth';

export function createAuthAdapter(options) {
  return createSharedAuthAdapter({ ...options, env: import.meta.env });
}
