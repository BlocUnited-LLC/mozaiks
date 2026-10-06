import { useCallback, useEffect, useRef, useState } from 'react';
import { openAuthenticatedWebSocket } from '@mozaiks/chat-ui/adapters/websocketAuth.js';
import { getStudioAccessToken, studioFetch } from '../../../../app/admin/pages/studioApi.js';

async function stopSandbox(sandboxId, isCurrent) {
  for (let attempt = 0; attempt < 3; attempt += 1) {
    if (!isCurrent()) return false;
    const response = await studioFetch(`/api/sandbox/${encodeURIComponent(sandboxId)}/stop`, { method: 'POST' });
    if (response.ok || response.status === 404) return true;
    const result = await response.json().catch(() => ({}));
    if (response.status !== 409 || attempt === 2) {
      throw new Error(result.detail || 'Preview could not be stopped');
    }
    // Status observation can briefly own the operation lease. Keep admission
    // until cleanup is confirmed, with at most two bounded busy waits.
    const retryAfter = Number(response.headers.get('Retry-After')?.trim() || 2);
    const delay = Number.isFinite(retryAfter) ? Math.max(0, Math.min(retryAfter, 5)) * 1000 : 2000;
    await new Promise((resolve) => window.setTimeout(resolve, delay));
  }
  return false;
}

