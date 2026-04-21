// =============================================================================
// @mozaiks/chat-ui — Main Entry Point
// =============================================================================
//
// SIMPLE USAGE:
//   import { ChatWidget, WorkflowChat, useMozaiks } from '@mozaiks/chat-ui';
//
// =============================================================================

import './styles/chatShell.css';

// -----------------------------------------------------------------------------
// CORE COMPONENTS
// -----------------------------------------------------------------------------

// ChatWidget: Standalone floating chat overlay
export { default as ChatWidget } from './components/chat/ChatWidget';

// WorkflowChat: Embeddable workflow chat component
export { default as WorkflowChat } from './components/chat/WorkflowChat';

// ChatInterface: Presentational chat component
export { default as ChatInterface } from './components/chat/ChatInterface';

// PersistentChatWidget: Context-aware widget (for full apps)
export { default as PersistentChatWidget } from './components/chat/PersistentChatWidget';

// MozaiksApp: Full application shell with routing and navigation
export { default as MozaiksApp } from './app/MozaiksApp';

// -----------------------------------------------------------------------------
// HOOKS
// -----------------------------------------------------------------------------

// useMozaiks: Trigger workflows from UI
export { useMozaiks } from './hooks/useMozaiks';

// useChatWebSocket: Low-level WebSocket management
export { useChatWebSocket } from './pages/hooks';

// -----------------------------------------------------------------------------
// API ADAPTERS
// -----------------------------------------------------------------------------

export { ApiAdapter, WebSocketApiAdapter, RestApiAdapter, appApi } from './adapters/api';

// -----------------------------------------------------------------------------
// CONFIGURATION
// -----------------------------------------------------------------------------

export { createAppConfig, resolveAppManifest, deriveWsUrl } from './platform/appManifest';

// -----------------------------------------------------------------------------
// SUPPORTING COMPONENTS (for building custom UIs)
// -----------------------------------------------------------------------------

export { default as ArtifactPanel } from './components/chat/ArtifactPanel';
export { default as ChatBubble } from './components/chat/ChatBubble';
export { default as ChatMessage } from './components/chat/ChatMessage';
export { default as ChatOverlay } from './components/chat/ChatOverlay';
export { default as ConnectionStatus } from './components/chat/ConnectionStatus';
export { default as MobileArtifactDrawer } from './components/chat/MobileArtifactDrawer';

// Error handling
export { default as ErrorBoundary } from './components/ErrorBoundary';

// Context
export * from './context/ChatUIContext';

// UI tool rendering
export { default as UIToolRenderer } from './core/ui/UIToolRenderer';
export { dynamicUIHandler } from './core/dynamicUIHandler';

// Component registry
export { default as componentRegistry } from './registry/componentRegistry';

// Side-effect: registers core components
import './registry/coreComponents';

// -----------------------------------------------------------------------------
// THEMING & BRANDING
// -----------------------------------------------------------------------------

// Theme hook
export { default as useTheme } from './styles/useTheme';

// Branding provider
export { default as BrandingProvider } from './providers/BrandingProvider';

// Theme utilities
export * as themeProvider from './styles/themeProvider';
export { loadBrand } from './theme/loadBrand';

// CSS Variables (import in your app)
// import '@mozaiks/chat-ui/styles/mozaiks-variables.css';

// -----------------------------------------------------------------------------
// EMBEDDABLE WIDGET
// -----------------------------------------------------------------------------

// MozaiksEmbed: Standalone widget for embedding into existing apps
export { MozaiksEmbed, applyThemeToContainer } from './embed/index.js';

// ---------------------------------------------------------------------------
// APP UI RENDERER
// ---------------------------------------------------------------------------

export { PageRenderer, PageFrame } from './ui/page-renderer/index.js';
