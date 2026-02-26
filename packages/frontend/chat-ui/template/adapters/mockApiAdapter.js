/**
 * mockApiAdapter.js — template demo adapter
 *
 * Stubs all API and WebSocket calls so the ChatPage renders and functions
 * without a running backend. This is what makes `npm run dev` work out of
 * the box for any new mozaiks app.
 *
 * ── How it works ──────────────────────────────────────────────────────────
 *  • startChat()                 → returns a stable fake chat_id
 *  • createWebSocketConnection() → fires onOpen after 400ms (simulates connect)
 *  • sendMessageToWorkflow()     → echoes the user message back as an agent reply
 *  • All other calls             → return safe empty values (no errors thrown)
 *
 * ── Replacing with a real backend ─────────────────────────────────────────
 *  When your backend is ready, replace the `apiAdapter` prop in App.jsx:
 *
 *    import { RestApiAdapter } from '@mozaiks/chat-ui';
 *    const apiAdapter = new RestApiAdapter({ baseUrl: import.meta.env.VITE_API_URL });
 *
 *  Then remove this file.
 */

// Stable session ID so the chat persists across hot-reloads in dev.
const MOCK_CHAT_ID = `demo-${Math.random().toString(36).slice(2, 8)}`;

// Shared reference so sendMessageToWorkflow can deliver replies through
// the WebSocket message path (same channel ChatPage listens on).
let _onMessage = null;

/**
 * Simulate a streamed agent reply through the WebSocket callback.
 * The event shape matches what ChatPage's handleIncoming() expects.
 */
function _simulateReply(userText) {
  if (!_onMessage) return;

  const reply =
    `You said: "${userText}"\n\n` +
    `This is a **demo response** — no backend is connected.\n\n` +
    `To wire up real AI, replace \`mockApiAdapter\` in \`App.jsx\` ` +
    `with a \`RestApiAdapter\` pointing at your mozaiks backend.`;

  setTimeout(() => {
    if (_onMessage) {
      _onMessage({
        type: 'chat.text',
        agent: 'Demo Agent',
        content: reply,
        is_visual: true,
        is_structured_capable: false,
      });
    }
  }, 700);
}

// ─── The adapter object ────────────────────────────────────────────────────

const mockApiAdapter = {
  // ── Chat lifecycle ─────────────────────────────────────────────────────

  async startChat(_appId, _workflowName, _userId) {
    console.log('[mockApi] startChat → demo chat_id:', MOCK_CHAT_ID);
    return { success: true, chat_id: MOCK_CHAT_ID };
  },

  // ── Ask-mode (general / free-form chat) ──────────────────────────────

  async listGeneralChats() {
    return { sessions: [] };
  },

  async fetchGeneralChatTranscript(_appId, chatId) {
    return { chat_id: chatId, messages: [], last_sequence: 0 };
  },

  // ── Message history ───────────────────────────────────────────────────

  async getMessageHistory() {
    return [];
  },

  // ── Sending messages ──────────────────────────────────────────────────
  //
  // In the real adapter, sendMessageToWorkflow sends the payload to the
  // backend and the reply arrives via the WebSocket onMessage callback.
  // Here we echo the message back through the same path.

  async sendMessageToWorkflow(message) {
    const text =
      typeof message === 'string'
        ? message
        : (message?.content ?? JSON.stringify(message));
    _simulateReply(text);
    return { success: true };
  },

  // ── WebSocket (simulated in-process) ─────────────────────────────────

  createWebSocketConnection(_appId, _userId, callbacks = {}) {
    // Store the onMessage handler so sendMessageToWorkflow can use it.
    _onMessage = callbacks.onMessage ?? null;

    // Simulate the connection establishing after a short delay.
    const timer = setTimeout(() => {
      console.log('[mockApi] WebSocket "connected"');
      if (callbacks.onOpen) callbacks.onOpen();
    }, 400);

    return {
      close() {
        clearTimeout(timer);
        _onMessage = null;
        if (callbacks.onClose) callbacks.onClose();
      },
      // No-op send — mock responses come from sendMessageToWorkflow via
      // _simulateReply, not from raw WebSocket frames.
      send() {},
    };
  },

  // ── Transport discovery ───────────────────────────────────────────────
  //
  // Returning null causes ChatPage to fall back to 'websocket' transport,
  // which then calls createWebSocketConnection above.

  async getWorkflowTransport() {
    return null;
  },

  // ── Generic HTTP ──────────────────────────────────────────────────────

  async get() {
    return null;
  },

  // ── File upload ───────────────────────────────────────────────────────

  async uploadFile() {
    return { success: false, error: 'File upload is not available in demo mode.' };
  },
};

export default mockApiAdapter;
