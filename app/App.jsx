import { useState, useEffect } from 'react';
import { MozaiksApp, WebSocketApiAdapter, createKeycloakAuthAdapter } from '@mozaiks/chat-ui';
import appConfig from '../app.json';

const apiAdapter = new WebSocketApiAdapter({ baseUrl: appConfig.apiUrl, wsUrl: appConfig.wsUrl });

export default function App() {
  const [authAdapter, setAuthAdapter] = useState(null);
  const [authReady, setAuthReady] = useState(false);
  const [authError, setAuthError] = useState(null);

  useEffect(() => {
    // Keycloak is always-on. It ships with the stack.
    // To skip auth in local dev, set AUTH_ENABLED=false in .env
    // (backend skips JWT validation; frontend detects via /auth.json absence).
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

  // Keycloak unreachable — show error, not a silent bypass
  if (authError) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100vh', fontFamily: 'system-ui, sans-serif', flexDirection: 'column', gap: '1rem', padding: '2rem', textAlign: 'center' }}>
        <h1 style={{ fontSize: '1.5rem', margin: 0 }}>Authentication Unavailable</h1>
        <p style={{ color: '#888', maxWidth: '28rem' }}>
          Could not connect to Keycloak. Make sure it is running
          (<code>docker compose up keycloak</code>) and <code>auth.json</code> is correct.
        </p>
        <button onClick={() => window.location.reload()} style={{ padding: '0.5rem 1.5rem', cursor: 'pointer' }}>
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
      defaultWorkflow={appConfig.defaultWorkflow}
      defaultUserId={appConfig.defaultUserId}
      apiAdapter={apiAdapter}
      authAdapter={authAdapter}
    />
  );
}

