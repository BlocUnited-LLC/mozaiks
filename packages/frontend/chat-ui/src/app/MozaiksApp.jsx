import React, { useCallback } from 'react';
import { BrowserRouter as Router } from 'react-router-dom';
import { ChatUIProvider, useChatUI } from '../context/ChatUIContext';
import GlobalChatWidgetWrapper from '../widget/GlobalChatWidgetWrapper';
import ShellUIToolRenderer from '../core/ui/ShellUIToolRenderer';
import NavigationProvider from '../providers/NavigationProvider';
import BrandingProvider from '../providers/BrandingProvider';
import RouteRenderer from '../components/RouteRenderer';
import { initializeWorkflows } from '@chat-workflows/index';

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
 *   defaultWorkflow {string}   Workflow name loaded by default (must match backend orchestrator.yaml)
 *   defaultAppId    {string}   App identifier sent to the backend
 *   apiAdapter      {object}   API adapter — use mockApiAdapter for local dev, RestApiAdapter for production
 *   authAdapter     {object}   Optional auth adapter
 *   uiConfig        {object}   Full uiConfig override (replaces individual props when supplied)
 *   children        {node}     Override the default page renderer
 */
export default function MozaiksApp({
  appName = 'My App',
  defaultWorkflow = '',
  defaultAppId = 'demo-app',
  defaultUserId = 'local-dev-user',
  apiAdapter,
  authAdapter,
  uiConfig: uiConfigProp,
  children,
}) {
  const uiConfig = uiConfigProp || {
    appName,
    chat: { defaultAppId, defaultWorkflow, defaultUserId },
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
            <GlobalChatWidgetWrapper />
            {children || <AppShell />}
          </Router>
        </ChatUIProvider>
      </NavigationProvider>
    </BrandingProvider>
  );
}
