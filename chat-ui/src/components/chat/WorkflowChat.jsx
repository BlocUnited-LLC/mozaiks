import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { BrowserRouter, useInRouterContext } from 'react-router-dom';
import { WebSocketApiAdapter } from '../../adapters/api';
import { submitToolCallResponse } from '../../adapters/uiToolResponse';
import platform from '../../platform/index.js';
import config from '../../config';
import { useChatWebSocket } from '../../pages/hooks';
import ShellUIToolRenderer from '../../core/ui/ShellUIToolRenderer';
import { applyArtifactUpdate, buildArtifactAction, deriveArtifactId } from '../../core/actions/actionUtils';
import { WorkflowToolSurface } from '../../core/ui/UIToolRenderer';
import { useOptionalChatUI } from '../../context/ChatUIContext';
import { useTheme } from '../../styles/useTheme';
import { applyTheme } from '../../styles/themeProvider';
import { isArtifactViewDisplay, isCanvasDisplay } from '../../state/uiSurfaceReducer';
import ArtifactPanel from './ArtifactPanel';
import ChatInterface from './ChatInterface';
import FluidChatLayout from './FluidChatLayout';
import {
  buildComposerArtifactContext, buildInlineToolCallMessageFromEvent, buildToolCallMessageFromEvent,
  patchToolCallMessage, projectSavedArtifact, savedResponseAvailability, upsertInlineToolCallMessage,
} from './inlineSurfaces';

function renderStandaloneTool(event, onResponse, actions) {
  return <ShellUIToolRenderer event={event} onResponse={onResponse} {...actions} />;
}

function StandaloneToolRenderer(props) {
  return <WorkflowToolSurface {...props} uiToolRenderer={renderStandaloneTool} />;
}

function normalizeWorkflowEvent(event) {
  if (!event || typeof event !== 'object') {
    return null;
  }

  let normalized = { ...event };

  if (
    typeof normalized.content === 'string'
    && !String(normalized.type || '').startsWith('chat.')
  ) {
    const trimmed = normalized.content.trim();
    if (trimmed.startsWith('{') && trimmed.endsWith('}')) {
      try {
        const parsed = JSON.parse(trimmed);
        if (parsed && typeof parsed === 'object' && parsed.type) {
          normalized = { ...parsed };
        }
      } catch {
        // Ignore double-serialized content we cannot parse.
      }
    }
  }

  if (normalized.data && typeof normalized.data === 'object') {
    normalized = { ...normalized.data, ...normalized };
  }

  if (normalized.content && typeof normalized.content === 'object') {
    normalized.content = normalized.content.content
      || normalized.content.text
      || normalized.content.message
      || '';
  }

  return normalized;
}

function getWorkflowEventType(event) {
  const rawType = String(event?.type || '');
  return rawType.startsWith('chat.') ? rawType.slice(5) : rawType;
}

function getWorkflowEventContent(event) {
  return String(event?.content || event?.message || '');
}

function getWorkflowAgentName(event) {
  return String(event?.agent || event?.agent_name || event?.sender || 'assistant');
}

