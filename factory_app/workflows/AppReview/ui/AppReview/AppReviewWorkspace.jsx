import { useEffect, useMemo, useRef, useState } from 'react';
import { Button } from '@mozaiks/chat-ui/ui';
import { studioFetch } from '../../../../app/admin/pages/studioApi.js';
import PreviewPane from '../../../_shared/ui/app_preview/PreviewPane.js';
import { useSandbox } from '../../../_shared/ui/app_preview/useSandbox.js';
import { useSavedArtifactBundle } from '../../../_shared/ui/app_preview/useSavedArtifactBundle.js';
import { refinementOutput } from '../../../_shared/ui/app_preview/refinementOutput.js';
import AppReviewSummary from './AppReviewSummary.jsx';

export default function AppReviewWorkspace({ payload = {} }) {
  const targetAppId = payload.target_app_id;
  const registryId = payload.build_registry_id;
  const sourceId = payload.artifact_version_id;
  const result = useMemo(() => refinementOutput(payload.refinement_result), [payload.refinement_result]);
  const reviewedId = result ? result.metadata?.build_record_id || null : sourceId;
  const pending = payload.refinement_pending === true;
  const editError = typeof payload.refinement_error === 'string' ? payload.refinement_error : null;
  const needsRevision = payload.lifecycle_state === 'needs_revision';
  const identity = JSON.stringify([targetAppId, registryId, sourceId, reviewedId, result?.status, pending, editError, payload.lifecycle_state,
    payload.refinement_result?.change_request_id, payload.refinement_result?.revision_id]);
  const identityRef = useRef(identity);
  identityRef.current = identity;
  const [refresh, setRefresh] = useState(0);
  const [accepting, setAccepting] = useState(false);
  const [actionError, setActionError] = useState(null);
  const { body, error } = useSavedArtifactBundle(targetAppId, registryId, reviewedId, refresh);
  const [previewBundle, setPreviewBundle] = useState(null);
  const selectedPreview = previewBundle?.workbench?.target_app_id === targetAppId
    && previewBundle?.workbench?.build_registry_id === registryId ? previewBundle : null;
  const previewVersion = selectedPreview?.artifact_version_id || sourceId;
  const preview = useSandbox(previewVersion, registryId);
  const evidence = body?.review?.validation_result || {};
  const buildStatus = body?.workbench?.app_validation_status || null;
  const acceptance = evidence.app_bundle_acceptance_result || body?.workbench?.integration_test_result;
  const validationPassed = body?.review?.validation_status === 'passed' && buildStatus === 'passed' && acceptance?.passed === true;

  useEffect(() => {
    if (body && validationPassed && (!result || result.status === 'validated')) setPreviewBundle(body);
  }, [body, result, validationPassed]);

  // A failed edit may keep the previous passed artifact selected. Its saved
  // checks do not clear the current build's authoritative revision requirement.
  const incomplete = needsRevision || Boolean(editError) || Boolean(body && !validationPassed)
    || Boolean(result && (!body || result.status !== 'validated' || !validationPassed));
  const reviewPayload = {
    refinement_pending: pending,
    artifact_version_id: reviewedId,
    build_registry_id: registryId,
    app_validation_status: editError ? null : body ? buildStatus : result?.status === 'failed' ? 'failed' : null,
    app_validation_strategy_used: body?.workbench?.app_validation_strategy_used,
    app_bundle_acceptance_status: editError ? null : acceptance?.passed === true ? 'passed' : acceptance?.passed === false ? 'failed' : null,
    integration_tests_passed: editError ? null : acceptance?.passed ?? null,
    // Security evidence belongs to this review version; never carry the parent's
    // payload into a refinement candidate or treat a missing scan as passed.
    security_readiness_summary: !result && body && !editError ? payload.security_readiness_summary : {},
    can_accept: !pending && !incomplete && validationPassed && body?.review?.can_accept === true,
    can_promote: !pending && !incomplete && validationPassed && body?.review?.can_promote === true,
  };

  async function acceptDraft() {
    if (pending || accepting || !body?.review?.can_accept || !validationPassed || incomplete) return;
    const selectedIdentity = identity;
    setAccepting(true);
    setActionError(null);
    try {
      const response = await studioFetch(
        `/api/studio/build/artifacts/${encodeURIComponent(reviewedId)}/accept?build_registry_id=${encodeURIComponent(registryId)}`,
        { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' },
      );
      const responseBody = await response.json().catch(() => null);
      if (!response.ok) throw new Error(responseBody?.detail || 'This draft could not be accepted.');
      // Reload the canonical review; accepting does not grant activation locally.
      if (identityRef.current === selectedIdentity) setRefresh(value => value + 1);
    } catch (err) {
      if (identityRef.current === selectedIdentity) setActionError({identity:selectedIdentity,message:err.message});
    } finally {
      setAccepting(false);
    }
  }

  return (
    <div className="min-w-0 space-y-4" data-testid="app-review-workspace">
      <PreviewPane
        previewUrl={preview.livePreviewUrl}
        artifactVersionId={previewVersion}
        previewArtifactId={preview.previewArtifactId}
        refinementPending={pending}
        sandboxStatus={preview.sandboxStatus}
        sandboxSyncing={preview.syncing}
        sandboxError={preview.sandboxError}
        onStartPreview={() => preview.syncAndRestart(selectedPreview?.generated_files)}
        canStartPreview={Boolean(selectedPreview && !pending && !error)}
        onStopPreview={preview.sandboxId ? preview.stopPreview : null}
        sandboxStopping={preview.stopping}
        sandboxRecovering={preview.recovering}
        sandboxRecoveredStarting={preview.recoveredStarting}
        sandboxRecoveryError={preview.recoveryError}
        onRetryRecovery={preview.retryRecovery}
        unavailableMessage={body && !validationPassed ? 'This saved draft needs passed checks before previewing.' : undefined}
      />
      {payload.review_notice && <p role="status" className="text-sm text-muted-foreground">{payload.review_notice}</p>}
      {incomplete && (
        <p role="status" className="text-sm text-warning">
          {preview.livePreviewUrl
            ? 'This change needs attention. The preview still shows the previous draft.'
            : 'This change needs attention. Required checks must pass before activation.'}
        </p>
      )}
      {(error || editError || actionError?.identity === identity) && <p role="alert" className="text-sm text-destructive">{error || editError || actionError.message}</p>}
      {error && <Button variant="secondary" onClick={() => setRefresh(value => value + 1)}>Retry opening draft</Button>}
      {!body && !error && reviewedId && <p role="status" className="text-sm text-muted-foreground">Loading saved draft…</p>}
      {body?.review?.can_accept && validationPassed && !incomplete && (
        <Button variant="primary" disabled={pending || accepting} onClick={acceptDraft}>
          {accepting ? 'Accepting draft…' : 'Accept this draft'}
        </Button>
      )}
      {body && <AppReviewSummary key={`${identity}/${refresh}`} payload={reviewPayload} />}
    </div>
  );
}
