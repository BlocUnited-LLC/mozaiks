/**
 * AppReviewSummary — In-chat agentic UI artifact for the build review step.
 *
 * Rendered by WorkflowUIRouter when present_review_summary.py emits a
 * UI_Surface tool call. Receives build validation results via the payload prop
 * and exposes a Promote button that promotes the reviewed artifact version.
 *
 * This is an agentic UI artifact, not a transition overlay.
 * It lives in the chat surface alongside the ReviewAgent conversation.
 */

import { useState, useCallback } from 'react';
import { Panel, StatusPill, Button, Metric } from '@mozaiks/chat-ui/ui';

const STATUS_TONE = {
  passed: 'success',
  failed: 'destructive',
  skipped: 'default',
};

function ValidationRow({ label, status }) {
  const tone = STATUS_TONE[status] || 'default';
  const label_text = status
    ? status.charAt(0).toUpperCase() + status.slice(1)
    : 'Unknown';
  return (
    <div className="flex items-center justify-between border-b border-border/40 py-2 last:border-0">
      <span className="text-sm text-muted-foreground">{label}</span>
      <StatusPill tone={tone}>{label_text}</StatusPill>
    </div>
  );
}

export default function AppReviewSummary({ payload = {} }) {
  const [promoting, setPromoting] = useState(false);
  const [promoted, setPromoted] = useState(false);
  const [error, setError] = useState(null);

  const handlePromote = useCallback(async () => {
    if (!payload?.artifact_version_id) {
      setError('No artifact version available. Cannot promote.');
      return;
    }
    if (!payload?.build_registry_id) {
      setError('No build registry ID available. Cannot promote.');
      return;
    }
    setPromoting(true);
    setError(null);
    try {
      const apiBase =
        typeof window !== 'undefined' ? window.__MOZAIKS_API_BASE__ || '' : '';
      const token =
        typeof window !== 'undefined' ? window.__MOZAIKS_ACCESS_TOKEN__ || '' : '';
      const headers = { 'Content-Type': 'application/json' };
      if (token) headers['Authorization'] = `Bearer ${token}`;
      const appIdQuery = payload?.app_id ? `?app_id=${encodeURIComponent(payload.app_id)}` : '';
      const res = await fetch(`${apiBase}/api/studio/build/artifacts/${encodeURIComponent(payload.artifact_version_id)}/promote${appIdQuery}`, {
        method: 'POST',
        headers,
        body: JSON.stringify({ build_registry_id: payload.build_registry_id || null }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body?.detail || `Promotion failed (${res.status})`);
      }
      setPromoted(true);
    } catch (err) {
      setError(err.message || 'Promotion failed.');
    } finally {
      setPromoting(false);
    }
  }, [payload]);

  const validationStatus = payload.app_validation_status || 'skipped';
  const acceptanceStatus = payload.app_bundle_acceptance_status || null;
  const integrationStatus =
    payload.integration_tests_passed === true
      ? 'passed'
      : payload.integration_tests_passed === false
      ? 'failed'
      : 'skipped';
  const canPromote = (
    payload.can_promote !== false
    && Boolean(payload?.artifact_version_id)
    && Boolean(payload?.build_registry_id)
  );

  return (
    <Panel>
      <p className="mb-3 text-xs font-semibold uppercase tracking-widest text-muted-foreground">
        Build Summary
      </p>

      {payload.app_validation_strategy_used && (
        <div className="mb-3">
          <Metric
            label="Validation strategy"
            value={payload.app_validation_strategy_used}
          />
        </div>
      )}

      <div className="mb-4 rounded-lg border border-border/40 bg-muted/30 px-4 py-1">
        {acceptanceStatus && (
          <ValidationRow label="Bundle acceptance" status={acceptanceStatus} />
        )}
        <ValidationRow label="Build validation" status={validationStatus} />
        <ValidationRow label="Integration checks" status={integrationStatus} />
      </div>

      {payload.app_validation_preview_url && (
        <div className="mb-4">
          <a
            href={payload.app_validation_preview_url}
            target="_blank"
            rel="noopener noreferrer"
            className="text-sm font-medium text-primary hover:underline"
          >
            Open Preview ↗
          </a>
        </div>
      )}

      {error && (
        <p className="mb-3 text-sm text-destructive">{error}</p>
      )}

      {!canPromote && !promoted && (
        <p className="mb-3 rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">
          Build review context is incomplete. Promotion is unavailable until the
          AppGenerator handoff provides a review-ready bundle.
        </p>
      )}

      {promoted ? (
        <div className="rounded-lg border border-success/40 bg-success/10 px-4 py-3 text-sm text-success">
          App promoted to active — your build is live.
        </div>
      ) : (
        <>
          <Button
            variant="primary"
            disabled={promoting || !canPromote}
            onClick={handlePromote}
            className="w-full"
          >
            {promoting ? 'Promoting…' : 'Promote to Active'}
          </Button>
          <p className="mt-2 text-center text-xs text-muted-foreground">
            Want changes? Describe them in the chat below.
          </p>
        </>
      )}
    </Panel>
  );
}
