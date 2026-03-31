import {
  MozaiksApp,
  WebSocketApiAdapter,
} from '@mozaiks/chat-ui';
import appConfig from '../platform/app.json';

// Simple API adapter connecting to the backend
const apiAdapter = new WebSocketApiAdapter({
  baseUrl: appConfig.apiUrl || 'http://localhost:8000',
  wsUrl: appConfig.wsUrl || 'ws://localhost:8000',
});

// Simple mock auth adapter (no real auth required for demo)
const mockAuthAdapter = {
  isAuthenticated: () => true,
  // getCurrentUser is called by ChatUIContext during initialization
  getCurrentUser: () => Promise.resolve({
    id: 'demo-user',
    firstName: 'Demo',
    lastName: 'User',
    email: 'demo@example.com',
  }),
  getToken: () => Promise.resolve('demo-token'),
  login: () => Promise.resolve(),
  logout: () => Promise.resolve(),
  onAuthStateChange: (callback) => {
    // Immediately notify with the demo user
    callback({
      id: 'demo-user',
      firstName: 'Demo',
      lastName: 'User',
      email: 'demo@example.com',
    });
    // Return unsubscribe function
    return () => {};
  },
};

export default function App() {
  return (
    <MozaiksApp
      appName={appConfig.appName || 'Mozaiks'}
      defaultAppId={appConfig.appId || 'demo'}
      apiAdapter={apiAdapter}
      authAdapter={mockAuthAdapter}
    />
  );
}
