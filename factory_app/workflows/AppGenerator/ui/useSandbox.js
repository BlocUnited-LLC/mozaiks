import { useCallback, useEffect, useRef, useState } from 'react';
import { openAuthenticatedWebSocket } from '@mozaiks/chat-ui/adapters/websocketAuth.js';
import { getStudioAccessToken, studioFetch } from '../../../app/admin/pages/studioApi.js';

export function useSandbox(artifactId, buildRegistryId) {
  const [sandboxId, setSandboxId] = useState(null);
  const [sandboxStatus, setSandboxStatus] = useState(null);
  const [livePreviewUrl, setLivePreviewUrl] = useState(null);
  const [sandboxError, setSandboxError] = useState(null);
  const [syncing, setSyncing] = useState(false);
  const generation = useRef(0);
  const inFlight = useRef(false);

  useEffect(() => {
    generation.current += 1;
    inFlight.current = false;
    setSandboxId(null);
    setSandboxStatus(null);
    setLivePreviewUrl(null);
    setSandboxError(null);
    setSyncing(false);
    return () => { generation.current += 1; };
  }, [artifactId, buildRegistryId]);

  const applyStatus = useCallback((message) => {
    setSandboxStatus(message.status || null);
    setLivePreviewUrl(message.status === 'running' ? message.previewUrl || null : null);
    setSandboxError(message.error || message.lastError || message.message || null);
  }, []);

  useEffect(() => {
    if (!sandboxId) return undefined;
    const currentGeneration = generation.current;
    let closed = false;
    let timer;
    const isCurrent = () => !closed && currentGeneration === generation.current;
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
  }, [sandboxId, applyStatus]);

  const syncAndRestart = useCallback(async (filesMap) => {
    if (!artifactId || !buildRegistryId || inFlight.current) return;
    const entries = Object.entries(filesMap || {});
    if (!entries.length) return;
    const currentGeneration = generation.current;
    const isCurrent = () => generation.current === currentGeneration;
    inFlight.current = true;
    setSyncing(true);
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
      const query = `?build_registry_id=${encodeURIComponent(buildRegistryId)}`;
      const { sandboxId: sid } = await post(`/api/artifacts/${encodeURIComponent(artifactId)}/sandbox${query}`);
      if (!isCurrent()) return;
      setSandboxId(sid);
      await post(`/api/sandbox/${encodeURIComponent(sid)}/sync`, {
        files: entries.map(([path, content]) => ({ path, content: String(content) })), deleted: [],
      });
      if (!isCurrent()) return;
      const result = await post(`/api/sandbox/${encodeURIComponent(sid)}/start`);
      if (isCurrent()) applyStatus(result);
    } catch (error) {
      if (isCurrent()) applyStatus({ status: 'error', message: error.message || 'Preview failed' });
    } finally {
      if (isCurrent()) {
        inFlight.current = false;
        setSyncing(false);
      }
    }
  }, [artifactId, buildRegistryId, applyStatus]);

  return { sandboxId, sandboxStatus, livePreviewUrl, sandboxError, syncing, syncAndRestart };
}
