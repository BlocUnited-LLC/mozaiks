// ==============================================================================
// FILE: factory_app/workflows/AppGenerator/ui/useSandbox.js
// DESCRIPTION: Hook for managing a live preview sandbox (e2b or Docker) tied
//   to an artifact.
//   - Tracks sandboxId and live status via WebSocket
//   - Exposes syncAndRestart(filesMap) to push updated files and reboot the
//     dev server after a coding-worker patch is applied
// ==============================================================================

import { useCallback, useEffect, useRef, useState } from 'react';
import { getStudioAccessToken, studioFetch } from '../../../app/admin/pages/studioApi.js';

export function useSandbox(artifactId) {
  const [sandboxId, setSandboxId] = useState(null);
  const [sandboxStatus, setSandboxStatus] = useState(null); // 'starting' | 'running' | 'error' | null
  const [livePreviewUrl, setLivePreviewUrl] = useState(null);
  const [sandboxError, setSandboxError] = useState(null);
  const [syncing, setSyncing] = useState(false);
  const wsRef = useRef(null);

  // Subscribe to live sandbox status over WebSocket whenever sandboxId is known
  useEffect(() => {
    if (!sandboxId) return;

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = new URL(`${protocol}//${window.location.host}/ws/sandbox/${encodeURIComponent(sandboxId)}`);
    const token = getStudioAccessToken();
    if (token) wsUrl.searchParams.set('access_token', token);
    const ws = new WebSocket(wsUrl.toString());
    wsRef.current = ws;

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        if (msg.type === 'status') {
          setSandboxStatus(msg.status || null);
          if (msg.previewUrl) setLivePreviewUrl(msg.previewUrl);
          if (msg.error) setSandboxError(msg.error);
        }
      } catch {
        // ignore malformed messages
      }
    };

    ws.onerror = () => setSandboxError('Sandbox WebSocket connection error.');

    return () => {
      ws.close();
      wsRef.current = null;
    };
  }, [sandboxId]);

  // Sync a full filesMap into the sandbox and restart the dev server.
  // Pass the merged filesMap (all files, not just the diff) so an expired
  // sandbox that was recreated gets a consistent full state.
  const syncAndRestart = useCallback(
    async (filesMap) => {
      if (!artifactId) return;
      const entries = Object.entries(filesMap || {});
      if (!entries.length) return;

      setSyncing(true);
      setSandboxError(null);

      try {
        // 1. Create or reuse an existing sandbox for this artifact
        const createRes = await studioFetch(`/api/artifacts/${encodeURIComponent(artifactId)}/sandbox`, {
          method: 'POST',
        });
        if (!createRes.ok) {
          const body = await createRes.json().catch(() => ({}));
          throw new Error(body.detail || `Sandbox create failed (${createRes.status})`);
        }
        const { sandboxId: sid } = await createRes.json();
        setSandboxId(sid);

        // 2. Push all current files into the sandbox
        const syncRes = await studioFetch(`/api/sandbox/${encodeURIComponent(sid)}/sync`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            files: entries.map(([path, content]) => ({ path, content: String(content) })),
            deleted: [],
          }),
        });
        if (!syncRes.ok) {
          const body = await syncRes.json().catch(() => ({}));
          throw new Error(body.detail || `Sandbox sync failed (${syncRes.status})`);
        }

        // 3. Restart the dev server; WebSocket will broadcast the new previewUrl
        setSandboxStatus('starting');
        const startRes = await studioFetch(`/api/sandbox/${encodeURIComponent(sid)}/start`, {
          method: 'POST',
        });
        if (!startRes.ok) {
          const body = await startRes.json().catch(() => ({}));
          throw new Error(body.detail || `Sandbox start failed (${startRes.status})`);
        }
        const { status, previewUrl } = await startRes.json();
        setSandboxStatus(status || null);
        if (previewUrl) setLivePreviewUrl(previewUrl);
      } catch (err) {
        setSandboxError(err instanceof Error ? err.message : String(err));
        setSandboxStatus('error');
      } finally {
        setSyncing(false);
      }
    },
    [artifactId],
  );

  return { sandboxId, sandboxStatus, livePreviewUrl, sandboxError, syncing, syncAndRestart };
}
