import { useState, useEffect } from 'react';
import {
  MozaiksApp,
  WebSocketApiAdapter,
  createKeycloakAuthAdapter,
  mockApiAdapter,
  createMockAuthAdapter,
} from '@mozaiks/chat-ui';
import {
  registerComponent,
  hasComponent,
} from '@mozaiks/chat-ui/registry/componentRegistry';
import appConfig from '../platform/app.json';
import AdminPortal from '../platform/modules/admin_portal/ui/AdminPortal';
import LineupBoard from '../platform/modules/lineup_board/ui/LineupBoard';
import ShowArchive from '../platform/modules/show_archive/ui/ShowArchive';

// Explicit mock mode: set VITE_MOCK_MODE=true in .env to bypass auth/backend
const USE_MOCK = import.meta.env.VITE_MOCK_MODE === 'true';
const DEV_AUTH_MODE = appConfig?.dev?.authMode;
const USE_CONFIGURED_MOCK = DEV_AUTH_MODE === 'mock';
const FALLBACK_TO_MOCK = appConfig?.dev?.fallbackToMockAuth === true;
const SHOULD_USE_MOCK = USE_MOCK || USE_CONFIGURED_MOCK || appConfig?.auth?.provider === 'mock';

const apiAdapter = USE_MOCK
  ? mockApiAdapter
  : new WebSocketApiAdapter({ baseUrl: appConfig.apiUrl, wsUrl: appConfig.wsUrl });

// Fallback registration: if module auto-discovery misses these components in dev,
// we still keep routes renderable.
const FALLBACK_PLATFORM_COMPONENTS = {
  AdminPortal,
  LineupBoard,
  ShowArchive,
};

for (const [componentName, component] of Object.entries(FALLBACK_PLATFORM_COMPONENTS)) {
  if (!hasComponent(componentName)) {
    registerComponent(componentName, component, {
      description: 'Platform module component (app fallback registration)',
    });
  }
}

export default function App() {
  const [authAdapter, setAuthAdapter] = useState(null);
  const [authReady, setAuthReady] = useState(false);
  const [authError, setAuthError] = useState(null);

  useEffect(() => {
    // Mock mode: skip Keycloak entirely
    if (SHOULD_USE_MOCK) {
      console.log('[App] Mock auth mode enabled');
      createMockAuthAdapter({ auth: appConfig.auth, dev: appConfig.dev }).then((adapter) => {
        setAuthAdapter(adapter);
        setAuthReady(true);
      });
      return;
    }

    // Production/dev mode: use real Keycloak
    // Pass auth + dev config from app.json so keycloakAuth has everything it needs.
    createKeycloakAuthAdapter({ auth: appConfig.auth, dev: appConfig.dev })
      .then((adapter) => {
        setAuthAdapter(adapter);
        setAuthReady(true);
      })
      .catch((err) => {
        console.error('[App] Keycloak init failed:', err);
        if (FALLBACK_TO_MOCK) {
          console.warn('[App] Falling back to mock auth adapter (dev.fallbackToMockAuth=true)');
          createMockAuthAdapter({ auth: appConfig.auth, dev: appConfig.dev })
            .then((adapter) => {
              setAuthAdapter(adapter);
              setAuthReady(true);
            })
            .catch((mockErr) => setAuthError(mockErr));
          return;
        }
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
          The app is configured to use Keycloak authentication (via <code style={{ color: '#22d3ee' }}>app.json</code>),
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
            Set <code style={{ fontSize: '0.875rem' }}>dev.authMode="mock"</code> in <code>platform/app.json</code><br />
            or add <code style={{ fontSize: '0.875rem' }}>VITE_MOCK_MODE=true</code> to <code>.env</code>
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
