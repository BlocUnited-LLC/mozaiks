import {
  MozaiksApp,
  WebSocketApiAdapter,
  componentRegistry,
} from '@mozaiks/chat-ui';
import { registerStudioComponents } from '@studio/extensions';

// Product UI extension — resolved at build time via @platform/extensions alias.
// Default local app root: mozaiks-platform/app/ui/index.js (hosted-only surfaces).
// Switch by changing PLATFORM_PATH in .env. Do not import product components here.
import { register } from '@platform/extensions';

// Register product UI extensions (Mozaiks App hosted surfaces).
// Studio components are registered separately below via registerStudioComponents.
// Core substrate components are always loaded from coreComponents.js.
register(componentRegistry.registerComponent.bind(componentRegistry));

const hostMode = (
  import.meta.env.VITE_MOZAIKS_HOST ||
  import.meta.env.MOZAIKS_HOST ||
  ''
).toLowerCase();

// Register Studio management components when running in studio or mozaiks host mode.
// Studio is the shared management interface. Mozaiks App extends Studio —
// it does not get a separate set of management components.
if (hostMode === 'studio' || hostMode === 'mozaiks') {
  registerStudioComponents(componentRegistry.registerComponent.bind(componentRegistry));
}

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
