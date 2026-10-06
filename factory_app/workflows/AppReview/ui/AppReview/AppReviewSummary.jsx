/**
 * AppReviewSummary — Check details and activation controls inside AppReviewWorkspace.
 *
 * Receives the selected saved draft's review evidence from its workspace owner.
 * Acceptance is handled there; this component activates an accepted version.
 */

import { useState, useCallback } from 'react';
import { Panel, StatusPill, Button } from '@mozaiks/chat-ui/ui';
import { studioFetch } from '../../../../app/admin/pages/studioApi.js';

const STATUS_TONE = {
  passed: 'success',
  failed: 'destructive',
  skipped: 'default',
  attention_required: 'warning',
};

function ValidationRow({ label, status }) {
  const tone = status ? STATUS_TONE[status] || 'default' : 'warning';
  const label_text = status
    ? status.charAt(0).toUpperCase() + status.slice(1)
    : 'Missing';
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
      const appIdQuery = `?build_registry_id=${encodeURIComponent(payload.build_registry_id)}`;
      const res = await studioFetch(`/api/studio/build/artifacts/${encodeURIComponent(payload.artifact_version_id)}/promote${appIdQuery}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
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

  const securitySummary = payload.security_readiness_summary || {};
  const securityStatus = securitySummary.status || (securitySummary.finding_count > 0 ? 'attention_required' : null);
  const securityFindings = Array.isArray(securitySummary.findings) ? securitySummary.findings : [];
  const validationStatus = payload.app_validation_status || null;
  const acceptanceStatus = payload.app_bundle_acceptance_status || null;
  const integrationStatus =
    payload.integration_tests_passed === true
      ? 'passed'
      : payload.integration_tests_passed === false
      ? 'failed'
      : null;
  const canPromote = (
    payload.can_promote === true
    && validationStatus === 'passed'
    && acceptanceStatus === 'passed'
    && integrationStatus === 'passed'
    && Boolean(payload?.artifact_version_id)
    && Boolean(payload?.build_registry_id)
  );
  const awaitingAcceptance = (
    payload.can_accept === true
    && validationStatus === 'passed'
    && acceptanceStatus === 'passed'
    && integrationStatus === 'passed'
    && Boolean(payload?.artifact_version_id)
    && Boolean(payload?.build_registry_id)
  );

  return (
    <Panel>
      <p className="mb-2 text-xs font-semibold uppercase tracking-widest text-muted-foreground">
        Review your app
      </p>
      <h3 className="text-xl font-semibold tracking-tight text-foreground">
        {promoted ? 'Your version is active' : canPromote ? 'Ready for your decision'
          : awaitingAcceptance ? 'Checks passed · Ready for your review' : 'This draft needs attention'}
      </h3>
      <p className="mt-2 mb-5 text-sm leading-relaxed text-muted-foreground">
        {promoted
          ? 'The reviewed version is now active in this workspace. Hosting and public access are managed separately.'
          : canPromote
            ? 'Required checks passed. Activate this version when you are happy with it, or describe a change in chat.'
            : awaitingAcceptance
              ? 'Accept this draft before activation, or request a change.'
              : 'Required checks are incomplete or failed. Review the check results before activating this version.'}
      </p>

      <details className="mb-4 rounded-lg border border-border/40 bg-muted/30 px-4 py-3">
        <summary className="cursor-pointer text-sm font-medium text-foreground">Check results</summary>
        <div className="mt-2">
        <ValidationRow label="Bundle acceptance" status={acceptanceStatus} />
        <ValidationRow label="Build validation" status={validationStatus} />
        <ValidationRow label="Integration checks" status={integrationStatus} />
        <ValidationRow label="Security readiness" status={securityStatus} />
        </div>
        {payload.app_validation_strategy_used && (
          <p className="mt-2 text-xs text-muted-foreground">Validation environment: {payload.app_validation_strategy_used}</p>
        )}
      </details>

      {securityFindings.length > 0 && (
        <div className="mb-4 rounded-lg border border-warning/30 bg-warning/10 px-4 py-3">
          <p className="text-sm font-medium text-warning">Security readiness needs attention</p>
          <p className="mt-1 text-xs text-muted-foreground">
            {securityFindings.length} advisory finding{securityFindings.length === 1 ? '' : 's'} recorded for review.
          </p>
        </div>
      )}

      {error && (
        <p role="alert" className="mb-3 text-sm text-destructive">{error}</p>
      )}

      {promoted ? (
        <div role="status" className="rounded-lg border border-success/40 bg-success/10 px-4 py-3 text-sm text-success">
          Version activated successfully.
        </div>
      ) : (
        <>
          <Button
            variant="primary"
            disabled={promoting || !canPromote}
            onClick={handlePromote}
            className="w-full"
          >
            {promoting ? 'Activating…' : 'Activate this version'}
          </Button>
          <p className="mt-2 text-center text-xs text-muted-foreground">
            Want changes? Describe them in the chat below.
          </p>
        </>
      )}
    </Panel>
  );
}
