import React, { useCallback } from 'react';
import { BrowserRouter as Router } from 'react-router-dom';
import { ChatUIProvider, useChatUI } from '../context/ChatUIContext';
import GlobalChatWidgetWrapper from '../widget/GlobalChatWidgetWrapper';
import ShellUIToolRenderer from '../core/ui/ShellUIToolRenderer';
import NavigationProvider from '../providers/NavigationProvider';
import BrandingProvider from '../providers/BrandingProvider';
import RouteRenderer from '../components/RouteRenderer';
import { initializeWorkflows } from '@chat-workflows/index';
import ConfigValidationOverlay from '../config/ConfigValidationOverlay';

/**
 * Inner shell — must render inside ChatUIProvider to consume its context.
 */
function AppShell({ onAuthRequired }) {
  const { user } = useChatUI();
  return (
    <RouteRenderer
      isAuthenticated={!!user}
      onAuthRequired={onAuthRequired || ((path) => console.log(`[mozaiks] auth required: ${path}`))}
    />
  );
}

/**
 * MozaiksApp — complete application shell.
 *
 * Wraps BrandingProvider → NavigationProvider → ChatUIProvider → Router.
 * Workflows are auto-registered from the @chat-workflows alias (no manual registry needed).
 *
 * Props:
 *   appName         {string}   App display name
 *   defaultAppId    {string}   App identifier sent to the backend
 *   apiAdapter      {object}   API adapter — use mockApiAdapter for local dev, RestApiAdapter for production
 *   authAdapter     {object}   Auth adapter (Keycloak by default, mock for VITE_MOCK_MODE)
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
    (event, onResponse, submitInputRequest, options = {}) => (
      <ShellUIToolRenderer
        event={event}
        onResponse={onResponse}
        submitInputRequest={submitInputRequest}
        onArtifactAction={options.onArtifactAction}
        actionStatusMap={options.actionStatusMap}
      />
    ),
    []
  );

  // Config validation overlay (dev-mode in-browser error/warning banner)
  // Replaces console-only logging — founders see issues without opening DevTools

  return (
    <BrandingProvider configPath="/brand.json">
      <NavigationProvider>
        <ChatUIProvider
          workflowInitializer={initializeWorkflows}
          uiToolRenderer={renderUiTool}
          apiAdapter={apiAdapter}
          authAdapter={authAdapter}
          uiConfig={uiConfig}
        >
          <Router>
            <ConfigValidationOverlay />
            <GlobalChatWidgetWrapper />
            {children || <AppShell />}
          </Router>
        </ChatUIProvider>
      </NavigationProvider>
    </BrandingProvider>
  );
}
