/**
 * Portable chat-ui core entrypoint.
 *
 * This surface is intended for non-web hosts such as React Native.
 * It excludes DOM/renderer code, router integrations, and browser-only
 * auth adapters while preserving the transport, state, config, and
 * platform injection APIs.
 */

export { configurePlatform, platform, default as defaultPlatform } from '../platform/index.js';

export { ChatUIProvider, useChatUI } from '../context/ChatUIContext';

export { useConversation, useArtifacts, useChatWebSocket } from '../pages/hooks';
export { useCoreWebSocket } from '../hooks/useCoreWebSocket';

export { ApiAdapter, WebSocketApiAdapter, RestApiAdapter, appApi } from '../adapters/api';

export { default as services } from '../services';
export { default as config } from '../config';
export { default as workflowConfig } from '../config/workflowConfig';

export {
  buildNavigationCacheKey,
  readNavigationCache,
  writeNavigationCache,
} from '../navigation/navigationCache';

export {
  createInitialSurfaceState,
  uiSurfaceReducer,
  mapSurfaceEventToAction,
} from '../state/uiSurfaceReducer';

export {
  getValueByPath,
  interpolateString,
  interpolateParams,
  deriveArtifactId,
  applyJsonPatch,
  applyArtifactUpdate,
  applyOptimisticUpdate,
} from '../core/actions/actionUtils';

export { DynamicUIHandler, dynamicUIHandler } from '../core/dynamicUIHandler';

export { NavigationProvider, useNavigation } from '../providers/NavigationProvider';
