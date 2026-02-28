import { useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { useChatUI } from '../context/ChatUIContext';

const resolveMode = (mode) => {
  const normalized = typeof mode === 'string' ? mode.toLowerCase() : 'workflow';
  if (normalized === 'view' || normalized === 'workflow' || normalized === 'ask') {
    return normalized;
  }
  return 'workflow';
};

const resolveLayoutMode = (mode) => {
  if (mode === 'view') return 'view';
  if (mode === 'ask') return 'full';
  return 'split';
};

const resolveAppId = (overrideAppId, chatConfig) => {
  return (
    overrideAppId ||
    chatConfig?.chat?.defaultAppId ||
    process.env.REACT_APP_DEFAULT_APP_ID ||
    process.env.REACT_APP_DEFAULT_app_id ||
    null
  );
};

export const useNavigationActions = () => {
  const navigate = useNavigate();
  const {
    config: chatConfig,
    setPendingNavigationTrigger,
    setLayoutMode,
    setConversationMode,
    setChatMinimized,
    setIsInWidgetMode,
  } = useChatUI();

  return useCallback(
    (item) => {
      if (!item) return;
      const trigger = item.trigger;

      if (trigger && trigger.type === 'workflow') {
        const mode = resolveMode(trigger.mode);
        const appId = resolveAppId(trigger.app_id || trigger.appId, chatConfig);
        const workflow = trigger.workflow;
        const cacheTtl = trigger.cache_ttl ?? trigger.cacheTtl ?? null;
        const triggerId = typeof crypto !== 'undefined' && crypto.randomUUID
          ? crypto.randomUUID()
          : `nav-${Date.now()}-${Math.random().toString(36).slice(2)}`;

        const triggerPayload = {
          id: triggerId,
          workflow,
          app_id: appId,
          mode,
          input: trigger.input ?? null,
          cache_ttl: cacheTtl,
          requested_at: Date.now(),
          source: 'navigation',
        };

        if (typeof setPendingNavigationTrigger === 'function') {
          setPendingNavigationTrigger(triggerPayload);
        }

        if (typeof setLayoutMode === 'function') {
          setLayoutMode(resolveLayoutMode(mode));
        }
        if (typeof setConversationMode === 'function') {
          setConversationMode(mode === 'ask' ? 'ask' : 'workflow');
        }
        if (typeof setChatMinimized === 'function') {
          setChatMinimized(false);
        }
        if (typeof setIsInWidgetMode === 'function') {
          setIsInWidgetMode(false);
        }

        const params = new URLSearchParams();
        if (appId) params.set('appId', appId);
        if (workflow) params.set('workflow', workflow);
        if (mode === 'ask') params.set('mode', 'ask');
        if (mode === 'workflow') params.set('mode', 'workflow');
        params.set('nav', triggerId);
        navigate(`/?${params.toString()}`);
        return;
      }

      if (item.path) {
        navigate(item.path);
        return;
      }

      if (item.href) {
        window.location.href = item.href;
      }
    },
    [
      chatConfig,
      navigate,
      setPendingNavigationTrigger,
      setLayoutMode,
      setConversationMode,
      setChatMinimized,
      setIsInWidgetMode,
    ],
  );
};

export default useNavigationActions;
