/**
 * App.jsx — root of your mozaiks application
 *
 * ┌─────────────────────────────────────────────────────────────────────────┐
 * │  You should not need to change most of this file.                        │
 * │                                                                           │
 * │  Things you WILL customise:                                               │
 * │    - authAdapter    → connect your auth system (Supabase, Clerk, etc.)   │
 * │    - apiAdapter     → point at your backend URL                           │
 * │    - uiConfig       → app name, features, layout preferences              │
 * │    - workflows/     → add your own workflow UI components                 │
 * └─────────────────────────────────────────────────────────────────────────┘
 */

import React, { useCallback } from 'react';
import { BrowserRouter as Router } from 'react-router-dom';
import {
  ChatUIProvider,
  useChatUI,
  GlobalChatWidgetWrapper,
  ShellUIToolRenderer,
  NavigationProvider,
  BrandingProvider,
  RouteRenderer,
} from '@mozaiks/chat-ui';
import { initializeWorkflows } from '@chat-workflows/index';

// ── Demo / local-dev adapter ─────────────────────────────────────────────────
// Stubs all API and WebSocket calls so the ChatPage works without a backend.
// Replace with a real RestApiAdapter when your backend is ready.
// See: adapters/mockApiAdapter.js for details.
import mockApiAdapter from './adapters/mockApiAdapter';

// ── App-level config ──────────────────────────────────────────────────────────
// defaultAppId   — must match your backend app identifier.
//                  Use any string in dev; change when you connect a real backend.
// defaultWorkflow — the workflow that loads by default (matches orchestrator.yaml).
const uiConfig = {
  appName: 'My App',
  chat: {
    defaultAppId: 'demo-app',
    defaultWorkflow: 'hello_world',
    defaultUserId: 'local-dev-user',
  },
};

// ─── App content — renders the page routes ────────────────────────────────
// RouteRenderer handles page-level routing based on auth state.
// Add your own pages via the pages/ registry in @mozaiks/chat-ui.
const AppContent = () => {
  const { user } = useChatUI();
  return (
    <RouteRenderer
      isAuthenticated={!!user}
      onAuthRequired={(path) => console.log(`Auth required for: ${path}`)}
    />
  );
};

// ─── Root app ─────────────────────────────────────────────────────────────
export default function App() {
  // uiToolRenderer — called when the backend requests a UI input from the user
  // (e.g. a confirmation step, a form, a custom picker mid-workflow).
  // ShellUIToolRenderer handles built-in tool types out of the box.
  const renderUiTool = useCallback((event, onResponse, submitInputRequest, options = {}) => (
    <ShellUIToolRenderer
      event={event}
      onResponse={onResponse}
      submitInputRequest={submitInputRequest}
      onArtifactAction={options.onArtifactAction}
      actionStatusMap={options.actionStatusMap}
    />
  ), []);

  return (
    // BrandingProvider — loads brand.json (colors, fonts, logo)
    // configPath must match the file name in brands/public/ (served as /brand.json)
    <BrandingProvider configPath="/brand.json">
      {/* NavigationProvider — tracks page location for the chat widget */}
      <NavigationProvider>
        {/* ChatUIProvider — the core runtime: auth, API, streaming, workflows */}
        {/* workflowInitializer — registers your workflows */}
        {/* uiToolRenderer — handles mid-workflow UI */}
        {/* authAdapter={myAuthAdapter} — plug in your auth system */}
        {/* apiAdapter={myApiAdapter}   — plug in your API client  */}
        {/* uiConfig={{ appName: 'My App' }} — app-level config    */}
        <ChatUIProvider
          workflowInitializer={initializeWorkflows}
          uiToolRenderer={renderUiTool}
          apiAdapter={mockApiAdapter}
          uiConfig={uiConfig}
        >
          <Router>
            {/* GlobalChatWidgetWrapper — the persistent chat panel/widget */}
            <GlobalChatWidgetWrapper />
            {/* AppContent — your pages render here */}
            <AppContent />
          </Router>
        </ChatUIProvider>
      </NavigationProvider>
    </BrandingProvider>
  );
}
