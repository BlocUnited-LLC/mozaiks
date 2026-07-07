import { useCallback } from 'react';
import { BrowserRouter as Router } from 'react-router-dom';
import { ChatUIProvider, useChatUI } from '../context/ChatUIContext';
import GlobalChatWidgetWrapper from '../widget/GlobalChatWidgetWrapper';
import ShellUIToolRenderer from '../core/ui/ShellUIToolRenderer';
import NavigationProvider from '../providers/NavigationProvider';
import RouteRenderer from '../components/RouteRenderer';
import { initializeWorkflows } from '../@chat-workflows/index.js';
import { registerComponent } from '../registry/componentRegistry.js';
import ConfigValidationOverlay from '../config/ConfigValidationOverlay';

/**
 * Inner shell — must render inside ChatUIProvider to consume its context.
 */
function AppShell({ onAuthRequired }) {
  const { user } = useChatUI();
  return (
    <RouteRenderer
      isAuthenticated={!!user}
    />
  );
}

/**
 * MozaiksApp — complete application shell.
 *
 * Wraps NavigationProvider → ChatUIProvider → Router.
 * Workflows are auto-registered from the @chat-workflows alias (no manual registry needed).
 *
 * Props:
 *   appName         {string}   App display name
 *   defaultAppId    {string}   App identifier sent to the backend
 *   apiAdapter      {object}   API adapter — use mockApiAdapter for local dev, RestApiAdapter for production
 *   authAdapter     {object}   Auth adapter — host-provided (any OIDC provider, or mock for VITE_MOCK_MODE)
 *   uiConfig        {object}   Full uiConfig override (replaces individual props when supplied)
 *   children        {node}     Override the default page renderer
 */
export default function MozaiksApp({
  appName = 'My App',
  defaultAppId = 'demo-app',
  apiAdapter,
  authAdapter,
  uiConfig: uiConfigProp,
  children,
}) {
  const uiConfig = uiConfigProp || {
    appName,
    chat: { defaultAppId },
  };

  const renderUiTool = useCallback(
    (event, onResponse, options = {}) => (
      <ShellUIToolRenderer
        event={event}
        onResponse={onResponse}
        onArtifactAction={options.onArtifactAction}
        actionStatusMap={options.actionStatusMap}
      />
    ),
    []
  );

  // Config validation overlay (dev-mode in-browser error/warning banner)
  // Replaces console-only logging — founders see issues without opening DevTools

  const moduleInitializer = useCallback(() => {
    initializeWorkflows(registerComponent);
  }, []);

  return (
    <NavigationProvider>
      <ChatUIProvider
        workflowInitializer={moduleInitializer}
        uiToolRenderer={renderUiTool}
        apiAdapter={apiAdapter}
        authAdapter={authAdapter}
        uiConfig={uiConfig}
      >
        <Router future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
          <ConfigValidationOverlay />
          <GlobalChatWidgetWrapper />
          {children || <AppShell />}
        </Router>
      </ChatUIProvider>
    </NavigationProvider>
  );
}