export function useSandbox(artifactId, buildRegistryId) {
  const [session, setSession] = useState(null);
  const [sandboxStatus, setSandboxStatus] = useState(null);
  const [livePreviewUrl, setLivePreviewUrl] = useState(null);
  const [sandboxError, setSandboxError] = useState(null);
  const [syncing, setSyncing] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [recovering, setRecovering] = useState(Boolean(buildRegistryId));
  const [recoveryError, setRecoveryError] = useState(null);
  const generation = useRef(0);
  const observation = useRef(0);
  const selectedRegistry = useRef(buildRegistryId);
  selectedRegistry.current = buildRegistryId;
  const registryScope = useRef(null);
  const inFlight = useRef(false);
  const lastSession = useRef(null);
  const currentStatus = useRef(null);
  // Hide a different app synchronously, before the selection effect runs.
  const visibleSession = session?.buildRegistryId === buildRegistryId ? session : null;
  const sandboxId = visibleSession?.sandboxId || null;

  const applyStatus = useCallback((message) => {
    currentStatus.current = message.status || null;
    setSandboxStatus(message.status || null);
    setLivePreviewUrl(message.status === 'running' ? message.previewUrl || null : null);
    setSandboxError(message.error || message.lastError || message.message || null);
  }, []);

  const isSelectedRegistry = useCallback((scope) => registryScope.current === scope
    && selectedRegistry.current === scope.buildRegistryId, []);

  const restoreSession = useCallback((scope) => {
    if (!isSelectedRegistry(scope)) return;
    const sessions = [...scope.sessions.values()];
    const restored = sessions.find(value => value.status === 'running' && value.previewUrl)
      || sessions[0] || null;
    lastSession.current = restored;
    setSession(restored);
    if (!inFlight.current) applyStatus(restored || { status: null });
  }, [applyStatus, isSelectedRegistry]);

  const recoverSessions = useCallback((scope) => {
    if (scope.recovery) return scope.recovery;
    if (!isSelectedRegistry(scope)) return Promise.resolve(false);
    setRecovering(true);
    setRecoveryError(null);
    scope.error = null;
    scope.recovery = (async () => {
      try {
        const response = await studioFetch(`/api/sandbox?build_registry_id=${encodeURIComponent(scope.buildRegistryId)}`);
        const body = await response.json();
        if (!isSelectedRegistry(scope)) return false;
        if (!response.ok) throw new Error(body.detail || 'Existing previews could not be recovered');
        if (!Array.isArray(body.sessions) || body.sessions.some(value => !value?.sandboxId
          || !value.artifactId || value.buildRegistryId !== scope.buildRegistryId)) {
          throw new Error('Existing preview identities could not be verified');
        }
        // Keep uncertain local cleanup handles until Stop confirms termination,
        // even if a concurrent list no longer contains them.
        for (const value of body.sessions) scope.sessions.set(value.sandboxId, value);
        restoreSession(scope);
        return true;
      } catch (error) {
        if (isSelectedRegistry(scope)) {
          scope.error = error.message || 'Existing previews could not be recovered';
          setRecoveryError(scope.error);
        }
        return false;
      } finally {
        scope.recovery = null;
        if (isSelectedRegistry(scope)) setRecovering(false);
      }
    })();
    return scope.recovery;
  }, [isSelectedRegistry, restoreSession]);

  useEffect(() => {
    generation.current += 1;
    if (inFlight.current) {
      observation.current += 1;
      applyStatus({ status: null });
    }
    setSyncing(inFlight.current);
    return () => { generation.current += 1; };
  }, [artifactId, buildRegistryId, applyStatus]);

  useEffect(() => {
    const scope = { buildRegistryId, sessions: new Map(), recovery: null, error: null };
    registryScope.current = scope;
    observation.current += 1;
    lastSession.current = null;
    setSession(null);
    applyStatus({ status: null });
    setRecoveryError(null);
    setRecovering(Boolean(buildRegistryId));
    if (buildRegistryId) recoverSessions(scope);
    return () => { if (registryScope.current === scope) registryScope.current = null; };
  }, [buildRegistryId, applyStatus, recoverSessions]);

  const retryRecovery = useCallback(() => {
    const scope = registryScope.current;
    if (scope?.buildRegistryId && !inFlight.current) return recoverSessions(scope);
    return Promise.resolve(false);
  }, [recoverSessions]);

  useEffect(() => {
    if (!sandboxId || syncing || stopping || recovering || !['starting', 'running'].includes(sandboxStatus)) return undefined;
    const currentObservation = observation.current;
    let closed = false;
    let timer;
    // Preserve the terminal cause even if an older poll arrives after failure.
    // A new explicit start resets status before observing the new attempt.
    const isCurrent = () => !closed && !inFlight.current && currentObservation === observation.current
      && lastSession.current?.sandboxId === sandboxId
      && ['starting', 'running'].includes(currentStatus.current);
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const url = `${protocol}//${window.location.host}/ws/sandbox/${encodeURIComponent(sandboxId)}`;
    const socket = openAuthenticatedWebSocket(url, getStudioAccessToken());
    socket.onmessage = (event) => {
      if (!isCurrent()) return;
      try {
        const message = JSON.parse(event.data);
        if (message.type === 'status') applyStatus(message);
      } catch {
        // HTTP polling remains authoritative if a status frame is malformed.
      }
    };

    async function poll() {
      try {
        const response = await studioFetch(`/api/sandbox/${encodeURIComponent(sandboxId)}/status`);
        const body = await response.json();
        if (isCurrent()) {
          if (!response.ok) throw new Error(body.detail || 'Preview unavailable');
          applyStatus(body);
        }
      } catch (error) {
        if (isCurrent()) applyStatus({ status: 'error', message: error.message || 'Preview unavailable' });
      } finally {
        if (isCurrent()) timer = window.setTimeout(poll, 10000);
      }
    }
    timer = window.setTimeout(poll, 10000);
    return () => {
      closed = true;
      window.clearTimeout(timer);
      socket.close();
    };
  }, [sandboxId, sandboxStatus, syncing, stopping, recovering, applyStatus]);

  const stopRegistrySessions = useCallback(async (scope, isCurrent) => {
    for (const previous of [...scope.sessions.values()]) {
      if (!isCurrent() || previous.buildRegistryId !== scope.buildRegistryId) return false;
      if (!await stopSandbox(previous.sandboxId, isCurrent)) return false;
      scope.sessions.delete(previous.sandboxId);
      if (lastSession.current?.sandboxId === previous.sandboxId && isSelectedRegistry(scope)) {
        lastSession.current = scope.sessions.values().next().value || null;
        setSession(lastSession.current);
      }
    }
    return isCurrent();
  }, [isSelectedRegistry]);

  const finishOperation = useCallback(async (scope) => {
    inFlight.current = false;
    const selected = registryScope.current;
    if (!selected) return;
    setSyncing(false);
    setStopping(false);
    // Navigation can recreate even the same registry while allocation is
    // pending. Finish its earlier lookup before reading the settled handles.
    if (selected !== scope) {
      if (selected.recovery) await selected.recovery;
      if (isSelectedRegistry(selected)) await recoverSessions(selected);
    }
  }, [isSelectedRegistry, recoverSessions]);

  const syncAndRestart = useCallback(async (filesMap) => {
    if (!artifactId || !buildRegistryId || inFlight.current) return;
    const entries = Object.entries(filesMap || {});
    if (!entries.length) return;
    const scope = registryScope.current;
    if (!scope || !isSelectedRegistry(scope) || scope.error) return;
    const currentGeneration = generation.current;
    const isCurrent = () => generation.current === currentGeneration && isSelectedRegistry(scope);
    inFlight.current = true;
    observation.current += 1;
    setSyncing(true);
    applyStatus({ status: 'starting' });
    let allocating = false;

    async function post(url, body) {
      const response = await studioFetch(url, {
        method: 'POST',
        ...(body ? { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {}),
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || `Preview request failed (${response.status})`);
      return result;
    }

    try {
      if (!await recoverSessions(scope)) {
        if (isCurrent()) applyStatus({ status: null });
        return;
      }
      if (!isCurrent()) return;
      applyStatus({ status: 'starting' });
      if (!await stopRegistrySessions(scope, isCurrent)) return;
      const query = `?build_registry_id=${encodeURIComponent(buildRegistryId)}`;
      allocating = true;
      const { sandboxId: sid } = await post(`/api/artifacts/${encodeURIComponent(artifactId)}/sandbox${query}`);
      allocating = false;
      const created = { sandboxId: sid, artifactId, buildRegistryId };
      scope.sessions.set(sid, created);
      if (isSelectedRegistry(scope)) {
        lastSession.current = created;
        setSession(created);
      }
      if (!isCurrent()) return;
      await post(`/api/sandbox/${encodeURIComponent(sid)}/sync`, {
        files: entries.map(([path, content]) => ({ path, content: String(content) })), deleted: [],
      });
      if (!isCurrent()) return;
      const result = await post(`/api/sandbox/${encodeURIComponent(sid)}/start`);
      if (isCurrent()) applyStatus(result);
    } catch (error) {
      // A failed response can still leave a durable reservation or provider
      // allocation. Recover its cleanup handle without retrying allocation.
      if (allocating && isSelectedRegistry(scope)) await recoverSessions(scope);
      if (isCurrent()) applyStatus({ status: 'error', message: error.message || 'Preview failed' });
    } finally {
      await finishOperation(scope);
    }
  }, [artifactId, buildRegistryId, applyStatus, isSelectedRegistry, recoverSessions, stopRegistrySessions, finishOperation]);

  const stopPreview = useCallback(async () => {
    if (!sandboxId || inFlight.current) return;
    const scope = registryScope.current;
    if (!scope || !isSelectedRegistry(scope)) return;
    // Stop owns registry cleanup, independent of whichever draft arrives next.
    const isCurrent = () => isSelectedRegistry(scope);
    inFlight.current = true;
    observation.current += 1;
    setStopping(true);
    applyStatus({ status: 'stopping' });
    try {
      if (!await recoverSessions(scope)) {
        if (isCurrent()) applyStatus({ status: null });
        return;
      }
      if (!isCurrent()) return;
      applyStatus({ status: 'stopping' });
      if (!await stopRegistrySessions(scope, isCurrent)) return;
      if (isCurrent()) {
        generation.current += 1;
        setSession(null);
        applyStatus({ status: null });
      }
    } catch (error) {
      if (isCurrent()) applyStatus({ status: 'error', message: error.message || 'Preview could not be stopped' });
    } finally {
      await finishOperation(scope);
    }
  }, [sandboxId, applyStatus, isSelectedRegistry, recoverSessions, stopRegistrySessions, finishOperation]);

  return {
    sandboxId, sandboxStatus, livePreviewUrl: visibleSession ? livePreviewUrl : null,
    previewArtifactId: visibleSession?.artifactId || null,
    sandboxError, syncing, stopping, recovering, recoveryError, retryRecovery, syncAndRestart, stopPreview,
  };
}
