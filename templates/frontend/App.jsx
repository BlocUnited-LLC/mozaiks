import { MozaiksApp, mockApiAdapter } from '@mozaiks/chat-ui';
import appConfig from '../app.json';

// ─────────────────────────────────────────────────────────────────────────────
// All app-specific config lives in app.json — edit that file, not this one.
//
// When your backend is ready, swap mockApiAdapter for a real one:
//
//   import { RestApiAdapter } from '@mozaiks/chat-ui';
//   const apiAdapter = new RestApiAdapter({ baseUrl: appConfig.apiUrl });
// ─────────────────────────────────────────────────────────────────────────────

export default function App() {
  return (
    <MozaiksApp
      appName={appConfig.appName}
      defaultAppId={appConfig.appId}
      defaultWorkflow={appConfig.defaultWorkflow}
      apiAdapter={mockApiAdapter}
    />
  );
}

