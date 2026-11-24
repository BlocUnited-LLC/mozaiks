import React, { useEffect, useState } from 'react';
import { authFetch } from '../../adapters/api';

export function ModelSelect({ value, onChange, disabled, children, title }) {
  return (
    <select
      aria-label="AI model"
      title={title}
      className="h-8 max-w-full cursor-pointer truncate rounded-full border border-transparent bg-card px-2.5 text-xs font-medium text-foreground hover:border-border focus-visible:outline-2 focus-visible:outline-primary disabled:cursor-default disabled:text-muted-foreground"
      value={value}
      onChange={event => onChange(event.target.value)}
      disabled={disabled}
    >
      {children}
    </select>
  );
}

export function useWorkflowModelPicker({ api, appId, workflow, chatId, userId, disabled }) {
  const path = appId && workflow && chatId && userId
    ? '/api/chats/model/' + [appId, workflow, chatId].map(encodeURIComponent).join('/')
    : null;
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState(null);
  const config = api?.config;

  useEffect(() => {
    if (!path) return;
    const controller = new AbortController();
    const { signal } = controller;
    const scope = { path, userId, config, signal };
    setState(previous => previous?.path === path && previous.userId === userId && previous.config === config && previous.unconfirmed
      ? { ...scope, saving: true, unconfirmed: true }
      : null);
    async function load() {
      try {
        const response = await authFetch(path, { signal }, config);
        if (response.status === 404) {
          if (!signal.aborted) setState(null);
          return;
        }
        if (!response.ok) throw new Error();
        const catalog = await response.json();
        if (!signal.aborted) setState({ ...scope, catalog });
      } catch {
        if (!signal.aborted) setState(previous => ({ ...scope, unconfirmed: previous?.unconfirmed, error: 'Model list unavailable.' }));
      }
    }
    load();
    return () => controller.abort();
  }, [path, userId, config, attempt]);

  // A route change renders before effects run. Never expose the previous chat's choice or controls.
  if (!path || state?.path !== path || state.userId !== userId || state.config !== config || state.signal.aborted) {
    return { picker: null, saving: false };
  }
  const { catalog, signal, saving = false } = state;

  async function choose(value) {
    if (saving || signal.aborted) return;
    setState({ ...state, saving: true, error: null });
    try {
      const response = await authFetch(path, {
        method: 'PUT', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ model: value || null }), signal,
      }, config);
      if (!response.ok) throw new Error();
      const updated = await response.json();
      if (!signal.aborted) setState({ ...state, catalog: updated, saving: false, error: null });
    } catch {
      // A lost response may follow a committed write. Reload the server's choice before offering another edit.
      if (!signal.aborted) setState({ ...state, catalog: null, unconfirmed: true, error: 'Couldn’t confirm the model change.' });
    }
  }

  if (state.error) {
    return {
      saving: Boolean(state.unconfirmed),
      picker: <div className="flex min-w-0 flex-wrap items-center gap-x-2 text-xs text-muted-foreground">
        <span role="status">{state.error}</span>
        <button type="button" className="underline" onClick={() => setAttempt(value => value + 1)}>Reload models</button>
      </div>,
    };
  }
  const models = catalog?.models || [];
  if (!models.length && !catalog?.selected && !catalog?.locked) return { picker: null, saving };
  const selectedModel = models.find(model => model.id === catalog.selected);
  const defaultLabel = models.find(model => model.id === catalog.default)?.label;
  if (catalog.locked) {
    const label = selectedModel?.label || catalog.selected || defaultLabel;
    return { picker: label ? <span className="truncate px-2.5 text-xs text-muted-foreground" aria-label={`Run model: ${label}`}>{label}</span> : null, saving: false };
  }
  return {
    saving,
    picker: <ModelSelect value={catalog.selected || ''} disabled={disabled || saving} onChange={choose}
      title="Model for the next workflow reply">
      <option value="">{defaultLabel ? `Workflow default · ${defaultLabel}` : 'Workflow default'}</option>
      {catalog.selected && !selectedModel && <option value={catalog.selected} disabled>{catalog.selected} (unavailable)</option>}
      {models.map(model => <option key={model.id} value={model.id}>{model.label}</option>)}
    </ModelSelect>,
  };
}
