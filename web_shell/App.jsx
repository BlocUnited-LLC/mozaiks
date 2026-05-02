import {
  MozaiksApp,
  WebSocketApiAdapter,
  componentRegistry,
} from '@mozaiks/chat-ui';

// Active app UI barrel — resolved at build time via @platform/extensions.
// The selected app root owns custom route components, including any
// management surfaces it declares in its route manifest.
import { register } from '@platform/extensions';

// Register active app UI extensions once.
// Core substrate components are always loaded from coreComponents.js.
register(componentRegistry.registerComponent.bind(componentRegistry));

// ── API adapter ────────────────────────────────────────────────────────────
// apiUrl and wsUrl come from the platform's app.json or env vars.
// Vite proxies /api and /ws to apiUrl during dev (see vite.config.js).
const apiAdapter = new WebSocketApiAdapter({
  baseUrl: import.meta.env.VITE_API_URL || 'http://localhost:8000',
  wsUrl:   import.meta.env.VITE_WS_URL  || 'ws://localhost:8000',
});

// ── Auth adapter ───────────────────────────────────────────────────────────
// Development mock — replaced by Keycloak adapter in production.
// Auth configuration is declared in the active app root app.json.
const mockAuthAdapter = {
  isAuthenticated:    () => true,
  getCurrentUser:     () => Promise.resolve({ id: 'demo-user', firstName: 'Demo', lastName: 'User', email: 'demo@example.com', roles: ['admin', 'user'] }),
  getToken:           () => Promise.resolve('demo-token'),
  login:              () => Promise.resolve(),
  logout:             () => Promise.resolve(),
  onAuthStateChange:  (callback) => {
    callback({ id: 'demo-user', firstName: 'Demo', lastName: 'User', email: 'demo@example.com', roles: ['admin', 'user'] });
    return () => {};
  },
};

export default function App() {
  return (
    <MozaiksApp
      apiAdapter={apiAdapter}
      authAdapter={mockAuthAdapter}
    />
  );
}
