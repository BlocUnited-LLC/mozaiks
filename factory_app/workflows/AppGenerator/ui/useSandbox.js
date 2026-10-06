import { useCallback, useEffect, useRef, useState } from 'react';
import { openAuthenticatedWebSocket } from '@mozaiks/chat-ui/adapters/websocketAuth.js';
import { getStudioAccessToken, studioFetch } from '../../../app/admin/pages/studioApi.js';

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
  const generation = useRef(0);
  const observation = useRef(0);
  const selectedRegistry = useRef(buildRegistryId);
  const inFlight = useRef(false);
  const lastSession = useRef(null);
  const currentStatus = useRef(null);
  // Hide a different app synchronously, before the selection effect runs.
  const visibleSession = session?.buildRegistryId === buildRegistryId ? session : null;
  const sandboxId = visibleSession?.sandboxId || null;

  useEffect(() => {
    generation.current += 1;
    const retainPreview = selectedRegistry.current === buildRegistryId
      && currentStatus.current === 'running' && !inFlight.current;
    selectedRegistry.current = buildRegistryId;
    if (!retainPreview) {
      observation.current += 1;
      setSession(null);
      setSandboxStatus(null);
      currentStatus.current = null;
      setLivePreviewUrl(null);
      setSandboxError(null);
    }
    // A new version waits for the previous request before adopting a session.
    setSyncing(inFlight.current);
    return () => { generation.current += 1; };
  }, [artifactId, buildRegistryId]);

  const applyStatus = useCallback((message) => {
    currentStatus.current = message.status || null;
    setSandboxStatus(message.status || null);
    setLivePreviewUrl(message.status === 'running' ? message.previewUrl || null : null);
    setSandboxError(message.error || message.lastError || message.message || null);
  }, []);

  useEffect(() => {
    if (!sandboxId || syncing || stopping || !['starting', 'running'].includes(sandboxStatus)) return undefined;
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
  }, [sandboxId, sandboxStatus, syncing, stopping, applyStatus]);

  const syncAndRestart = useCallback(async (filesMap) => {
    if (!artifactId || !buildRegistryId || inFlight.current) return;
    const entries = Object.entries(filesMap || {});
    if (!entries.length) return;
    const currentGeneration = generation.current;
    const isCurrent = () => generation.current === currentGeneration;
    inFlight.current = true;
    observation.current += 1;
    setSyncing(true);
    setSession(null);
    applyStatus({ status: 'starting' });

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
      const previous = lastSession.current;
      if (previous && (previous.artifactId !== artifactId || previous.buildRegistryId !== buildRegistryId || previous.requiresStop)) {
        previous.requiresStop = true;
        if (!await stopSandbox(previous.sandboxId, isCurrent)) return;
        if (lastSession.current === previous) lastSession.current = null;
        if (!isCurrent()) return;
      }
      const query = `?build_registry_id=${encodeURIComponent(buildRegistryId)}`;
      const { sandboxId: sid } = await post(`/api/artifacts/${encodeURIComponent(artifactId)}/sandbox${query}`);
      lastSession.current = { sandboxId: sid, artifactId, buildRegistryId };
      if (!isCurrent()) return;
      setSession(lastSession.current);
      await post(`/api/sandbox/${encodeURIComponent(sid)}/sync`, {
        files: entries.map(([path, content]) => ({ path, content: String(content) })), deleted: [],
      });
      if (!isCurrent()) return;
      const result = await post(`/api/sandbox/${encodeURIComponent(sid)}/start`);
      if (isCurrent()) applyStatus(result);
    } catch (error) {
      if (isCurrent()) applyStatus({ status: 'error', message: error.message || 'Preview failed' });
    } finally {
      inFlight.current = false;
      setSyncing(false);
      setStopping(false);
    }
  }, [artifactId, buildRegistryId, applyStatus]);

  const stopPreview = useCallback(async () => {
    if (!sandboxId || inFlight.current) return;
    const currentGeneration = generation.current;
    const isCurrent = () => generation.current === currentGeneration;
    inFlight.current = true;
    observation.current += 1;
    setStopping(true);
    if (lastSession.current?.sandboxId === sandboxId) lastSession.current.requiresStop = true;
    applyStatus({ status: 'stopping' });
    try {
      if (!await stopSandbox(sandboxId, isCurrent)) return;
      if (lastSession.current?.sandboxId === sandboxId) lastSession.current = null;
      if (isCurrent()) {
        generation.current += 1;
        setSession(null);
        applyStatus({ status: null });
      }
    } catch (error) {
      if (isCurrent()) applyStatus({ status: 'error', message: error.message || 'Preview could not be stopped' });
    } finally {
      inFlight.current = false;
      setSyncing(false);
      setStopping(false);
    }
  }, [sandboxId, applyStatus]);

  return {
    sandboxId, sandboxStatus, livePreviewUrl: visibleSession ? livePreviewUrl : null,
    previewArtifactId: visibleSession && livePreviewUrl ? visibleSession.artifactId : null,
    sandboxError, syncing, stopping, syncAndRestart, stopPreview,
  };
}
