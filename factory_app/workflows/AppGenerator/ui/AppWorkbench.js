// ==============================================================================
// FILE: factory_app/workflows/AppGenerator/ui/AppWorkbench.js
// DESCRIPTION: AppGenerator artifact canvas (files + Monaco + preview + export)
// ==============================================================================

import React, { useMemo, useState } from 'react';
import { Code, LayoutGrid, Monitor } from 'lucide-react';
import { useWorkflowStart } from '@mozaiks/chat-ui/hooks/useWorkflowStart.js';
import { components as designComponents, typography as designTypography } from '@mozaiks/chat-ui/platform/workflowSurfaceStyles.js';
import { useAppValidationWorkbench } from './useAppValidationWorkbench';
import BuildStatusArtifact from './BuildStatusArtifact';
import CodeEditorArtifact from './CodeEditorArtifact';
import E2BPreviewArtifact from './E2BPreviewArtifact';
import ExportActions from './ExportActions';
import FileTreeArtifact from './FileTreeArtifact';
import HarnessDecisionCard from '../../../app/ui/components/HarnessDecisionCard.jsx';

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
  const defaultView = layoutCfg.defaultView || 'split';
  const [view, setView] = useState(defaultView);
  const [refinementRequest, setRefinementRequest] = useState('');
  const [refinementResult, setRefinementResult] = useState(null);
  const [refinementError, setRefinementError] = useState(null);
  const [artifactReview, setArtifactReview] = useState(payload?.review || null);
  const [artifactReviewBusy, setArtifactReviewBusy] = useState(false);
  const [artifactReviewError, setArtifactReviewError] = useState(null);
  const { startWorkflow, starting: refinementStarting, error: workflowStartError } = useWorkflowStart();
  const [activeArtifactVersionId, setActiveArtifactVersionId] = useState(
    payload?.artifact_version_id || payload?.artifactVersionId || null
  );

  const {
    filesMap,
    setFilesMap,
    selectedPath,
    setSelectedPath,
    currentContent,
    updateFileContent,
    previewUrl,
    validationResult,
    validationStatus,
    validationStrategy,
    integrationTestResult,
    integrationPassed,
  } = useAppValidationWorkbench(payload, config);
  const artifactVersionId = activeArtifactVersionId;
  const artifactKind = payload?.artifact_kind || payload?.artifactKind || 'app_bundle';
  const artifactKey = payload?.artifact_key || payload?.artifactKey || artifactKind;

  React.useEffect(() => {
    setActiveArtifactVersionId(payload?.artifact_version_id || payload?.artifactVersionId || null);
  }, [payload?.artifact_version_id, payload?.artifactVersionId]);

  React.useEffect(() => {
    setArtifactReview(payload?.review || null);
  }, [payload?.review]);

  const headerText = useMemo(() => payload?.title || 'App Workbench', [payload]);

  const subtitle = useMemo(() => {
    const agentMsg = payload?.agent_message || payload?.description || null;
    if (agentMsg && typeof agentMsg === 'string') return agentMsg;
    if (validationStatus === 'passed') {
      return 'Validation passed. Review code, preview, and export.';
    }
    if (validationStatus === 'skipped') {
      return 'Validation was explicitly skipped. Review the generated bundle before export.';
    }
    if (validationStatus === 'failed') {
      return 'Validation failed. Review errors and retry.';
    }
    return 'Validation is pending. Review the bundle and wait for validation or skip explicitly.';
  }, [payload, validationStatus]);

  const panelClass = [designComponents.panel.artifact, 'p-0 overflow-hidden'].join(' ');

  const toolbarBtn = (active) =>
    [
      'px-3 py-1.5 rounded-lg text-xs font-semibold transition-colors inline-flex items-center gap-2',
      active
        ? 'bg-[rgba(var(--color-primary-rgb),0.25)] border border-[rgba(var(--color-primary-rgb),0.35)] text-white'
        : 'bg-white/5 hover:bg-white/10 border border-white/10 text-[var(--color-text-secondary)]',
    ].join(' ');

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

  React.useEffect(() => {
    let cancelled = false;
    async function loadReview() {
      if (!artifactVersionId) {
        if (!cancelled) {
          setArtifactReview(null);
          setArtifactReviewError(null);
        }
        return;
      }
      setArtifactReviewBusy(true);
      setArtifactReviewError(null);
      try {
        const response = await fetch(`/api/studio/build/artifacts/${encodeURIComponent(artifactVersionId)}/review`);
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
  }, [artifactVersionId]);

  const buildRefinementTriggerPayload = (harnessAction = null) => {
    const payload = {
      refinement_request: {
        artifact_kind: artifactKind,
        artifact_key: artifactKey,
        artifact_version_id: artifactVersionId,
        raw_user_request: refinementRequest.trim(),
        source_surface: 'app_workbench',
      },
    };
    if (selectedPath && scopeFiles[selectedPath] != null) {
      payload.coding_request = {
        files: scopeFiles,
        validation_strategy: validationStrategy || 'skip',
      };
    }
    if (harnessAction && typeof harnessAction === 'object') {
      payload.harness_action = harnessAction;
    }
    return payload;
  };

  const handleApplyScopedRefinement = async () => {
    setRefinementError(null);
    setRefinementResult(null);
    if (!artifactVersionId) {
      setRefinementError('This workbench does not have an artifact version yet, so scoped refinement cannot branch from it.');
      return;
    }
    if (!refinementRequest.trim()) {
      setRefinementError('Describe the patch you want to apply.');
      return;
    }
    const response = await startWorkflow(
      null,
      {},
      {
        trigger_source: 'refinement',
        trigger_payload: buildRefinementTriggerPayload(),
      }
    );

    if (response?.execution_mode === 'coding_worker') {
      const appliedFiles = response?.coding_worker?.applied_files || {};
      if (appliedFiles && typeof appliedFiles === 'object' && Object.keys(appliedFiles).length > 0) {
        setFilesMap((prev) => ({ ...(prev || {}), ...appliedFiles }));
      }
      const nextArtifactVersionId = response?.coding_worker?.metadata?.artifact_version_id || null;
      if (nextArtifactVersionId) {
        setActiveArtifactVersionId(nextArtifactVersionId);
      }
      setRefinementResult(response);
      return;
    }

    if (response?.execution_mode === 'harness_decision') {
      setRefinementResult(response);
      return;
    }

    if (!response && workflowStartError) {
      setRefinementError(workflowStartError);
    }
  };

  const handleHarnessDecisionAction = async (action) => {
    if (!action || !refinementRequest.trim() || !artifactVersionId) return;
    setRefinementError(null);
    const response = await startWorkflow(
      null,
      {},
      {
        trigger_source: 'refinement',
        trigger_payload: buildRefinementTriggerPayload({ action_id: action.action_id }),
      }
    );
    if (response?.execution_mode === 'coding_worker' || response?.execution_mode === 'harness_decision') {
      setRefinementResult(response);
    }
  };

  const handleArtifactReviewAction = async (action) => {
    if (!artifactVersionId || !action) return;
    setArtifactReviewBusy(true);
    setArtifactReviewError(null);
    try {
      const response = await fetch(`/api/studio/build/artifacts/${encodeURIComponent(artifactVersionId)}/${action}`, {
        method: 'POST',
      });
      const body = await response.json().catch(() => ({ detail: response.statusText }));
      if (!response.ok) {
        throw new Error(body.detail || `Artifact ${action} failed.`);
      }
      setArtifactReview(body.review || null);
    } catch (error) {
      setArtifactReviewError(error instanceof Error ? error.message : `Artifact ${action} failed.`);
    } finally {
      setArtifactReviewBusy(false);
    }
  };

  return (
    <div className={panelClass}>
      <div className="px-4 py-3 border-b border-white/10 bg-black/40">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className={['text-white font-bold', designTypography.heading].join(' ')}>{headerText}</div>
            <div className="text-xs text-[var(--color-text-muted)] mt-1">{subtitle}</div>
            <div className="text-[10px] text-[var(--color-text-muted)] mt-1">
              {generatedWorkflowName || sourceWorkflowName || 'AppGenerator'} • event {toolCallId || 'n/a'}
            </div>
          </div>
          <div className="flex items-center gap-2 flex-shrink-0">
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
        <BuildStatusArtifact
          config={config}
          validationResult={validationResult}
          validationStatus={validationStatus}
          validationStrategy={validationStrategy}
          integrationTestResult={integrationTestResult}
          integrationPassed={integrationPassed}
        />

        {artifactReview && (
          <div className="rounded-2xl border border-white/10 bg-black/20 p-4">
            <div className="flex items-start justify-between gap-3">
              <div>
                <div className="text-sm font-semibold text-white">Artifact Review</div>
                <div className="mt-1 text-xs text-[var(--color-text-muted)]">
                  Lifecycle: {artifactReview.lifecycle_status} · Validation: {artifactReview.validation_status} · Review: {artifactReview.review_status}
                </div>
              </div>
              <div className="text-[10px] text-[var(--color-text-muted)]">
                {artifactReview.changed_file_count || 0} changed file{artifactReview.changed_file_count === 1 ? '' : 's'}
              </div>
            </div>

            {artifactReview.selected_paths?.length > 0 && (
              <div className="mt-3 flex flex-wrap gap-2">
                {artifactReview.selected_paths.map((path) => (
                  <button
                    key={path}
                    type="button"
                    onClick={() => setSelectedPath(path)}
                    className="rounded-lg border border-white/10 bg-white/5 px-2 py-1 font-mono text-[11px] text-[var(--color-text-muted)] transition hover:bg-white/10"
                  >
                    {path}
                  </button>
                ))}
              </div>
            )}

            {artifactReview.coding_summary && (
              <div className="mt-3 rounded-xl border border-white/10 bg-black/30 px-3 py-2 text-xs text-[var(--color-text-muted)]">
                {artifactReview.coding_summary}
              </div>
            )}

            {(artifactReview.can_accept || artifactReview.can_reject || artifactReview.can_promote) && (
              <div className="mt-3 flex flex-wrap gap-3">
                {artifactReview.can_accept && (
                  <button
                    type="button"
                    className={toolbarBtn(!artifactReviewBusy)}
                    disabled={artifactReviewBusy}
                    onClick={() => handleArtifactReviewAction('accept')}
                  >
                    {artifactReviewBusy ? 'Working...' : 'Accept artifact'}
                  </button>
                )}
                {artifactReview.can_reject && (
                  <button
                    type="button"
                    className={toolbarBtn(!artifactReviewBusy)}
                    disabled={artifactReviewBusy}
                    onClick={() => handleArtifactReviewAction('reject')}
                  >
                    {artifactReviewBusy ? 'Working...' : 'Reject artifact'}
                  </button>
                )}
                {artifactReview.can_promote && (
                  <button
                    type="button"
                    className={toolbarBtn(!artifactReviewBusy)}
                    disabled={artifactReviewBusy}
                    onClick={() => handleArtifactReviewAction('promote')}
                  >
                    {artifactReviewBusy ? 'Working...' : 'Promote to app root'}
                  </button>
                )}
              </div>
            )}

            {artifactReview.changed_files?.length > 0 && (
              <div className="mt-4 space-y-3">
                {artifactReview.changed_files.slice(0, 6).map((file) => (
                  <div key={`${file.change_type}:${file.path}`} className="rounded-xl border border-white/10 bg-black/30 p-3">
                    <div className="flex items-center justify-between gap-3">
                      <button
                        type="button"
                        onClick={() => setSelectedPath(file.path)}
                        className="font-mono text-xs text-white transition hover:text-[var(--color-primary)]"
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
            )}

            {artifactReviewError && (
              <div className="mt-3 rounded-xl border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-200">
                {artifactReviewError}
              </div>
            )}
          </div>
        )}

        <div className="rounded-2xl border border-white/10 bg-black/20 p-4">
          <div className="flex items-start justify-between gap-3">
            <div>
              <div className="text-sm font-semibold text-white">Scoped Refinement</div>
              <div className="mt-1 text-xs text-[var(--color-text-muted)]">
                Send the current file through the control-plane harness for a codex-backed patch refinement.
              </div>
            </div>
            <div className="text-right text-[10px] text-[var(--color-text-muted)]">
              <div>Scope: {selectedPath || 'select a file'}</div>
              <div>Mode: {selectedPath ? 'explicit file scope' : 'auto scope proposal'}</div>
              <div>Artifact: {artifactVersionId || 'missing artifact version'}</div>
            </div>
          </div>

          <textarea
            value={refinementRequest}
            onChange={(event) => setRefinementRequest(event.target.value)}
            placeholder="Describe the patch you want applied. Select a file for explicit scope, or let the harness infer scope from the artifact workspace."
            className="mt-3 min-h-24 w-full rounded-xl border border-white/10 bg-black/30 px-3 py-2 text-sm text-white outline-none transition focus:border-[rgba(var(--color-primary-rgb),0.45)]"
          />

          <div className="mt-3 flex flex-wrap items-center gap-3">
            <button
              type="button"
              className={toolbarBtn(canApplyScopedRefinement && !refinementStarting)}
              disabled={!canApplyScopedRefinement || refinementStarting}
              onClick={handleApplyScopedRefinement}
            >
              {refinementStarting ? 'Applying...' : 'Apply scoped refinement'}
            </button>
            <div className="text-xs text-[var(--color-text-muted)]">
              If a file is selected, it is sent as explicit coding scope. Otherwise the control-plane harness proposes scope from the artifact workspace.
            </div>
          </div>

          {(refinementError || workflowStartError) && (
            <div className="mt-3 rounded-xl border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-200">
              {refinementError || workflowStartError}
            </div>
          )}

          {refinementResult?.coding_worker && (
            <div className="mt-3 rounded-xl border border-emerald-500/30 bg-emerald-500/10 px-3 py-3 text-xs text-emerald-100">
              <div className="font-semibold text-emerald-50">
                {refinementResult.coding_worker.plan?.summary || 'Scoped refinement applied.'}
              </div>
              <div className="mt-1">
                Status: {refinementResult.coding_worker.status}
                {refinementResult.coding_worker.metadata?.artifact_version_id
                  ? ` • Artifact ${refinementResult.coding_worker.metadata.artifact_version_id}`
                  : ''}
              </div>
            </div>
          )}

          {refinementResult?.harness_decision && (
            <div className="mt-3">
              <HarnessDecisionCard
                decision={refinementResult.harness_decision}
                busy={refinementStarting}
                error={refinementError || workflowStartError}
                onAction={handleHarnessDecisionAction}
                className="border-white/10 bg-black/20"
              />
            </div>
          )}
        </div>

        <div className={['grid gap-4', showSplit ? 'grid-cols-12' : 'grid-cols-12'].join(' ')}>
          {(showSplit || showCode) && (
            <div className={showSplit ? 'col-span-3' : 'col-span-4'}>
              <FileTreeArtifact
                filesMap={filesMap}
                config={config}
                selectedPath={selectedPath}
                onSelectFile={setSelectedPath}
              />
            </div>
          )}

          {(showSplit || showCode) && (
            <div className={showSplit ? 'col-span-5' : 'col-span-8'}>
              <CodeEditorArtifact
                config={config}
                filePath={selectedPath}
                content={currentContent}
                onChange={(val) => updateFileContent(selectedPath, val)}
              />
              <div className="text-[10px] text-[var(--color-text-muted)] mt-2">
                Editor changes are local to your browser session (not yet synced back to the runtime).
              </div>
            </div>
          )}

          {(showSplit || showPreview) && (
            <div className={showSplit ? 'col-span-4' : 'col-span-12'}>
              <E2BPreviewArtifact previewUrl={previewUrl} config={config} />
            </div>
          )}
        </div>

        {showExportActions && (
          <div className="pt-2">
            <ExportActions
              payload={payload}
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
