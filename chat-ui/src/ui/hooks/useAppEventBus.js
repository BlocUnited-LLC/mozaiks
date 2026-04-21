/**
 * useAppEventBus — In-process pub/sub bus for two event namespaces:
 *
 * 1. ui.* — agent-driven primitive events (pushed via WebSocket)
 *    { type: "ui.datatable.refresh", payload: { component_id: "..." } }
 *    { type: "ui.form.set_field",    payload: { component_id: "...", field: "...", value: ... } }
 *    { type: "ui.stat.update",       payload: { component_id: "...", value: ..., trend: ... } }
 *    { type: "ui.modal.open",        payload: { modal_id: "..." } }
 *    { type: "ui.modal.close",       payload: { modal_id: "..." } }
 *    { type: "ui.alert.show",        payload: { component_id: "...", message: "...", variant: "..." } }
 *    { type: "ui.tool.<name>",       payload: <component push data> }
 *
 * 2. routing.* — workflow transition events (shell-level, client-side only)
 *    { type: "routing.transition.show",    payload: { transition_id: string } }
 *    { type: "routing.transition.resolve", payload: { transition_id: string, option_id: string, context_variables?: object } }
 *
 * Primitives use useAppEvent(eventType, componentId, handler).
 * Transition components use useAppEventBus(eventType, handler) (no component id needed).
 * The shell subscribes to routing.transition.resolve to execute navigation.
 *
 * Usage:
 *   useAppEvent('ui.datatable.refresh', props.id, () => refetch());
 *   useAppEventBus('routing.transition.resolve', ({ option_id, context_variables }) => navigate(option_id, context_variables));
 */

import { useEffect, useRef, useCallback } from 'react';

// ---------------------------------------------------------------------------
// In-process event bus — a simple pub/sub over a global Map.
// Populated by the WebSocket bridge (below).
// ---------------------------------------------------------------------------

const listeners = new Map(); // topic → Set<(payload) => void>

function subscribe(topic, fn) {
  if (!listeners.has(topic)) listeners.set(topic, new Set());
  listeners.get(topic).add(fn);
  return () => listeners.get(topic)?.delete(fn);
}

function publish(topic, payload) {
  listeners.get(topic)?.forEach((fn) => fn(payload));
}

// ---------------------------------------------------------------------------
// WebSocket bridge — connects the agent WebSocket to the in-process bus.
// Mounted once per page via mountAppEventBridge().
// ---------------------------------------------------------------------------

let bridgeMounted = false;

/**
 * Wire a WebSocket instance (or the core WS ref) into the app event bus.
 * Call once when the chat WebSocket connects — typically in App.jsx or a
 * top-level provider that holds the WS connection.
 *
 * @param {WebSocket} ws
 * @returns {() => void} cleanup function
 */
export function mountAppEventBridge(ws) {
  if (!ws) return () => {};

  const handler = (event) => {
    let data;
    try { data = JSON.parse(event.data); } catch { return; }
    if (typeof data?.type === 'string' && data.type.startsWith('ui.')) {
      const payload = data.payload ?? {};
      // Route to component-scoped topic: "ui.datatable.refresh:my-table-id"
      // AND to the wildcard topic: "ui.datatable.refresh" (for primitives that use no id)
      publish(data.type, payload);
      if (payload.component_id) {
        publish(`${data.type}:${payload.component_id}`, payload);
      }
      if (payload.modal_id) {
        publish(`${data.type}:${payload.modal_id}`, payload);
      }
    }
  };

  ws.addEventListener('message', handler);
  bridgeMounted = true;
  return () => {
    ws.removeEventListener('message', handler);
    bridgeMounted = false;
  };
}

// ---------------------------------------------------------------------------
// React hook for primitives
// ---------------------------------------------------------------------------

/**
 * Subscribe a primitive to a typed ui.* event, optionally scoped by component id.
 *
 * @param {string}   eventType   - e.g. 'ui.datatable.refresh'
 * @param {string}   componentId - the primitive's id prop; scopes the subscription
 * @param {Function} handler     - called with the event payload
 */
export function useAppEvent(eventType, componentId, handler) {
  const handlerRef = useRef(handler);
  handlerRef.current = handler;

  useEffect(() => {
    const stableHandler = (payload) => handlerRef.current(payload);
    const topic = componentId ? `${eventType}:${componentId}` : eventType;
    return subscribe(topic, stableHandler);
  }, [eventType, componentId]);
}

/**
 * Programmatically emit a ui.* event (e.g. when a UI action should trigger a workflow).
 * The event is delivered locally to all subscribed primitives on this page.
 *
 * @param {string} eventType
 * @param {object} payload
 */
export function emitAppEvent(eventType, payload = {}) {
  publish(eventType, payload);
  if (payload.component_id) {
    publish(`${eventType}:${payload.component_id}`, payload);
  }
  if (payload.modal_id) {
    publish(`${eventType}:${payload.modal_id}`, payload);
  }
}

/**
 * Route an already-parsed WebSocket message into the app event bus.
 * Called from useCoreWebSocket's onmessage handler.
 * Handles both ui.* (primitive events) and routing.* (transition events).
 *
 * @param {{ type: string, payload?: object }} msg
 */
export function ingestEvent(msg) {
  if (typeof msg?.type !== 'string') return;
  const t = msg.type;
  if (!t.startsWith('ui.') && !t.startsWith('routing.')) return;
  const payload = msg.payload ?? {};
  publish(t, payload);
  if (payload.component_id) publish(`${t}:${payload.component_id}`, payload);
  if (payload.modal_id)     publish(`${t}:${payload.modal_id}`, payload);
}

// ---------------------------------------------------------------------------
// Routing transition helpers — used by transition components and the shell
// ---------------------------------------------------------------------------

/**
 * Subscribe to a bus event with no component-id scoping.
 * Useful for transition components and the shell router.
 *
 * @param {string}   eventType
 * @param {Function} handler
 */
export function useAppEventBus(eventType, handler) {
  const handlerRef = useRef(handler);
  handlerRef.current = handler;

  useEffect(() => {
    const stableHandler = (payload) => handlerRef.current(payload);
    return subscribe(eventType, stableHandler);
  }, [eventType]);
}

/**
 * Emit routing.transition.show to instruct the shell to mount a transition component.
 *
 * @param {string} transitionId
 */
export function showTransition(transitionId) {
  publish('routing.transition.show', { transition_id: transitionId });
}

/**
 * Emit routing.transition.resolve — called by transition components when the user picks an option.
 * The shell subscribes to this and executes the navigation.
 *
 * @param {string} transitionId
 * @param {string} optionId   — selected semantic option id
 * @param {object} contextVariables — optional runtime input from a custom transition component
 */
export function resolveTransition(transitionId, optionId, contextVariables = {}) {
  publish('routing.transition.resolve', {
    transition_id: transitionId,
    option_id: optionId,
    context_variables: contextVariables,
  });
}

export { subscribe as _subscribe, publish as _publish };
