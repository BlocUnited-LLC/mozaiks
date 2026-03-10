// Chat UI Components
export { default as ArtifactPanel } from './components/chat/ArtifactPanel';
export { default as ChatBubble } from './components/chat/ChatBubble';
export { default as ChatInterface } from './components/chat/ChatInterface';
export { default as ChatMessage } from './components/chat/ChatMessage';
export { default as ChatOverlay } from './components/chat/ChatOverlay';
export { default as ConnectionStatus } from './components/chat/ConnectionStatus';
export { default as FluidChatLayout } from './components/chat/FluidChatLayout';
export { default as MobileArtifactDrawer } from './components/chat/MobileArtifactDrawer';
export { default as PersistentChatWidget } from './components/chat/PersistentChatWidget';
export { default as WorkflowCompletion } from './components/chat/WorkflowCompletion';

// Layout Components
export { default as Header } from './components/layout/Header';
export { default as Footer } from './components/layout/Footer';
export { default as RouteRenderer } from './components/RouteRenderer';

// Widget
export { default as GlobalChatWidgetWrapper } from './widget/GlobalChatWidgetWrapper';

// Context
export * from './context/ChatUIContext';

// Hooks
export * from './hooks/useWidgetMode';

// Core utilities
export {
  getValueByPath,
  interpolateString,
  interpolateParams,
  deriveArtifactId,
  applyJsonPatch,
  applyArtifactUpdate,
  applyOptimisticUpdate,
} from './core/actions/actionUtils';

// Core runtime
export { default as ShellUIToolRenderer } from './core/ui/ShellUIToolRenderer';
export { default as UIToolRenderer } from './core/ui/UIToolRenderer';
export { default as UserInputRequest } from './core/ui/UserInputRequest';
export { dynamicUIHandler } from './core/dynamicUIHandler';
export { default as eventDispatcher, handleEvent, registerEventHandler } from './core/eventDispatcher';
export { default as WorkflowUIRouter } from './core/WorkflowUIRouter';

// Adapters
export { ApiAdapter, WebSocketApiAdapter, RestApiAdapter, appApi } from './adapters/api';
export * from './adapters/auth';
export { KeycloakAuthAdapter, createKeycloakAuthAdapter } from './adapters/keycloakAuth';

// Config
export { default as config } from './config';
export { default as workflowConfig } from './config/workflowConfig';

// Providers
export { default as BrandingProvider } from './providers/BrandingProvider';
export { default as NavigationProvider } from './providers/NavigationProvider';

// Registry
export { default as componentRegistry } from './registry/componentRegistry';

// App shell — top-level application wrapper
export { default as MozaiksApp } from './app/MozaiksApp';

// Development adapters — stubs API/WS/Auth calls without a backend
export { default as mockApiAdapter } from './adapters/mockApiAdapter';
export { default as mockAuthAdapter, createMockAuthAdapter } from './adapters/mockAuthAdapter';

// Side-effect: registers core pages (ChatPage) in the component registry.
// Platform modules (AdminPortal etc.) are registered via @modules auto-discovery.
import './registry/coreComponents';

// Pages
export { default as ChatPage } from './pages/ChatPage';
// AdminPortal is now a platform module — import from platform/modules/admin_portal/ui/

// AdminPortal extensibility API — section registry, auth hooks, shared primitives
export {
  registerAdminSection,
  getAdminSection,
  getAllAdminSections,
  unregisterAdminSection,
  // Legacy aliases
  registerDashboardSection,
  getDashboardSection,
  getAllDashboardSections,
  unregisterDashboardSection,
  useIsAdmin,
  useHasRole,
  useAuthConfig,
  Card as AdminCard,
  Stat as AdminStat,
  ProgressBar as AdminProgressBar,
  // Original names — for platform module UIs that import from '@mozaiks/chat-ui'
  Card,
  Stat,
  ProgressBar,
} from './adminPortalRegistry';

// Admin bridge functions (used by platform/modules/admin_portal/ui)
export { adminListUsers, adminGetAnalytics } from './coreBridge';


// Navigation
export { readNavigationCache, writeNavigationCache } from './navigation/navigationCache';
export { default as useNavigationActions } from './navigation/useNavigationActions';

// Styles
export * as themeProvider from './styles/themeProvider';
export { default as useTheme } from './styles/useTheme';
export * from './styles/artifactDesignSystem';

// Primitives
export * from './primitives';

// State
export * from './state/uiSurfaceReducer';
