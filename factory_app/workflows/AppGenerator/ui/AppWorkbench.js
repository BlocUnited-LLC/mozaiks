// ==============================================================================
// FILE: factory_app/workflows/AppGenerator/ui/AppWorkbench.js
// DESCRIPTION: AppGenerator artifact canvas (files + Monaco + preview + export)
// ==============================================================================

import { useMemo, useState, useEffect, useRef } from 'react';
import { Code, LayoutGrid, Monitor } from 'lucide-react';
import { useWorkflowStart } from '@mozaiks/chat-ui/hooks/useWorkflowStart.js';
import { workflowSurfaceStyles, workflowToolbarButtonClass } from '@mozaiks/chat-ui/platform/workflowSurfaceStyles.js';
import { normalizePrimitiveActions } from '@mozaiks/chat-ui/core/ui/workflowPrimitiveUtils.js';
import { useAppValidationWorkbench } from './useAppValidationWorkbench';
import { useSandbox } from './useSandbox';
import BuildStatusPane from './BuildStatusPane';
import CodeEditorPane from './CodeEditorPane';
import PreviewPane from './PreviewPane';
import ExportActions from './ExportActions';
import FileTreePane from './FileTreePane';
import HarnessDecisionCard from '../../../app/ui/components/HarnessDecisionCard.jsx';
import { studioFetch } from '../../../app/admin/pages/studioApi.js';

const THEME_FILE_PATH = 'brand/theme_config.json';

const refinementOutput = (response) => {
  if (response?.execution_mode === 'coding_worker') return response.coding_worker || null;
  if (response?.execution_mode !== 'surface_regeneration' || !response.surface_result) return null;
  const result = response.surface_result;
  return {
    ...result,
    status: result.status === 'failed' ? 'failed'
      : result.status === 'success' && result.metadata?.validation_result?.validation_status === 'passed'
        ? 'validated' : 'planned',
    applied_files: result.all_files,
    validation_result: result.metadata?.validation_result,
    error: result.surfaces_executed?.find((surface) => surface.status === 'failed')?.error,
  };
};

