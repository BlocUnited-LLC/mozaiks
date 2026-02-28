/**
 * mockApiAdapter — development stub adapter
 *
 * Stubs all API and WebSocket calls so the app renders without a backend.
 * This is what makes `npm run dev` work out of the box.
 *
 * When your backend is ready, replace the `apiAdapter` prop in App.jsx:
 *
 *   import { RestApiAdapter } from '@mozaiks/chat-ui';
 *   const apiAdapter = new RestApiAdapter({ baseUrl: import.meta.env.VITE_API_URL });
 */

const MOCK_CHAT_ID = `demo-${Math.random().toString(36).slice(2, 8)}`;
let _onMessage = null;

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

const mockApiAdapter = {
  async startChat(_appId, _workflowName, _userId) {
    console.log('[mockApi] startChat → demo chat_id:', MOCK_CHAT_ID);
    return { success: true, chat_id: MOCK_CHAT_ID };
  },

  async listGeneralChats()                    { return { sessions: [] }; },
  async fetchGeneralChatTranscript(_, chatId) { return { chat_id: chatId, messages: [], last_sequence: 0 }; },
  async getMessageHistory()                   { return []; },

  async sendMessageToWorkflow(message) {
    const text = typeof message === 'string' ? message : (message?.content ?? JSON.stringify(message));
    _simulateReply(text);
    return { success: true };
  },

  createWebSocketConnection(_appId, _userId, callbacks = {}) {
    _onMessage = callbacks.onMessage ?? null;
    const timer = setTimeout(() => {
      console.log('[mockApi] WebSocket "connected"');
      if (callbacks.onOpen) callbacks.onOpen();
    }, 400);
    return {
      close() { clearTimeout(timer); _onMessage = null; if (callbacks.onClose) callbacks.onClose(); },
      send() {},
    };
  },

  async getWorkflowTransport() { return null; },
  async get()                  { return null; },
  async uploadFile()           { return { success: false, error: 'File upload not available in demo mode.' }; },
};

export default mockApiAdapter;
