/**
 * Minimal Demo App for mozaiks chat-ui
 *
 * This is a development/testing entry point that wires up @mozaiks/chat-ui
 * primitives with mock adapters. No platform dependencies (auth, subscription,
 * profile, notifications).
 *
 * For a full product app, wire these primitives into your app shell and auth/runtime adapters.
 */
import './demo.css';
import './styles/chatShell.css';

import React from 'react';
import ReactDOM from 'react-dom/client';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { ChatUIProvider } from './context/ChatUIContext';
import { NavigationProvider } from './providers/NavigationProvider';
import BrandingProvider from './providers/BrandingProvider';
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
  baseUrl: import.meta.env.VITE_API_URL || 'http://localhost:8000',
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
      <BrandingProvider>
        <NavigationProvider>
          <ChatUIProvider
            authAdapter={mockAuthAdapter}
            apiAdapter={mockApiAdapter}
          >
            <main style={{ height: '100vh', overflow: 'hidden' }}>
              <Routes>
                <Route path="/" element={<Navigate to="/chat/demo-app" replace />} />
                <Route path="/chat" element={<Navigate to="/chat/demo-app" replace />} />
                <Route path="/chat/:chatId" element={<ChatPage />} />
              </Routes>
            </main>
          </ChatUIProvider>
        </NavigationProvider>
      </BrandingProvider>
    </BrowserRouter>
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <DemoApp />
  </React.StrictMode>
);
