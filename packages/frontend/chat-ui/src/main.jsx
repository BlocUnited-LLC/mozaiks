/**
 * Minimal Demo App for mozaiks chat-ui
 *
 * This is a development/testing entry point that wires up @mozaiks/chat-ui
 * primitives with mock adapters. No platform dependencies (auth, subscription,
 * profile, notifications).
 *
 * For a full product app, wire these primitives into your app shell and auth/runtime adapters.
 */
import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { ChatUIProvider } from './context/ChatUIContext';
import ChatPage from './pages/ChatPage';

/**
 * Minimal mock auth adapter for development.
 * Replace with a real adapter (e.g. CoreAuthAdapter from runtimeBridge.js)
 * when connecting to a live backend.
 */
const mockAuthAdapter = {
  getToken: () => Promise.resolve('dev-token'),
  getUser: () => Promise.resolve({ id: 'dev-user', name: 'Developer' }),
  isAuthenticated: () => true,
  login: () => {},
  logout: () => {},
};

/**
 * Minimal mock API adapter for development.
 */
const mockApiAdapter = {
  baseUrl: import.meta.env.VITE_API_URL || 'http://localhost:8080',
  fetch: (path, options = {}) =>
    fetch(`${mockApiAdapter.baseUrl}${path}`, {
      ...options,
      headers: {
        'Content-Type': 'application/json',
        Authorization: 'Bearer dev-token',
        ...(options.headers || {}),
      },
    }),
};

function DemoApp() {
  return (
    <BrowserRouter>
      <ChatUIProvider
        authAdapter={mockAuthAdapter}
        apiAdapter={mockApiAdapter}
      >
        <div style={{ height: '100vh', display: 'flex', flexDirection: 'column' }}>
          <header style={{
            padding: '8px 16px',
            borderBottom: '1px solid var(--color-border, #333)',
            background: 'var(--color-surface, #1a1a1a)',
            color: 'var(--color-text, #e0e0e0)',
            fontSize: '14px',
          }}>
            mozaiks — dev shell
          </header>
          <main style={{ flex: 1, overflow: 'hidden' }}>
            <Routes>
              <Route path="/" element={<ChatPage />} />
              <Route path="/chat" element={<ChatPage />} />
              <Route path="/chat/:chatId" element={<ChatPage />} />
            </Routes>
          </main>
        </div>
      </ChatUIProvider>
    </BrowserRouter>
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <DemoApp />
  </React.StrictMode>
);