const AppWorkbench = ({
  payload = {},
  onResponse,
  toolName,
  toolCallId,
  sourceWorkflowName,
  generatedWorkflowName,
  showExportActions = true,
}) => {
  const config = useMemo(() => {
    const workbench = payload?.workbench && typeof payload.workbench === 'object' ? payload.workbench : {};
    const candidates = [
      payload?.theme_config,
      payload?.themeConfig,
      payload?.ui_config,
      payload?.uiConfig,
      workbench?.theme_config,
      workbench?.themeConfig,
      workbench?.ui_config,
      workbench?.uiConfig,
    ];
    return candidates.find((candidate) => candidate && typeof candidate === 'object') || {};
  }, [payload]);
  const layoutCfg = config?.layout || {};
  const defaultView = layoutCfg.defaultView || 'preview-only';
  const [view, setView] = useState(defaultView);
  const [refinementRequest, setRefinementRequest] = useState('');
  const [limitToSelectedFile, setLimitToSelectedFile] = useState(false);
  const [refinementResult, setRefinementResult] = useState(null);
  const [pendingHarness, setPendingHarness] = useState(null);
  const [refinementError, setRefinementError] = useState(null);
  const [artifactReview, setArtifactReview] = useState(payload?.review || null);
  const [artifactReviewBusy, setArtifactReviewBusy] = useState(false);
  const [artifactReviewError, setArtifactReviewError] = useState(null);
  const [artifactReviewNotice, setArtifactReviewNotice] = useState(null);
  const artifactValidationResult = artifactReview?.validation_result || null;
  const artifactValidationCommands = Array.isArray(artifactValidationResult?.command_results)
    ? artifactValidationResult.command_results
    : [];
  const artifactValidationFallbacks = Array.isArray(artifactValidationResult?.fallback_checks)
    ? artifactValidationResult.fallback_checks
    : [];
  const { startWorkflow, starting: refinementStarting, error: workflowStartError } = useWorkflowStart();
  const [activeArtifactVersionId, setActiveArtifactVersionId] = useState(
    payload?.artifact_version_id || payload?.artifactVersionId || null
  );
  const [reviewArtifactVersionId, setReviewArtifactVersionId] = useState(
    payload?.artifact_version_id || payload?.artifactVersionId || null
  );
  const artifactReviewRef = useRef(null);
  const reviewNotes = Array.isArray(artifactReview?.risk_notes)
    ? artifactReview.risk_notes.filter(note => note && note !== artifactReview.validation_blocker)
    : [];
  const codingResult = refinementOutput(refinementResult);
  const savedDraftId = codingResult?.metadata?.build_record_id;
  const confirmationOnly = payload?.stage === 'confirm';
  const hasDownloadFiles = Array.isArray(payload?.files) && payload.files.some(Boolean);
  const canShowExportActions = showExportActions && !codingResult && (hasDownloadFiles || confirmationOnly);
  const exportPayload = confirmationOnly ? {
    ...payload,
    actions: normalizePrimitiveActions(payload, [
      { id: 'download_complete', label: 'Confirm app bundle', variant: 'primary', approved: true },
      { id: 'close', label: 'Close', variant: 'secondary' },
    ]).map((action) => action.id === 'download_complete' ? { ...action, label: 'Confirm app bundle' } : action),
  } : payload;
  const codingResultTone = codingResult?.status === 'validated' && savedDraftId
    ? 'border-emerald-500/30 bg-emerald-500/10 text-emerald-100'
    : codingResult?.status === 'failed'
      ? 'border-red-500/30 bg-red-500/10 text-red-200'
      : 'border-amber-400/30 bg-amber-400/10 text-amber-100';
  const codingResultMessage = {
    validated: savedDraftId ? 'Draft validated and saved for review.' : 'Validation passed, but no saved draft is available.',
    planned: savedDraftId ? 'Draft saved; validation is incomplete.' : 'Refinement planned; no draft was saved.',
    failed: savedDraftId ? 'Draft saved; validation failed.' : 'Refinement failed; no draft was saved.',
    ineligible: 'This change is not eligible for scoped refinement.',
  }[codingResult?.status] || 'Refinement has not completed.';

  // Preview ownership follows the persisted artifact version and build target.
  const artifactVersionId = activeArtifactVersionId;
  const buildRegistryId = payload?.build_registry_id;
  const artifactQuery = `?build_registry_id=${encodeURIComponent(buildRegistryId || '')}`;
  const reviewIdentity = `${buildRegistryId || ''}/${reviewArtifactVersionId || ''}`;
  const reviewIdentityRef = useRef(reviewIdentity);
  reviewIdentityRef.current = reviewIdentity;
  const selectionIdentity = JSON.stringify([buildRegistryId, payload?.artifact_version_id || payload?.artifactVersionId]);
  const selectionRef = useRef({ identity: selectionIdentity });
  if (selectionRef.current.identity !== selectionIdentity) selectionRef.current = { identity: selectionIdentity };
  const refinementSelectionRef = useRef(null);
  const currentWorkflowError = refinementSelectionRef.current === selectionRef.current ? workflowStartError : null;
  const anotherVersionIsRefining = refinementStarting && refinementSelectionRef.current !== selectionRef.current;
  const artifactKind = payload?.artifact_kind || payload?.artifactKind || 'app_bundle';
  const artifactKey = payload?.artifact_key || payload?.artifactKey || artifactKind;

  const {
    filesMap,
    setFilesMap,
    selectedPath,
    setSelectedPath,
    currentContent,
    updateFileContent,
    validationResult,
    validationStatus,
    validationStrategy,
    integrationTestResult,
    integrationPassed,
  } = useAppValidationWorkbench(payload, config, codingResult, activeArtifactVersionId);

  const {
    sandboxStatus,
    livePreviewUrl,
    previewArtifactId,
    sandboxError: sandboxSyncError,
    syncing: sandboxSyncing,
    sandboxId,
    stopping: sandboxStopping,
    stopPreview,
    syncAndRestart,
  } = useSandbox(artifactVersionId, buildRegistryId);

  useEffect(() => {
    setActiveArtifactVersionId(payload?.artifact_version_id || payload?.artifactVersionId || null);
    setReviewArtifactVersionId(payload?.artifact_version_id || payload?.artifactVersionId || null);
    setRefinementResult(null);
    setPendingHarness(null);
    setRefinementError(null);
  }, [payload?.artifact_version_id, payload?.artifactVersionId]);

  useEffect(() => {
    if (reviewArtifactVersionId === (payload?.artifact_version_id || payload?.artifactVersionId || null)) {
      setArtifactReview(payload?.review || null);
    }
  }, [payload?.review, payload?.artifact_version_id, payload?.artifactVersionId, reviewArtifactVersionId]);

  const headerText = useMemo(() => payload?.title || 'App Workbench', [payload]);

  const subtitle = useMemo(() => {
    if (codingResult) return 'Review the checks for this refinement before accepting it.';
    const agentMsg = payload?.agent_message || payload?.description || null;
    if (agentMsg && typeof agentMsg === 'string') return agentMsg;
    if (validationStatus === 'passed') {
      return 'Try your app, request changes, then review it for activation.';
    }
    if (validationStatus === 'skipped') {
      return 'This draft has not been validated. Required checks must pass before export or activation.';
    }
    if (validationStatus === 'failed') {
      return 'Validation failed. Review errors and retry.';
    }
    return 'Checks are incomplete. Review the draft; export and activation require passed checks.';
  }, [payload, validationStatus, codingResult]);

  const panelClass = workflowSurfaceStyles.darkPanel;

  const toolbarBtn = workflowToolbarButtonClass;

  const showSplit = view === 'split';
  const showCode = view === 'code-only';
  const showPreview = view === 'preview-only';
  const scopeFiles = useMemo(() => {
    if (!selectedPath) return {};
    return { [selectedPath]: currentContent };
  }, [selectedPath, currentContent]);
  const canApplyScopedRefinement = Boolean(
    artifactVersionId &&
    refinementRequest.trim()
  );

  useEffect(() => {
    let cancelled = false;
    setArtifactReviewNotice(null);
    async function loadReview() {
      if (!reviewArtifactVersionId || !buildRegistryId) {
        if (!cancelled) {
          setArtifactReview(null);
          setArtifactReviewError(null);
        }
        return;
      }
      setArtifactReviewBusy(true);
      setArtifactReviewError(null);
      setArtifactReview(null);
      try {
        const response = await studioFetch(`/api/studio/build/artifacts/${encodeURIComponent(reviewArtifactVersionId)}/review${artifactQuery}`);
        const body = await response.json().catch(() => ({ detail: response.statusText }));
        if (!response.ok) {
          throw new Error(body.detail || 'Artifact review could not be loaded.');
        }
        if (!cancelled) {
          setArtifactReview(body.review || null);
        }
      } catch (error) {
        if (!cancelled) {
          setArtifactReviewError(error instanceof Error ? error.message : 'Artifact review could not be loaded.');
        }
      } finally {
        if (!cancelled) {
          setArtifactReviewBusy(false);
        }
      }
    }
    loadReview();
    return () => { cancelled = true; };
  }, [reviewArtifactVersionId, artifactQuery, buildRegistryId]);

  const buildRefinementTriggerPayload = (isThemeRefinement = false) => {
    const triggerPayload = {
      refinement_request: {
        artifact_kind: artifactKind,
        artifact_key: artifactKey,
        artifact_version_id: artifactVersionId,
        raw_user_request: refinementRequest.trim(),
        source_surface: 'app_workbench',
      },
    };

    if (isThemeRefinement) {
      // Carry the current theme config so ThemeCapture can use it as
      // parent_theme_config. Must live inside refinement_request.extra because
      // RefinementRequest uses extra="forbid" — top-level unknown fields fail
      // Pydantic validation on the server.
      const themeSource = filesMap?.[THEME_FILE_PATH];
      const parentTheme = themeSource ? JSON.parse(themeSource) : null;
      if (parentTheme && typeof parentTheme === 'object' && !Array.isArray(parentTheme)) {
        triggerPayload.refinement_request.extra = {
          ...(triggerPayload.refinement_request.extra || {}),
          parent_theme_config: parentTheme,
        };
      }
      // The theme is a file in this saved app bundle, not a separate artifact.
      if (themeSource != null) {
        triggerPayload.coding_request = {
          files: { [THEME_FILE_PATH]: themeSource },
        };
      }
    } else if (limitToSelectedFile && selectedPath && scopeFiles[selectedPath] != null) {
      triggerPayload.coding_request = {
        files: scopeFiles,
      };
    } else {
      // Missing explicit files asks the existing harness to propose a safe scope.
      triggerPayload.coding_request = {};
    }

    return triggerPayload;
  };

  // Shared handler for any refinement response. Saved candidates remain
  // inspectable even when validation prevents advancing the preview baseline.
  const handleRefinementResponse = (response, submission = null) => {
    if (!response) {
      if (currentWorkflowError) setRefinementError(currentWorkflowError);
      return;
    }
    const result = refinementOutput(response);
    if (result) {
      setPendingHarness(null);
      const nextVersionId = result?.metadata?.build_record_id;
      if (nextVersionId) setReviewArtifactVersionId(nextVersionId);
      if (result?.status === 'validated' && nextVersionId) {
        const appliedFiles = result.applied_files || {};
        if (typeof appliedFiles === 'object' && Object.keys(appliedFiles).length > 0) {
          setFilesMap((current) => ({ ...(current || {}), ...appliedFiles }));
        }
        setActiveArtifactVersionId(nextVersionId);
      }
      setRefinementResult(response);
      return;
    }
    if (response.execution_mode === 'harness_decision') {
      // A routing question does not replace the active candidate's evidence.
      setPendingHarness({ response, submission });
    }
  };

  const pendingSubmission = pendingHarness?.submission;
  const pendingRequest = pendingSubmission?.triggerPayload?.refinement_request;
  const currentHarnessDecision = pendingSubmission?.selection === selectionRef.current
    && pendingSubmission?.buildRegistryId === buildRegistryId
    && pendingRequest?.artifact_version_id === artifactVersionId
    && pendingRequest?.raw_user_request === refinementRequest.trim()
      ? pendingHarness?.response?.harness_decision : null;

  // AppReview can hand an already finished inline refinement to this surface.
  // It must use the same saved-draft/validation rules as a request made here.
  useEffect(() => {
    if (payload.refinement_result) handleRefinementResponse(payload.refinement_result);
  }, [payload.refinement_result]);

  const handleApplyScopedRefinement = async () => {
    setRefinementError(null);
    if (!artifactVersionId) {
      setRefinementError('This build has not been saved as a refinable version yet. Wait for generation to finish, then try again.');
      return;
    }
    if (!refinementRequest.trim()) {
      setRefinementError('Describe the change you want first.');
      return;
    }
    const selection = selectionRef.current;
    refinementSelectionRef.current = selection;
    setPendingHarness(null);
    const triggerPayload = buildRefinementTriggerPayload();
    const response = await startWorkflow(
      null,
      {},
      { trigger_source: 'refinement', build_registry_id: buildRegistryId, trigger_payload: triggerPayload }
    );
    if (selectionRef.current === selection) handleRefinementResponse(response, { triggerPayload, selection, buildRegistryId });
  };

  const handleThemeRefinement = async () => {
    setRefinementError(null);
    if (!artifactVersionId) {
      setRefinementError('This build has not been saved as a refinable version yet. Wait for generation to finish, then try again.');
      return;
    }
    if (!refinementRequest.trim()) {
      setRefinementError('Describe the theme change you want first.');
      return;
    }
    const selection = selectionRef.current;
    refinementSelectionRef.current = selection;
    setPendingHarness(null);
    const triggerPayload = buildRefinementTriggerPayload(true);
    const response = await startWorkflow(
      null,
      {},
      { trigger_source: 'refinement', build_registry_id: buildRegistryId, trigger_payload: triggerPayload }
    );
    if (selectionRef.current === selection) handleRefinementResponse(response, { triggerPayload, selection, buildRegistryId });
  };

  const handleHarnessDecisionAction = async (action) => {
    if (action?.action_type === 'review_patch') {
      if (!savedDraftId) {
        setRefinementError('No saved draft is available to review.');
        return;
      }
      setReviewArtifactVersionId(savedDraftId);
      artifactReviewRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
      artifactReviewRef.current?.focus({ preventScroll: true });
      return;
    }
    if (!action || !refinementRequest.trim() || !artifactVersionId) return;
    setRefinementError(null);
    if (!currentHarnessDecision?.actions?.some(item => item.action_id === action.action_id)) {
      setRefinementError('This scope decision is no longer current. Submit the change again.');
      return;
    }
    const triggerPayload = {
      ...pendingSubmission.triggerPayload,
      harness_action: { action_id: action.action_id },
      ...(pendingHarness.response.change_request_id ? { change_request_id: pendingHarness.response.change_request_id } : {}),
      ...(pendingHarness.response.revision_id ? { revision_id: pendingHarness.response.revision_id } : {}),
    };
    if (['run_recommended_workflow', 'confirm_recommended_workflow'].includes(action.action_id)) {
      delete triggerPayload.coding_request;
    } else if (action.action_id === 'apply_proposed_scope') {
      const paths = currentHarnessDecision.selected_paths || [];
      if (!paths.length || paths.some(path => !Object.hasOwn(filesMap, path))) {
        setRefinementError('The proposed files are unavailable in this version. Submit the change again.');
        return;
      }
      triggerPayload.coding_request = {
        ...(triggerPayload.coding_request || {}),
        files: Object.fromEntries(paths.map(path => [path, filesMap[path]])),
      };
    }
    const selection = selectionRef.current;
    refinementSelectionRef.current = selection;
    setPendingHarness(null);
    const response = await startWorkflow(
      null,
      {},
      { trigger_source: 'refinement', build_registry_id: buildRegistryId, trigger_payload: triggerPayload }
    );
    if (selectionRef.current === selection) handleRefinementResponse(response, { triggerPayload, selection, buildRegistryId });
  };

  const handleArtifactReviewAction = async (action) => {
    if (!reviewArtifactVersionId || !action || refinementStarting) return;
    const identity = reviewIdentity;
    const selection = selectionRef.current;
    const isCurrent = () => selectionRef.current === selection && reviewIdentityRef.current === identity;
    setArtifactReviewBusy(true);
    setArtifactReviewError(null);
    setArtifactReviewNotice(null);
    try {
      const response = await studioFetch(`/api/studio/build/artifacts/${encodeURIComponent(reviewArtifactVersionId)}/${action}${artifactQuery}`, {
        method: 'POST',
      });
      const body = await response.json().catch(() => ({ detail: response.statusText }));
      if (!response.ok) {
        throw new Error(body.detail || `Artifact ${action} failed.`);
      }
      const confirmation = { accept: 'accepted', reject: 'rejected', promote: 'promoted' }[action];
      if (body[confirmation] !== true) throw new Error(`Artifact ${action} was not confirmed. Refresh the review before retrying.`);
      if (isCurrent()) {
        setArtifactReview(body.review || null);
        setArtifactReviewNotice(action === 'promote'
          ? body.restart_required
            ? 'Version activated. Restart the app to load this version.'
            : 'Version activated.'
          : action === 'accept' ? 'Draft accepted. Activate it when you are ready.' : 'Draft rejected.');
      }
    } catch (error) {
      if (isCurrent()) setArtifactReviewError(error instanceof Error ? error.message : `Artifact ${action} failed.`);
    } finally {
      if (isCurrent()) setArtifactReviewBusy(false);
    }
  };

  return (
    <div className={panelClass}>
      <div className="px-4 py-3 border-b border-white/10 bg-black/40">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="text-white font-bold font-heading text-sm">{headerText}</div>
            <div className="text-xs text-[var(--color-text-muted)] mt-1">{subtitle}</div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <button type="button" className={toolbarBtn(showSplit)} onClick={() => setView('split')} title="Split view">
              <LayoutGrid className="w-4 h-4" /> Split
            </button>
            <button type="button" className={toolbarBtn(showCode)} onClick={() => setView('code-only')} title="Code view">
              <Code className="w-4 h-4" /> Code
            </button>
            <button type="button" className={toolbarBtn(showPreview)} onClick={() => setView('preview-only')} title="Preview view">
              <Monitor className="w-4 h-4" /> Preview
            </button>
          </div>
        </div>
      </div>

      <div className="p-4 space-y-4">
        <BuildStatusPane
          config={config}
          validationResult={validationResult}
          validationStatus={validationStatus}
          validationStrategy={validationStrategy}
          integrationTestResult={integrationTestResult}
          integrationPassed={integrationPassed}
        />

        <div className={['grid gap-4', showSplit ? 'grid-cols-12' : 'grid-cols-12'].join(' ')}>
          {(showSplit || showCode) && (
            <div className={showSplit ? 'col-span-3' : 'col-span-4'}>
              <FileTreePane
                filesMap={filesMap}
                config={config}
                selectedPath={selectedPath}
                onSelectFile={setSelectedPath}
              />
            </div>
          )}

          {(showSplit || showCode) && (
            <div className={showSplit ? 'col-span-5' : 'col-span-8'}>
              <CodeEditorPane
                config={config}
                filePath={selectedPath}
                content={currentContent}
                onChange={(val) => updateFileContent(selectedPath, val)}
              />
              <div className="text-[10px] text-[var(--color-text-muted)] mt-2">
                Edits here stay in your browser only. To really change your app, describe the change below and apply it.
              </div>
            </div>
          )}

          {(showSplit || showPreview) && (
            <div className={showSplit ? 'col-span-4' : 'col-span-12'}>
              <PreviewPane
                previewUrl={livePreviewUrl}
                sandboxStatus={sandboxStatus}
                sandboxSyncing={sandboxSyncing}
                sandboxError={sandboxSyncError}
                artifactVersionId={artifactVersionId}
                previewArtifactId={previewArtifactId}
                refinementPending={refinementStarting && !anotherVersionIsRefining}
                config={config}
                onStartPreview={() => syncAndRestart(filesMap)}
                onStopPreview={sandboxId ? stopPreview : null}
                sandboxStopping={sandboxStopping}
                canStartPreview={Boolean(artifactVersionId && buildRegistryId && Object.keys(filesMap || {}).length > 0)}
              />
            </div>
          )}
        </div>

        <div className="rounded-2xl border border-white/10 bg-black/20 p-4">
          <div className="flex items-start justify-between gap-3">
            <div>
              <div className="text-sm font-semibold text-white">Refine your app</div>
            </div>
            <div className="text-right text-[10px] text-[var(--color-text-muted)]">
              <div>{limitToSelectedFile && selectedPath ? selectedPath : 'Entire app'}</div>
              {!artifactVersionId && <div>Waiting for this build to be saved…</div>}
            </div>
          </div>

          <textarea
            value={refinementRequest}
            onChange={(event) => setRefinementRequest(event.target.value)}
            aria-label="App change request"
            placeholder="Describe the change"
            className="mt-3 min-h-24 w-full rounded-xl border border-white/10 bg-black/30 px-3 py-2 text-sm text-white outline-none transition focus:border-[rgba(var(--color-primary-rgb),0.45)]"
          />

          <div className="mt-3 flex flex-wrap items-center gap-3">
            {(showCode || showSplit || limitToSelectedFile) && <label className="flex items-center gap-2 text-xs text-[var(--color-text-muted)]">
              <input
                type="checkbox"
                checked={limitToSelectedFile}
                onChange={(event) => setLimitToSelectedFile(event.target.checked)}
                disabled={!selectedPath || refinementStarting}
              />
              Limit to selected file
            </label>}
            <button
              type="button"
              className={toolbarBtn(canApplyScopedRefinement && !refinementStarting)}
              disabled={!canApplyScopedRefinement || refinementStarting}
              onClick={handleApplyScopedRefinement}
            >
              {anotherVersionIsRefining ? 'Working on another version…' : refinementStarting ? 'Applying…' : 'Apply change'}
            </button>
            <button
              type="button"
              className={toolbarBtn(canApplyScopedRefinement && !refinementStarting)}
              disabled={!canApplyScopedRefinement || refinementStarting}
              onClick={handleThemeRefinement}
              title="Change your app's colors, fonts, or visual identity."
            >
              {refinementStarting ? 'Applying...' : 'Redesign theme'}
            </button>
            <div className="text-xs text-[var(--color-text-muted)]">
              Use <span className="font-medium text-white/70">Apply change</span> for features, code, or layout.
              Use <span className="font-medium text-white/70">Redesign theme</span> for colors, fonts, or full visual identity.
            </div>
          </div>

          {(refinementError || currentWorkflowError) && (
            <div className="mt-3 rounded-xl border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-200">
              {refinementError || currentWorkflowError}
            </div>
          )}

          {codingResult && (
            <div role="status" aria-label="Refinement result" className={`mt-3 rounded-xl border px-3 py-3 text-xs ${codingResultTone}`}>
              <div className="font-semibold">{codingResultMessage}</div>
              <details className="mt-2">
                <summary className="cursor-pointer">Refinement details</summary>
                {codingResult.plan?.summary && <div className="mt-1">{codingResult.plan.summary}</div>}
                <div className="mt-1 break-words [overflow-wrap:anywhere]">
                  Status: {codingResult.status}
                  {savedDraftId ? ` • Draft ${savedDraftId}` : ''}
                </div>
              </details>
              {savedDraftId && codingResult.status !== 'validated' && (
                <div className="mt-1">The editor and preview still show version {artifactVersionId}. Inspect the saved draft below.</div>
              )}
              {codingResult.error && <div className="mt-1">{codingResult.error}</div>}
              {savedDraftId && (
                <button type="button" className="mt-2 underline" onClick={() => handleHarnessDecisionAction({ action_type: 'review_patch' })}>
                  Review patch
                </button>
              )}
            </div>
          )}

          {currentHarnessDecision && (
            <div className="mt-3">
              <HarnessDecisionCard
                decision={currentHarnessDecision}
                busy={refinementStarting}
                error={refinementError || currentWorkflowError}
                onAction={handleHarnessDecisionAction}
                className="border-white/10 bg-black/20"
              />
            </div>
          )}
        </div>

        <section ref={artifactReviewRef} tabIndex={-1} aria-label="Artifact review">
        {artifactReviewBusy && <p role="status" className="text-xs text-[var(--color-text-muted)]">Loading artifact review…</p>}
        {artifactReviewNotice && <p role="status" className="mb-3 text-sm text-emerald-200">{artifactReviewNotice}</p>}
        {artifactReviewError && (
          <div role="alert" className="mt-3 rounded-xl border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-200">
            {artifactReviewError}
          </div>
        )}
        {artifactReview && (
          <div className="rounded-2xl border border-white/10 bg-black/20 p-4">
            <div className="flex items-start justify-between gap-3">
              <div>
                <div className="text-sm font-semibold text-white">Review this draft</div>
                <div className="mt-1 text-xs text-[var(--color-text-muted)]">
                  {artifactReview.can_accept
                    ? 'Accept the draft when you are satisfied. Activation is a separate step.'
                    : artifactReview.can_promote
                      ? 'Review this version before making it the active app.'
                      : 'Review the saved version and its checks.'}
                </div>
              </div>
              <div className="text-[10px] text-[var(--color-text-muted)]">
                {artifactReview.changed_file_count || 0} changed file{artifactReview.changed_file_count === 1 ? '' : 's'}
              </div>
            </div>

            <details className="mt-3 text-xs text-[var(--color-text-muted)]">
              <summary className="cursor-pointer">Version and check details</summary>
              <div className="mt-2 break-words [overflow-wrap:anywhere]">Version {reviewArtifactVersionId}</div>
              <div className="mt-1">
                Lifecycle: {artifactReview.lifecycle_status} · Validation: {artifactReview.validation_status} · Review: {artifactReview.review_status}
              </div>
            {artifactReview.selected_paths?.length > 0 && (
              <div className="mt-3 flex flex-wrap gap-2">
                {artifactReview.selected_paths.map((path) => (
                  <button
                    key={path}
                    type="button"
                    onClick={() => { setSelectedPath(path); setView('code-only'); }}
                    className="rounded-lg border border-white/10 bg-white/5 px-2 py-1 font-mono text-[11px] text-[var(--color-text-muted)] transition hover:bg-white/10"
                  >
                    {path}
                  </button>
                ))}
              </div>
            )}
            </details>

            {artifactReview.coding_summary && (
              <details className="mt-3 text-xs text-[var(--color-text-muted)]">
                <summary className="cursor-pointer">Change summary</summary>
                <p className="mt-2 break-words [overflow-wrap:anywhere]">{artifactReview.coding_summary}</p>
              </details>
            )}

            {artifactReview.validation_blocker && (
              <div className="mt-3 rounded-xl border border-amber-400/30 bg-amber-400/10 px-3 py-2 text-xs text-amber-100">
                {artifactReview.validation_blocker}
              </div>
            )}

            {reviewNotes.length > 0 && (
              <details className="mt-3 text-xs text-[var(--color-text-muted)]">
                <summary className="cursor-pointer text-amber-200">Review notes ({reviewNotes.length})</summary>
                <ul className="mt-2 space-y-2 break-words [overflow-wrap:anywhere]">
                  {reviewNotes.map((note, index) => <li key={index}>{note}</li>)}
                </ul>
              </details>
            )}

            {(artifactValidationCommands.length > 0 || artifactValidationFallbacks.length > 0) && (
              <details className="mt-3 text-xs text-[var(--color-text-muted)]">
                <summary className="cursor-pointer">Validation commands</summary>
              <div className="mt-2 grid gap-2 sm:grid-cols-2">
                {artifactValidationCommands.slice(0, 4).map((item) => (
                  <div key={`${item.kind}:${item.command}`} className="rounded-xl border border-white/10 bg-black/30 px-3 py-2">
                    <div className="break-all font-mono text-[11px] text-white">{item.command}</div>
                    <div className="mt-1">{item.status} · {item.reason}</div>
                  </div>
                ))}
                {artifactValidationFallbacks.slice(0, 4).map((item) => (
                  <div key={item.name} className="rounded-xl border border-white/10 bg-black/30 px-3 py-2">
                    <div className="break-all font-mono text-[11px] text-white">{item.name}</div>
                    <div className="mt-1">{item.status} · {item.reason}</div>
                  </div>
                ))}
              </div>
              </details>
            )}

            {(artifactReview.can_accept || artifactReview.can_reject || artifactReview.can_promote) && (
              <div className="mt-3 flex flex-wrap gap-3">
                {artifactReview.can_accept && (
                  <button
                    type="button"
                    className={toolbarBtn(!artifactReviewBusy && !refinementStarting)}
                    disabled={artifactReviewBusy || refinementStarting}
                    onClick={() => handleArtifactReviewAction('accept')}
                  >
                    {artifactReviewBusy ? 'Working...' : 'Accept artifact'}
                  </button>
                )}
                {artifactReview.can_reject && (
                  <button
                    type="button"
                    className={toolbarBtn(!artifactReviewBusy && !refinementStarting)}
                    disabled={artifactReviewBusy || refinementStarting}
                    onClick={() => handleArtifactReviewAction('reject')}
                  >
                    {artifactReviewBusy ? 'Working...' : 'Reject artifact'}
                  </button>
                )}
                {artifactReview.can_promote && (
                  <button
                    type="button"
                    className={toolbarBtn(!artifactReviewBusy && !refinementStarting)}
                    disabled={artifactReviewBusy || refinementStarting}
                    onClick={() => handleArtifactReviewAction('promote')}
                  >
                    {artifactReviewBusy ? 'Working...' : 'Activate this draft'}
                  </button>
                )}
              </div>
            )}

            {artifactReview.changed_files?.length > 0 && (
              <details className="mt-4 text-xs text-[var(--color-text-muted)]">
                <summary className="cursor-pointer">Code changes ({artifactReview.changed_file_count || artifactReview.changed_files.length})</summary>
              <div className="mt-3 space-y-3">
                {artifactReview.changed_files.slice(0, 6).map((file) => (
                  <div key={`${file.change_type}:${file.path}`} className="rounded-xl border border-white/10 bg-black/30 p-3">
                    <div className="flex items-center justify-between gap-3">
                      <button
                        type="button"
                        onClick={() => { setSelectedPath(file.path); setView('code-only'); }}
                        className="min-w-0 break-words [overflow-wrap:anywhere] font-mono text-xs text-white transition hover:text-[var(--color-primary)]"
                      >
                        {file.path}
                      </button>
                      <span className="rounded-lg border border-white/10 bg-white/5 px-2 py-0.5 text-[10px] uppercase tracking-[0.14em] text-[var(--color-text-muted)]">
                        {file.change_type}
                      </span>
                    </div>
                    {file.diff_preview && (
                      <pre className="mt-3 max-h-56 overflow-auto rounded-lg border border-white/10 bg-black/40 p-3 text-[11px] leading-5 text-[var(--color-text-muted)] whitespace-pre-wrap">
                        {file.diff_preview}
                      </pre>
                    )}
                  </div>
                ))}
              </div>
              </details>
            )}
          </div>
        )}
        </section>

        {canShowExportActions && (
          <div className="pt-2">
            <ExportActions
              payload={{ ...exportPayload, title: 'Finish this step' }}
              collapseDetails
              onResponse={onResponse}
              toolName={toolName}
              toolCallId={toolCallId}
              sourceWorkflowName={sourceWorkflowName}
              generatedWorkflowName={generatedWorkflowName}
            />
          </div>
        )}
      </div>
    </div>
  );
};

export default AppWorkbench;