function WorkflowConversation({
  workflow,
  userId,
  appId,
  initialContext,
  onMessage,
  onComplete,
  onClose,
  className = '',
  showHeader = true,
  brandName,
  logo,
  backgroundImage,
  presentation,
  freshSession = false,
  getAccessToken,
  onArtifact,
  onReplyActivity,
  onSessionChange,
  composerLabel,
  transcriptLabel,
}) {
  const [chatId, setChatId] = useState(null);
  const [messages, setMessages] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [inputValue, setInputValue] = useState('');
  const [isSending, setIsSending] = useState(false);
  const [canReply, setCanReply] = useState(false);
  const [activity, setActivity] = useState('idle');
  const [sendError, setSendError] = useState('');
  const [ended, setEnded] = useState(false);
  const [artifactMessage, setArtifactMessage] = useState(null);
  const [actionStatuses, setActionStatuses] = useState({});
  const [layoutMode, setLayoutMode] = useState('split');
  const [mobileDrawerState, setMobileDrawerState] = useState('peek');
  const [isMobile, setIsMobile] = useState(false);
  const layoutRef = useRef(null);
  const expectingUser = useRef(false);
  const pendingUser = useRef(null);
  const activeUserTurn = useRef(false);
  const seenArtifacts = useRef(new Set());
  const canvasEvent = useRef({id: null, source: null});
  const streams = useRef(new Map());
  const messageSequence = useRef(0);
  const active = useRef(true);
  const studio = presentation === 'studio';
  const callbacks = useRef({});
  callbacks.current = { onArtifact, onMessage, onComplete, onSessionChange, onReplyActivity };
  useEffect(() => { callbacks.current.onReplyActivity?.(activity); }, [activity]);
  useEffect(() => {
    if (chatId) callbacks.current.onSessionChange?.(chatId);
    return () => { if (chatId) callbacks.current.onSessionChange?.(null); };
  }, [chatId]);

  const resolvedAppId = appId || config.get('chat.defaultAppId') || 'default';
  const hostChat = useOptionalChatUI();
  const { theme, loading: themeLoading } = useTheme(resolvedAppId, { applyToDocument: false });
  const appName = brandName || (!themeLoading && theme.branding?.name) || workflow;
  const backdrop = backgroundImage === undefined ? theme.branding?.chatbackgroundImage : backgroundImage;
  const chatTheme = useMemo(() => ({
    ...theme,
    branding: { ...theme.branding, name: appName, logo: logo ?? theme.branding?.logo, chatbackgroundImage: backdrop },
  }), [theme, appName, logo, backdrop]);
  useLayoutEffect(() => {
    if (layoutRef.current) applyTheme(chatTheme, layoutRef.current);
  }, [chatTheme, loading, themeLoading, error]);
  const ownsCanvas = typeof onArtifact !== 'function';
  const ToolRenderer = hostChat ? undefined : StandaloneToolRenderer;
  useLayoutEffect(() => {
    const container = layoutRef.current;
    if (!container || !ownsCanvas) return;
    const measure = () => setIsMobile(container.clientWidth < 768);
    measure();
    if (typeof ResizeObserver !== 'function') {
      window.addEventListener('resize', measure);
      return () => window.removeEventListener('resize', measure);
    }
    const observer = new ResizeObserver(measure);
    observer.observe(container);
    return () => observer.disconnect();
  }, [ownsCanvas, loading, themeLoading, error]);
  const tokenProvider = useRef(getAccessToken);
  tokenProvider.current = getAccessToken;
  const hasTokenProvider = typeof getAccessToken === 'function';
  const workflowApi = useMemo(
    () => new WebSocketApiAdapter({
      ...config.get('api'),
      ...(hasTokenProvider && { getAccessToken: () => tokenProvider.current?.() }),
    }),
    [hasTokenProvider]
  );

  const submitResponse = useCallback(async (id, response) => {
    const token = await (tokenProvider.current ? tokenProvider.current() : platform.getAccessToken());
    return submitToolCallResponse(id, response, { baseUrl: workflowApi.getHttpBaseUrl(), token });
  }, [workflowApi]);

  const handleRuntimeEvent = useCallback((rawEvent) => {
    const event = normalizeWorkflowEvent(rawEvent);
    if (!event) {
      return;
    }

    const receiveAnswer = (complete) => {
      const agent = getWorkflowAgentName(event);
      const streamKey = event.stream_id || agent;
      const previous = streams.current.get(streamKey);
      const fragment = getWorkflowEventContent(event);
      if (!fragment && !previous) return;
      if (!complete && previous && Number.isInteger(event.chunk_seq) && event.chunk_seq <= previous.chunkSeq) return;
      const message = {
        id: previous?.id || ++messageSequence.current,
        sender: 'assistant', agentName: agent, timestamp: previous?.timestamp || new Date().toISOString(),
        content: complete ? fragment || previous.content : (previous?.content || '') + fragment,
        isStreaming: !complete,
      };
      if (complete) streams.current.delete(streamKey);
      else streams.current.set(streamKey, { ...message, chunkSeq: event.chunk_seq });
      setMessages(items => previous ? items.map(item => item.id === message.id ? message : item) : [...items, message]);
      setActivity(complete ? 'replied' : 'answering');
      if (complete) callbacks.current.onMessage?.(message);
    };
    const eventType = getWorkflowEventType(event);
    const callbacksForSession = callbacks.current;
    const frame = rawEvent.data || rawEvent;
    // Only a typed frame on this socket can restore or change its canvas. Chat text may quote one.
    const matchesScope = data => (!data.chat_id || data.chat_id === chatId)
      && (!data.app_id || data.app_id === resolvedAppId) && (!data.user_id || data.user_id === userId)
      && (!data.workflow_name || data.workflow_name === workflow) && (!data.workflow || data.workflow === workflow);
    const isCanvasFrame = getWorkflowEventType(rawEvent) === eventType && matchesScope(rawEvent) && matchesScope(frame);
    switch (eventType) {
      case 'chat_meta': {
        if (callbacksForSession.onArtifact || !isCanvasFrame || event.chat_id !== chatId || event.chat_exists !== true) return;
        const saved = frame.last_artifact;
        if (!saved?.tool_name || !isCanvasDisplay(saved.display || 'artifact') || !matchesScope(saved)) return;
        const previous = canvasEvent.current;
        if (previous.source === 'live' || (previous.source === 'dismissed' && previous.id === saved.tool_call_id)) return;
        const {restoredArtifactMessage} = projectSavedArtifact(saved, workflow);
        const tool = restoredArtifactMessage.toolCall;
        if (tool.response_available === true && tool.payload.awaiting_response !== false && tool.tool_call_id) {
          tool.onResponse = response => submitResponse(tool.tool_call_id, response);
        }
        canvasEvent.current = {id: tool.tool_call_id, source: 'metadata'};
        setArtifactMessage(restoredArtifactMessage);
        return;
      }
      case 'awaiting_reply':
        expectingUser.current = true;
        activeUserTurn.current = false;
        setCanReply(true);
        return;
      case 'text':
      case 'print':
      case 'message':
      case 'agent_message':
        if (event.replay) {
          const content = getWorkflowEventContent(event);
          if (!content) return;
          const agentName = getWorkflowAgentName(event);
          const sender = agentName.toLowerCase() === 'user' ? 'user' : 'assistant';
          const replayed = { id: ++messageSequence.current, sender, agentName, content, replayIndex: event.index,
            timestamp: event.timestamp || new Date().toISOString() };
          if (sender === 'user' && pendingUser.current?.content === content) {
            pendingUser.current = null;
            setSendError('');
          }
          setMessages(items => {
            const matches = item => item.sender === sender && (sender === 'user' || item.agentName === agentName);
            const recorded = items.findIndex(item => matches(item) && Number.isInteger(event.index) && item.replayIndex === event.index);
            if (recorded >= 0) return items;
            const live = items.findIndex(item => matches(item) && item.replayIndex == null
              && (item.content === content || (item.isStreaming && content.startsWith(item.content))));
            if (live < 0) return [...items, replayed];
            return items.map((item, index) => index === live ? { ...replayed, id: item.id } : item);
          });
          return;
        }
        if (getWorkflowAgentName(event).toLowerCase() === 'user') {
          const text = getWorkflowEventContent(event);
          if (text && pendingUser.current?.content === text) {
            setSendError('');
            pendingUser.current = null;
          } else if (text) {
            setMessages(previous => [...previous, { id: event.id || ++messageSequence.current, sender: 'user', content: text }]);
          }
          return;
        }
        receiveAnswer(true);
        return;
      case 'stream_chunk':
        receiveAnswer(false);
        return;
      case 'stream_end':
        receiveAnswer(true);
        return;
      case 'tool_call':
      case 'ui.render': {
        const artifact = buildToolCallMessageFromEvent(event, workflow, 'artifact');
        if (artifact) {
          // Render authority comes from a tool event on this session's socket, never quoted chat text.
          const id = event.tool_call_id || event.corr;
          if (!isCanvasFrame || !id || (frame.tool_call_id || frame.corr) !== id) return;
          if (!callbacksForSession.onArtifact) {
            const previous = canvasEvent.current;
            if (previous.source === 'dismissed' && previous.id === id) return;
            if (event.replay && ['live', 'metadata', 'dismissed'].includes(previous.source)) return;
            canvasEvent.current = {id, source: event.replay ? 'history' : 'live'};
            if (event.replay) artifact.toolCall.response_available = savedResponseAvailability(event);
            artifact.toolCall.onResponse = response => submitResponse(id, response);
            setArtifactMessage(artifact);
            if (!event.replay && (previous.id !== id || previous.source !== 'live')) {
              setLayoutMode(isArtifactViewDisplay(artifact.toolCall.display) && !isMobile ? 'view' : 'split');
              setMobileDrawerState('expanded');
            }
          } else if (artifact.toolCall.display === 'artifact' && !seenArtifacts.current.has(id)) {
            seenArtifacts.current.add(id);
            callbacksForSession.onArtifact?.(rawEvent, { chatId, afterUserInput: activeUserTurn.current && !event.replay });
          }
          return;
        }
        const inlineEntry = buildInlineToolCallMessageFromEvent(event, workflow);
        if (!inlineEntry) {
          return;
        }
        setMessages((prev) => {
          return upsertInlineToolCallMessage(prev, {
            ...inlineEntry,
            timestamp: new Date().toISOString(),
          });
        });
        callbacksForSession.onMessage?.(inlineEntry);
        return;
      }
      case 'ui.update': {
        if (!isCanvasFrame || !frame.tool_call_id || !frame.patch || typeof frame.patch !== 'object' || Array.isArray(frame.patch)) return;
        if (canvasEvent.current.id === frame.tool_call_id && canvasEvent.current.source !== 'dismissed') {
          canvasEvent.current.source = 'live';
        }
        const applyPatch = message => patchToolCallMessage(message, frame.tool_call_id, frame.patch);
        setArtifactMessage(applyPatch);
        setMessages(previous => previous.map(applyPatch));
        return;
      }
      case 'ui.dismiss': {
        const id = frame.tool_call_id;
        if (!isCanvasFrame || !id || canvasEvent.current.id !== id || canvasEvent.current.source === 'dismissed') return;
        canvasEvent.current = {id, source: 'dismissed'};
        setArtifactMessage(null);
        setLayoutMode('full');
        setMobileDrawerState('peek');
        return;
      }
      case 'artifact.action.started':
      case 'artifact.action.completed':
      case 'artifact.action.failed': {
        if (rawEvent.type !== eventType || !event.action_id) return;
        const status = eventType.slice('artifact.action.'.length);
        setActionStatuses(previous => ({ ...previous, [event.action_id]: {
          ...previous[event.action_id], status, result: event.result, error: event.error,
        } }));
        const update = event.artifact_update || event.result?.artifact_update;
        if (status === 'completed' && update) {
          setArtifactMessage(current => {
            const toolCall = current?.toolCall;
            if (!toolCall || deriveArtifactId(toolCall.payload, toolCall.tool_call_id) !== event.artifact_id) return current;
            return { ...current, toolCall: { ...toolCall, payload: applyArtifactUpdate(toolCall.payload, update) } };
          });
        }
        return;
      }
      case 'run_complete':
      case 'workflow_complete':
        if (event.status === 'failed') {
          activeUserTurn.current = false;
          setActivity('idle');
          setError('The workflow could not continue.');
          return;
        }
        if (event.run_completed === true && !event.awaiting_user_input) {
          activeUserTurn.current = false;
          expectingUser.current = false;
          setCanReply(false);
          setEnded(true);
        }
        if (event.run_completed === false || event.awaiting_user_input === true) return;
        if (
          event?.status === 1 ||
          String(event?.status ?? '').trim().toLowerCase() === '1' ||
          ['completed', 'complete', 'success', 'succeeded', 'done', 'ok'].includes(
            String(event?.status ?? '').trim().toLowerCase()
          )
        ) {
          callbacksForSession.onComplete?.(event);
        }
        return;
      case 'error':
        if (event.error_code === 'CHAT_BUSY' && pendingUser.current) {
          const refused = pendingUser.current;
          pendingUser.current = null;
          activeUserTurn.current = false;
          expectingUser.current = true;
          setCanReply(true);
          setActivity('idle');
          setInputValue(refused.content);
          setMessages(previous => previous.filter(message => message !== refused));
          setSendError('The agent is finishing the previous turn. Send your message again.');
          return;
        }
        activeUserTurn.current = false;
        setActivity('idle');
        setError(event.message || 'Workflow error');
        return;
      default:
        return;
    }
  }, [chatId, resolvedAppId, userId, workflow, isMobile, submitResponse]);

  const { connectionStatus, retryConnection, wsRef } = useChatWebSocket({
    api: workflowApi,
    appId: resolvedAppId,
    userId,
    chatId,
    workflowName: workflow,
    workflowConfigLoaded: true,
    onMessage: handleRuntimeEvent,
    recoverOnDrop: !ended,
    onInterrupted: () => {
      // On reconnect the server can project a newer snapshot or expire a pending decision.
      if (canvasEvent.current.source === 'live') canvasEvent.current.source = null;
      if (pendingUser.current) setSendError('Delivery of your last message is unconfirmed. Check the conversation before sending it again.');
      streams.current.clear();
      expectingUser.current = false;
      activeUserTurn.current = false;
      setCanReply(false);
      setActivity('idle');
    },
  });

  useEffect(() => {
    let mounted = true;
    active.current = true;
    const controller = new AbortController();
    setMessages([]);
    setChatId(null);

    const startWorkflow = async () => {
      try {
        setLoading(true);
        setError(null);

        const result = await workflowApi.startChat(
          resolvedAppId,
          workflow,
          userId,
          { signal: controller.signal },
          initialContext || null,
          null,
          { forceNew: freshSession }
        );

        if (!mounted) {
          return;
        }

        if (result?.chat_id) {
          setChatId(result.chat_id);
          return;
        }

        const detail = typeof result?.detail === 'string'
          ? result.detail
          : result?.detail?.message || result?.detail?.error;
        setError(detail || result?.error || 'Failed to start workflow');
      } catch (err) {
        if (mounted) {
          setError(err.message);
        }
      } finally {
        if (mounted) {
          setLoading(false);
        }
      }
    };

    if (workflow && userId) {
      // StrictMode can dispose the first mount before a new chat is requested.
      queueMicrotask(() => { if (mounted) startWorkflow(); });
    }

    return () => {
      mounted = false;
      active.current = false;
      controller.abort();
    };
  }, [workflowApi, workflow, userId, resolvedAppId, initialContext, freshSession]);

  const handleSendMessage = useCallback(async (e) => {
    e?.preventDefault?.();
    const trimmed = (e?.content ?? inputValue).trim();
    if (!trimmed || !chatId || connectionStatus !== 'connected' || isSending || (studio && !expectingUser.current)) {
      return;
    }

    const userMessage = {
      id: ++messageSequence.current,
      sender: 'user',
      content: trimmed,
      timestamp: new Date().toISOString(),
    };

    expectingUser.current = false;
    pendingUser.current = userMessage;
    activeUserTurn.current = true;
    setCanReply(false);
    setActivity('answering');
    setSendError('');
    setMessages((prev) => [...prev, userMessage]);
    setInputValue('');
    setIsSending(true);

    try {
      const context = buildComposerArtifactContext(e?.artifactContext);
      const result = await workflowApi.sendMessageToWorkflow(trimmed, resolvedAppId, userId, workflow, chatId,
        context ? { artifact_context: context } : null);
      if (result === false || result?.success === false) throw new Error();
    } catch {
      if (!active.current || pendingUser.current !== userMessage) return;
      expectingUser.current = true;
      activeUserTurn.current = false;
      pendingUser.current = null;
      setCanReply(true);
      setMessages(previous => previous.filter(message => message !== userMessage));
      setInputValue(trimmed);
      setActivity('idle');
      setSendError('Your message could not be sent. Try again.');
    } finally {
      if (active.current) setIsSending(false);
    }
  }, [chatId, connectionStatus, inputValue, isSending, resolvedAppId, userId, workflowApi, workflow, studio]);

  if (loading || themeLoading) {
    return (
      <div
        className={`flex items-center justify-center h-full ${className}`}
        style={{ backgroundColor: 'var(--color-background, var(--mozaiks-bg, #0f172a))', fontFamily: 'var(--mozaiks-font, system-ui, sans-serif)' }}
      >
        <div className="text-center">
          <div
            className="animate-spin rounded-full h-8 w-8 border-b-2 mx-auto mb-2"
            style={{ borderColor: 'var(--mozaiks-primary, #3b82f6)' }}
          ></div>
          <p className="text-sm" style={{ color: 'var(--mozaiks-text-secondary, #6b7280)' }}>
            Starting {brandName || workflow}...
          </p>
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div
        className={`flex items-center justify-center h-full ${className}`}
        style={{ backgroundColor: 'var(--color-background, var(--mozaiks-bg, #0f172a))', fontFamily: 'var(--mozaiks-font, system-ui, sans-serif)' }}
      >
        <div className="text-center text-red-500">
          <p className="font-semibold">Failed to start workflow</p>
          <p className="text-sm">{error}</p>
        </div>
      </div>
    );
  }

  const phase = ended ? 'complete' : activity === 'answering' ? 'working' : 'idle';
  const detail = ended ? 'Conversation ended.' : activity === 'answering' ? appName + ' is replying…'
    : activity === 'replied' ? appName + ' replied' : canReply ? 'Ready for your question.' : 'Opening chat…';
  const toggleCanvas = () => {
    if (isMobile) setMobileDrawerState(state => state === 'expanded' ? 'peek' : 'expanded');
    else setLayoutMode(mode => mode === 'split' ? 'full' : 'split');
  };
  const sendArtifactAction = (action, context) => {
    const message = buildArtifactAction(action, context, {
      app_id: resolvedAppId, chat_id: chatId, user_id: userId, workflow_name: workflow,
    }, artifactMessage?.toolCall.tool_call_id);
    if (!message || connectionStatus !== 'connected' || !wsRef.current?.send) return null;
    wsRef.current.send(message);
    setActionStatuses(previous => ({ ...previous, [message.action_id]: { status: 'pending' } }));
    return message.action_id;
  };
  const chat = <ChatInterface
    messages={messages}
    onSendMessage={handleSendMessage}
    loading={activity === 'answering'}
    onAgentAction={action => submitResponse(action.tool_call_id, action.response)}
    onArtifactAction={sendArtifactAction}
    actionStatusMap={actionStatuses}
    onArtifactToggle={ownsCanvas ? toggleCanvas : undefined}
    ToolRenderer={ToolRenderer}
    hideHeader={!showHeader}
    outputControlOnly={!showHeader && ownsCanvas}
    plainContainer={!ownsCanvas}
    isOnChatPage
    appDisplayName={appName}
    workflowName={workflow}
    chatTheme={chatTheme}
    overlayMode={Boolean(onClose)}
    onOverlayClose={onClose}
    connectionStatus={connectionStatus}
    onRetry={ended ? undefined : retryConnection}
    chatStatus={{ phase, detail }}
    composerDisabled={(studio && !canReply) || connectionStatus !== 'connected' || ended || isSending}
    composerLabel={composerLabel}
    composerValue={inputValue}
    onComposerChange={setInputValue}
    transcriptLabel={transcriptLabel}
    artifactContext={artifactMessage && {
      id: artifactMessage.toolCall.tool_call_id, type: artifactMessage.toolCall.component_type,
      payload: artifactMessage.toolCall.payload, chat_id: chatId, workflow_name: workflow, display: artifactMessage.toolCall.display,
    }}
  />;

  return (
    <div ref={layoutRef} className={`relative isolate flex h-full min-h-0 flex-col ${className}`}>
      {backdrop && <img src={backdrop} alt="" className="absolute inset-0 -z-10 h-full w-full object-cover" />}
      {sendError && <p role="alert">{sendError}</p>}
      {ownsCanvas ? (
        <FluidChatLayout
          layoutMode={layoutMode}
          onLayoutChange={setLayoutMode}
          chatContent={chat}
          isMobile={isMobile}
          mobileDrawerState={mobileDrawerState}
          onMobileDrawerStateChange={setMobileDrawerState}
          onExitView={() => setLayoutMode('split')}
          onArtifactClose={() => {
            if (isMobile) setMobileDrawerState('peek');
            else setLayoutMode('full');
          }}
          artifactContent={<ArtifactPanel
            isEmbedded isMobile={isMobile} chatTheme={chatTheme} workflowName={workflow}
            messages={artifactMessage ? [artifactMessage] : []} ToolRenderer={ToolRenderer}
            onArtifactAction={sendArtifactAction} actionStatusMap={actionStatuses}
            viewMode={!isMobile && layoutMode === 'view'}
            onExpand={isMobile ? undefined : () => setLayoutMode('view')}
            onExitView={() => setLayoutMode('split')}
          />}
        />
      ) : chat}
    </div>
  );
}

export default function WorkflowChat(props) {
  const inRouter = useInRouterContext();
  const identity = JSON.stringify([props.appId, props.userId, props.workflow, props.initialContext, props.freshSession]);
  const initialContext = useMemo(() => props.initialContext, [identity]);
  const chat = <WorkflowConversation key={identity} {...props} initialContext={initialContext} />;
  return inRouter ? chat : <BrowserRouter>{chat}</BrowserRouter>;
}
