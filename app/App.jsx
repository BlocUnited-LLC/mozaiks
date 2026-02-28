import { MozaiksApp, WebSocketApiAdapter } from '@mozaiks/chat-ui';
import appConfig from '../app.json';

const apiAdapter = new WebSocketApiAdapter({ baseUrl: appConfig.apiUrl, wsUrl: appConfig.wsUrl });

export default function App() {
  return (
    <MozaiksApp
      appName={appConfig.appName}
      defaultAppId={appConfig.appId}
      defaultWorkflow={appConfig.defaultWorkflow}
      defaultUserId={appConfig.defaultUserId}
      apiAdapter={apiAdapter}
    />
  );
}

