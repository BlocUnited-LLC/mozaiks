import { useState, useEffect } from 'react';
import {
  MozaiksApp,
  WebSocketApiAdapter,
  createKeycloakAuthAdapter,
  mockApiAdapter,
  createMockAuthAdapter,
} from '@mozaiks/chat-ui';
import appConfig from './app.json';

// Explicit mock mode: set VITE_MOCK_MODE=true in .env to bypass auth/backend
const USE_MOCK = import.meta.env.VITE_MOCK_MODE === 'true';

const apiAdapter = USE_MOCK
  ? mockApiAdapter
  : new WebSocketApiAdapter({ baseUrl: appConfig.apiUrl, wsUrl: appConfig.wsUrl });

export default function App() {
  const [authAdapter, setAuthAdapter] = useState(null);
  const [authReady, setAuthReady] = useState(false);
  const [authError, setAuthError] = useState(null);

  useEffect(() => {
    // Mock mode: skip Keycloak entirely
    if (USE_MOCK) {
      console.log('[App] Mock mode enabled');
      createMockAuthAdapter().then((adapter) => {
        setAuthAdapter(adapter);
        setAuthReady(true);
      });
      return;
    }

    // Production/dev mode: use real Keycloak
    createKeycloakAuthAdapter()
      .then((adapter) => {
        setAuthAdapter(adapter);
        setAuthReady(true);
      })
      .catch((err) => {
        console.error('[App] Keycloak init failed:', err);
        setAuthError(err);
      });

    return () => {
      if (authAdapter?.destroy) authAdapter.destroy();
    };
  }, []);

  // Keycloak unavailable - show clear instructions
  if (authError) {
    return (
      <div style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        height: '100vh',
        fontFamily: 'system-ui, sans-serif',
        flexDirection: 'column',
        gap: '1.5rem',
        padding: '2rem',
        textAlign: 'center',
        background: '#0f172a',
        color: '#e2e8f0',
      }}>
        <h1 style={{ fontSize: '1.5rem', margin: 0, color: '#f59e0b' }}>
          Keycloak Not Available
        </h1>
        <p style={{ color: '#94a3b8', maxWidth: '32rem', lineHeight: 1.6 }}>
          The app is configured to use Keycloak authentication (via <code style={{ color: '#22d3ee' }}>auth.json</code>),
          but the Keycloak server isn&apos;t running.
        </p>
        <div style={{
          background: '#1e293b',
          padding: '1.5rem',
          borderRadius: '0.75rem',
          textAlign: 'left',
          maxWidth: '28rem',
          width: '100%',
        }}>
          <p style={{ margin: '0 0 1rem', fontWeight: 600 }}>Options:</p>
          <p style={{ margin: '0 0 0.75rem', color: '#94a3b8' }}>
            <strong style={{ color: '#22d3ee' }}>1. Start Keycloak:</strong><br />
            <code style={{ fontSize: '0.875rem' }}>docker compose up keycloak</code>
          </p>
          <p style={{ margin: 0, color: '#94a3b8' }}>
            <strong style={{ color: '#22d3ee' }}>2. Use Mock Mode</strong> (UI testing only):<br />
            Add <code style={{ fontSize: '0.875rem' }}>VITE_MOCK_MODE=true</code> to <code>.env</code>
          </p>
        </div>
        <button
          onClick={() => window.location.reload()}
          style={{
            padding: '0.75rem 2rem',
            cursor: 'pointer',
            background: '#3b82f6',
            color: '#fff',
            border: 'none',
            borderRadius: '0.5rem',
            fontSize: '1rem',
          }}
        >
          Retry
        </button>
      </div>
    );
  }

  if (!authReady) return null;

  return (
    <MozaiksApp
      appName={appConfig.appName}
      defaultAppId={appConfig.appId}
      apiAdapter={apiAdapter}
      authAdapter={authAdapter}
    />
  );
}
