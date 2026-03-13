import { useState, useRef, useEffect, useCallback } from 'react';
import {
  applyArtifactUpdate,
  applyJsonPatch,
  applyOptimisticUpdate,
  deriveArtifactId,
} from '../../core/actions/actionUtils';
import platform from '../../platform/index.js';

const LOCAL_STORAGE_KEY = 'mozaiks.current_chat_id';

/**
 * Hook for managing artifact state, caching, and updates.
 *
 * Extracts artifact management logic from ChatPage.
 *
 * @param {Object} options
 * @param {string} options.chatId - Current chat session ID
 * @param {string} options.workflowName - Current workflow name
 * @param {Function} options.setCurrentArtifactContext - Context setter
 * @param {boolean} options.isMobileView - Whether in mobile view
 * @returns {Object} Artifact state and methods
 */
export function useArtifacts({
  chatId,
  workflowName,
  setCurrentArtifactContext,
  isMobileView,
}) {
  const [currentArtifactMessages, setCurrentArtifactMessages] = useState([]);
  const [isPanelOpen, setIsPanelOpen] = useState(false);
  const [mobileDrawerState, setMobileDrawerState] = useState('peek');
  const [hasUnseenArtifact, setHasUnseenArtifact] = useState(false);
  const [actionStatusMap, setActionStatusMap] = useState({});

  // Refs
  const currentArtifactMessagesRef = useRef([]);
  const lastArtifactEventRef = useRef(null);
  const artifactRestoredOnceRef = useRef(false);
  const artifactCacheValidRef = useRef(false);
  const optimisticSnapshotsRef = useRef(new Map());
  const viewArtifactSnapshotRef = useRef(null);

  // Keep ref in sync
  useEffect(() => {
    currentArtifactMessagesRef.current = currentArtifactMessages;
  }, [currentArtifactMessages]);

  // Update context when artifact messages change
  useEffect(() => {
    if (typeof setCurrentArtifactContext !== 'function') return;

    if (!currentArtifactMessages || currentArtifactMessages.length === 0) {
      setCurrentArtifactContext(null);
      return;
    }

    const artifactMsg =
      currentArtifactMessages.find((m) => m?.uiToolEvent?.ui_tool_id) ||
      currentArtifactMessages[0];
    const uiToolEvent = artifactMsg?.uiToolEvent;

    if (!uiToolEvent) {
      setCurrentArtifactContext(null);
      return;
    }

    setCurrentArtifactContext({
      id: uiToolEvent.eventId || artifactMsg.id || null,
      type: uiToolEvent.ui_tool_id,
      payload: uiToolEvent.payload || null,
      artifact_id: deriveArtifactId(
        uiToolEvent.payload,
        uiToolEvent.eventId || artifactMsg.id || null
      ),
      chat_id: chatId,
      workflow_name: workflowName,
    });
  }, [currentArtifactMessages, chatId, workflowName, setCurrentArtifactContext]);

  // Persist artifact to platform.storage
  useEffect(() => {
    if (!chatId) return;
    if (!Array.isArray(currentArtifactMessages) || currentArtifactMessages.length === 0) return;

    const artifactMsg =
      currentArtifactMessages.find((m) => m?.uiToolEvent?.payload) ||
      currentArtifactMessages[0];
    const uiToolEvent = artifactMsg?.uiToolEvent;
    if (!uiToolEvent) return;

    const payload = uiToolEvent.payload || {};
    const displayMode =
      uiToolEvent.display || payload.display || payload.mode || 'artifact';
    if (displayMode !== 'artifact') return;

    const artifactPayload = {
      ...payload,
      artifact_id: deriveArtifactId(payload, uiToolEvent.eventId || artifactMsg?.id || null),
    };

    try {
      const cacheKey = `mozaiks.current_artifact.${chatId}`;
      const serializableArtifact = {
        ...artifactMsg,
        uiToolEvent: {
          ...uiToolEvent,
          payload: artifactPayload,
          onResponse: null,
        },
      };
      platform.storage.setItem(cacheKey, JSON.stringify(serializableArtifact));

      const lastKey = `mozaiks.last_artifact.${chatId}`;
      platform.storage.setItem(
        lastKey,
        JSON.stringify({
          ui_tool_id: uiToolEvent.ui_tool_id || 'core.state',
          eventId: uiToolEvent.eventId || null,
          workflow_name: uiToolEvent.workflow_name || workflowName,
          payload: artifactPayload,
          display: displayMode || 'artifact',
          ts: Date.now(),
        })
      );
      artifactCacheValidRef.current = true;
    } catch (e) {
      console.warn('[Artifacts] Failed to persist artifact cache', e);
    }
  }, [currentArtifactMessages, chatId, workflowName]);

  // Update artifact payload
  const updateArtifactPayload = useCallback((artifactId, updateFn) => {
    if (!updateFn) return;

    setCurrentArtifactMessages((prev) => {
      if (!Array.isArray(prev) || prev.length === 0) return prev;

      let updated = false;
      const next = prev.map((msg) => {
        const payload = msg?.uiToolEvent?.payload;
        if (!msg?.uiToolEvent) return msg;
        const resolvedId = deriveArtifactId(payload, msg?.uiToolEvent?.eventId || msg?.id);
        if (artifactId && resolvedId !== artifactId) return msg;
        updated = true;
        const nextPayload = updateFn(payload || {});
        return {
          ...msg,
          uiToolEvent: {
            ...msg.uiToolEvent,
            payload: nextPayload,
          },
        };
      });

      if (updated) return next;

      // Fallback: apply to most recent artifact if id mismatch
      const lastIdx = prev.length - 1;
      const last = prev[lastIdx];
      if (last?.uiToolEvent) {
        const nextPayload = updateFn(last.uiToolEvent.payload || {});
        const fallback = [...prev];
        fallback[lastIdx] = {
          ...last,
          uiToolEvent: {
            ...last.uiToolEvent,
            payload: nextPayload,
          },
        };
        return fallback;
      }
      return prev;
    });
  }, []);

  // Apply optimistic update
  const applyOptimisticForAction = useCallback(
    (actionId, artifactId, optimistic) => {
      if (!optimistic) return;

      updateArtifactPayload(artifactId, (payload) => {
        if (!optimisticSnapshotsRef.current.has(actionId)) {
          let snapshotPayload = payload;
          try {
            if (payload && typeof payload === 'object') {
              snapshotPayload = JSON.parse(JSON.stringify(payload));
            }
          } catch {}
          optimisticSnapshotsRef.current.set(actionId, {
            artifactId,
            payload: snapshotPayload,
          });
        }
        return applyOptimisticUpdate(payload, optimistic);
      });
    },
    [updateArtifactPayload]
  );

  // Apply artifact update from action
  const applyArtifactUpdateForAction = useCallback(
    (artifactId, update) => {
      if (!update) return;
      updateArtifactPayload(artifactId, (payload) => applyArtifactUpdate(payload, update));
    },
    [updateArtifactPayload]
  );

  // Apply JSON patch
  const applyPatch = useCallback(
    (artifactId, patchOps) => {
      if (!Array.isArray(patchOps)) return;
      updateArtifactPayload(artifactId, (current) => applyJsonPatch(current || {}, patchOps));
    },
    [updateArtifactPayload]
  );

  // Replace artifact state entirely
  const replaceArtifactState = useCallback((artifactId, newState) => {
    updateArtifactPayload(artifactId, () => newState);
  }, [updateArtifactPayload]);

  // Open artifact panel
  const openPanel = useCallback(() => {
    setIsPanelOpen(true);
    if (isMobileView) {
      setMobileDrawerState('expanded');
      setHasUnseenArtifact(false);
    }
  }, [isMobileView]);

  // Close artifact panel
  const closePanel = useCallback(() => {
    setIsPanelOpen(false);
    if (isMobileView) {
      setMobileDrawerState('peek');
    }
  }, [isMobileView]);

  // Set artifact from UI tool event
  const setArtifactFromEvent = useCallback(
    (uiToolEvent, options = {}) => {
      const { openPanelAutomatically = true, agentName = 'Agent' } = options;
      const {
        ui_tool_id,
        payload = {},
        eventId,
        workflow_name,
        onResponse,
        display,
      } = uiToolEvent;

      const artifactPayload = {
        ...payload,
        artifact_id: deriveArtifactId(payload, eventId || ui_tool_id || null),
      };

      const artifactMsg = {
        id: `ui-artifact-${eventId || Date.now()}`,
        sender: 'agent',
        agentName,
        content: artifactPayload.structured_output || artifactPayload.content || artifactPayload || {},
        isStreaming: false,
        uiToolEvent: {
          ui_tool_id,
          payload: artifactPayload,
          eventId,
          workflow_name,
          onResponse,
          display,
        },
      };

      setCurrentArtifactMessages([artifactMsg]);
      artifactCacheValidRef.current = true;
      lastArtifactEventRef.current = eventId;

      if (openPanelAutomatically) {
        openPanel();
      }

      return artifactMsg;
    },
    [openPanel]
  );

  // Restore artifact from platform.storage
  const restoreFromCache = useCallback(
    (targetChatId) => {
      if (artifactRestoredOnceRef.current) return false;

      try {
        const key = `mozaiks.last_artifact.${targetChatId}`;
        const raw = platform.storage.getItem(key);
        if (!raw) return false;

        const cached = JSON.parse(raw);
        if (!cached?.ui_tool_id) return false;

        const restoredMsg = {
          id: `ui-restored-${Date.now()}`,
          sender: 'agent',
          agentName: cached.payload?.agentName || 'Agent',
          content: cached.payload?.structured_output || cached.payload || {},
          isStreaming: false,
          uiToolEvent: {
            ui_tool_id: cached.ui_tool_id,
            payload: cached.payload || {},
            eventId: cached.eventId || null,
            workflow_name: cached.workflow_name || workflowName,
            display: cached.display || 'artifact',
            restored: true,
          },
        };

        setCurrentArtifactMessages([restoredMsg]);
        artifactRestoredOnceRef.current = true;
        artifactCacheValidRef.current = true;
        return true;
      } catch (e) {
        console.warn('[Artifacts] Failed to restore from cache:', e);
        return false;
      }
    },
    [workflowName]
  );

  // Clear artifacts
  const clearArtifacts = useCallback(() => {
    setCurrentArtifactMessages([]);
    lastArtifactEventRef.current = null;
    artifactCacheValidRef.current = false;
  }, []);

  // Clear cache for a chat
  const clearCache = useCallback((targetChatId) => {
    try {
      platform.storage.removeItem(`mozaiks.last_artifact.${targetChatId}`);
      platform.storage.removeItem(`mozaiks.current_artifact.${targetChatId}`);
    } catch {}
    artifactRestoredOnceRef.current = false;
    artifactCacheValidRef.current = false;
  }, []);

  return {
    // State
    currentArtifactMessages,
    isPanelOpen,
    mobileDrawerState,
    hasUnseenArtifact,
    actionStatusMap,

    // Refs
    currentArtifactMessagesRef,
    lastArtifactEventRef,
    artifactRestoredOnceRef,
    artifactCacheValidRef,
    optimisticSnapshotsRef,
    viewArtifactSnapshotRef,

    // Setters
    setCurrentArtifactMessages,
    setIsPanelOpen,
    setMobileDrawerState,
    setHasUnseenArtifact,
    setActionStatusMap,

    // Actions
    updateArtifactPayload,
    applyOptimisticForAction,
    applyArtifactUpdateForAction,
    applyPatch,
    replaceArtifactState,
    openPanel,
    closePanel,
    setArtifactFromEvent,
    restoreFromCache,
    clearArtifacts,
    clearCache,
  };
}

export default useArtifacts;
