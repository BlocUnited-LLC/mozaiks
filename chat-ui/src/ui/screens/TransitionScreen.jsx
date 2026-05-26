/**
 * TransitionScreen — shell-level transition renderer.
 *
 * Fetches a transition by id from /api/transitions/{id}, resolves the registered
 * React component from transition.ui.component, and mounts it with
 * { transition, onResolve } props.
 *
 * transition_type behavior:
 *   user_choice — renders transition.ui.component or falls back to LauncherScreen
 *   confirm     — renders transition.ui.component or falls back to ConfirmScreen
 *   condition   — asks the backend router to evaluate context_key and continue
 *   silent      — asks the backend router to continue immediately
 *
 * Subscribes to routing.transition.resolve bus for mid-flight transition chaining.
 *
 * Props:
 *   transitionId {string}   Transition id to fetch and render
 *   onNavigate   {Function} (option_id?: string|null, runtime_context?: object) => void
 *   context      {object}   Optional live runtime context (AG2 context_variables) merged
 *                           into transition.context before the component receives it.
 *                           Used by components like AppReviewScreen to read build_registry_id,
 *                           preview_url, validation results, and lifecycle_state.
 */

import { useState, useEffect, useCallback, useId } from 'react';
import { getComponent, hasComponent } from '../../registry/componentRegistry';
import { LauncherScreen } from './LauncherScreen';
import { ConfirmScreen } from './ConfirmScreen';
import { TransitionOverlayFrame } from './TransitionOverlayFrame.jsx';
import { useAppEventBus } from '../hooks/useAppEventBus';

// ---------------------------------------------------------------------------
// Loading / error states
// ---------------------------------------------------------------------------

const TransitionLoading = () => (
  <div className="fixed inset-0 z-[70] flex items-center justify-center bg-background/78 px-4 backdrop-blur-md">
    <div className="flex w-full max-w-sm flex-col items-center gap-3 rounded-[1.75rem] border border-border/80 bg-background/90 px-8 py-10 text-center shadow-2xl shadow-black/35 backdrop-blur-xl">
      <div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" />
      <p className="text-sm text-muted-foreground">Loading…</p>
    </div>
  </div>
);

const TransitionError = ({ message, onRetry }) => (
  <div className="fixed inset-0 z-[70] flex items-center justify-center bg-background/78 px-4 backdrop-blur-md">
    <div className="w-full max-w-md rounded-[1.75rem] border border-destructive/40 bg-background/90 p-8 text-center shadow-2xl shadow-black/35 backdrop-blur-xl">
      <h1 className="text-lg font-black text-destructive mb-3">Transition Error</h1>
      <p className="text-sm text-muted-foreground mb-5">{message}</p>
      {onRetry && (
        <button
          className="px-5 py-2 rounded-lg bg-muted text-muted-foreground text-sm font-semibold hover:bg-muted/70 transition-colors"
          onClick={onRetry}
        >
          Retry
        </button>
      )}
    </div>
  </div>
);

// ---------------------------------------------------------------------------
// TransitionScreen
// ---------------------------------------------------------------------------

export function TransitionScreen({ transitionId, onNavigate, context }) {
  const [transition, setTransition] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [retryCount, setRetryCount] = useState(0);
  const titleId = useId();
  const descriptionId = useId();

  // Fetch transition from API — re-runs on transitionId change or explicit retry
  useEffect(() => {
    if (!transitionId) return;
    setLoading(true);
    setError(null);
    setTransition(null);

    fetch(`/api/transitions/${encodeURIComponent(transitionId)}`)
      .then((res) => {
        if (!res.ok) throw new Error(`Transition '${transitionId}' not found (${res.status})`);
        return res.json();
      })
      .then((data) => {
        setTransition(data);
        setLoading(false);
      })
      .catch((err) => {
        setError(err.message);
        setLoading(false);
      });
  }, [transitionId, retryCount]);

  useEffect(() => {
    if (!transition) return;

    if (
      ['silent', 'progress_view', 'prerequisite_redirect', 'chat_session'].includes(transition.transition_type)
    ) {
      const t = setTimeout(() => {
        onNavigate?.(null);
      }, 0);
      return () => clearTimeout(t);
    }

    if (transition.transition_type !== 'condition') return;

    if (!transition.context_key) {
      console.warn(`[TransitionScreen] condition transition '${transition.id}' is missing context_key`);
      return;
    }

    if (!transition.routes?.length && !transition.default_route) {
      console.warn(`[TransitionScreen] condition transition '${transition.id}' has no route`);
      return;
    }

    const t = setTimeout(() => {
      onNavigate?.(null);
    }, 0);

    return () => clearTimeout(t);
  }, [transition, onNavigate]);

  const onResolve = useCallback(
    (option_id, contextVariables = {}) => {
      onNavigate?.(option_id, contextVariables);
    },
    [onNavigate]
  );

  useAppEventBus('routing.transition.resolve', ({ option_id, context_variables }) => {
    onNavigate?.(option_id ?? null, context_variables ?? {});
  });

  const handleRetry = useCallback(() => setRetryCount((n) => n + 1), []);
  const handleDismiss = useCallback(() => {
    if (typeof window !== 'undefined' && window.history.length > 1) {
      window.history.back();
    }
  }, []);

  if (loading) return <TransitionLoading />;
  if (error) return <TransitionError message={error} onRetry={handleRetry} />;
  if (!transition) return null;

  if (
    ['condition', 'silent', 'progress_view', 'prerequisite_redirect', 'chat_session'].includes(transition.transition_type)
  ) {
    return null;
  }

  let body = null;

  // Merge live runtime context (from AG2 context_variables) into the static
  // transition definition so components like AppReviewScreen can read values
  // such as build_registry_id, preview_url, and validation results.
  const transitionWithContext = (context && typeof context === 'object' && Object.keys(context).length > 0)
    ? { ...transition, context }
    : transition;

  const componentName = transition.ui?.component;
  if (componentName && hasComponent(componentName)) {
    const TransitionComponent = getComponent(componentName);
    body = (
      <TransitionComponent
        transition={transitionWithContext}
        onResolve={onResolve}
        overlayTitleId={titleId}
        overlayDescriptionId={descriptionId}
      />
    );
  } else if (transition.transition_type === 'confirm') {
    body = (
      <ConfirmScreen
        transition={transitionWithContext}
        onResolve={onResolve}
        overlayTitleId={titleId}
        overlayDescriptionId={descriptionId}
      />
    );
  } else {
    body = (
      <LauncherScreen
        transition={transitionWithContext}
        onResolve={onResolve}
        overlayTitleId={titleId}
        overlayDescriptionId={descriptionId}
      />
    );
  }

  return (
    <TransitionOverlayFrame
      transition={transition}
      titleId={titleId}
      descriptionId={descriptionId}
      onDismiss={handleDismiss}
    >
      {body}
    </TransitionOverlayFrame>
  );
}

export default TransitionScreen;
