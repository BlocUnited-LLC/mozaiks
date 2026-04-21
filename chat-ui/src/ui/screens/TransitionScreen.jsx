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
 *   transitionId {string}  Transition id to fetch and render
 *   onNavigate   {Function} (option_id?: string|null, runtime_context?: object) => void
 */

import { useState, useEffect, useCallback } from 'react';
import { getComponent, hasComponent } from '../../registry/componentRegistry';
import { LauncherScreen } from './LauncherScreen';
import { ConfirmScreen } from './ConfirmScreen';
import { useAppEventBus } from '../hooks/useAppEventBus';

// ---------------------------------------------------------------------------
// Loading / error states
// ---------------------------------------------------------------------------

const TransitionLoading = () => (
  <div className="flex min-h-full flex-1 items-center justify-center bg-background px-6 py-12">
    <div className="flex flex-col items-center gap-3">
      <div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" />
      <p className="text-sm text-muted-foreground">Loading…</p>
    </div>
  </div>
);

const TransitionError = ({ message, onRetry }) => (
  <div className="flex min-h-full flex-1 items-center justify-center bg-background px-6 py-12">
    <div className="rounded-xl border border-destructive/40 bg-destructive/10 p-8 text-center max-w-md">
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

export function TransitionScreen({ transitionId, onNavigate }) {
  const [transition, setTransition] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [retryCount, setRetryCount] = useState(0);

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
      ['silent', 'progress_view', 'prerequisite_redirect'].includes(transition.transition_type)
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

  if (loading) return <TransitionLoading />;
  if (error) return <TransitionError message={error} onRetry={handleRetry} />;
  if (!transition) return null;

  if (
    ['condition', 'silent', 'progress_view', 'prerequisite_redirect'].includes(transition.transition_type)
  ) {
    return null;
  }

  const componentName = transition.ui?.component;
  if (componentName && hasComponent(componentName)) {
    const TransitionComponent = getComponent(componentName);
    return <TransitionComponent transition={transition} onResolve={onResolve} />;
  }

  if (transition.transition_type === 'confirm') {
    return <ConfirmScreen transition={transition} onResolve={onResolve} />;
  }

  return <LauncherScreen transition={transition} onResolve={onResolve} />;
}

export default TransitionScreen;
