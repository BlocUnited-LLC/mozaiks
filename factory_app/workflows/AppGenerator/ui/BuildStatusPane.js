// ==============================================================================
// FILE: factory_app/workflows/AppGenerator/ui/BuildStatusPane.js
// DESCRIPTION: Build/test validation status summary
// ==============================================================================

import React, { useMemo, useRef, useEffect, useState } from 'react';
import { AlertTriangle, CheckCircle2, ChevronDown, ChevronUp, XCircle } from 'lucide-react';

const BuildStatusPane = ({
  validationResult = {},
  validationStatus = 'pending',
  validationStrategy = null,
  integrationTestResult = null,
  integrationPassed = null,
  config = {},
}) => {
  const statusCfg = useMemo(() => config?.artifacts?.['build-status'] || {}, [config]);
  const [showLogs, setShowLogs] = useState(statusCfg.showLogs === true);
  const [showWarnings, setShowWarnings] = useState(statusCfg.collapseWarnings === false);
  const logRef = useRef(null);

  const parsedErrors = useMemo(() => {
    const errs = validationResult?.parsed_errors || validationResult?.parsedErrors || [];
    return Array.isArray(errs) ? errs : [];
  }, [validationResult]);

  const warnings = useMemo(() => {
    const w = validationResult?.warnings || [];
    return Array.isArray(w) ? w : [];
  }, [validationResult]);

  const rawErrors = useMemo(() => {
    const e = validationResult?.errors || [];
    return Array.isArray(e) ? e : [];
  }, [validationResult]);

  const logs = useMemo(() => {
    const out = validationResult?.build_output || validationResult?.buildOutput || '';
    return typeof out === 'string' ? out : '';
  }, [validationResult]);

  const integration = useMemo(() => {
    if (!integrationTestResult || typeof integrationTestResult !== 'object') return null;
    return integrationTestResult;
  }, [integrationTestResult]);

  // Checks that did not run (for example the runtime smoke without a
  // database) are never shown as passed.
  const integrationSkipped = useMemo(() => {
    const skipped = integration?.skipped_checks || integration?.skippedChecks || [];
    return Array.isArray(skipped) ? skipped.filter((item) => item && typeof item === 'object') : [];
  }, [integration]);

  const integrationStatus = useMemo(() => {
    if (!integration) return null;
    const passed = integrationPassed ?? integration.passed ?? integration.success ?? null;
    if (passed == null) return null;
    if (!passed) return 'error';
    return integrationSkipped.length ? 'warning' : 'success';
  }, [integration, integrationPassed, integrationSkipped]);

  const integrationChecks = useMemo(() => {
    const c = integration?.checks || integration?.Checks || null;
    return Array.isArray(c) ? c : null;
  }, [integration]);

  const integrationFailures = useMemo(() => {
    if (integrationChecks) {
      return integrationChecks.filter((c) => c && typeof c === 'object' && c.passed === false);
    }
    const failed = integration?.failed_tests || integration?.failedTests || [];
    return Array.isArray(failed) ? failed : [];
  }, [integration, integrationChecks]);

  const integrationWarnings = useMemo(() => {
    const w = integration?.warnings || [];
    return Array.isArray(w) ? w : [];
  }, [integration]);

  const exportGateNote = useMemo(() => {
    if (validationStatus === 'failed') {
      return 'Fix the reported issue and run validation again before exporting.';
    }
    if (validationStatus !== 'passed') {
      return 'Required validation must pass before you can export or activate this draft.';
    }
    if (integrationPassed !== true) {
      return integrationPassed == null
        ? 'Integration checks still need to run before export.'
        : 'Integration checks must pass before export.';
    }
    return null;
  }, [integrationPassed, validationStatus]);

  useEffect(() => {
    if (!statusCfg.autoScroll) return;
    if (!showLogs) return;
    if (!logRef.current) return;
    try {
      logRef.current.scrollTop = logRef.current.scrollHeight;
    } catch {}
  }, [logs, showLogs, statusCfg]);

  const status =
    validationStatus === 'passed'
      ? 'success'
      : validationStatus === 'failed'
        ? 'error'
        : 'warning';
  const Icon = status === 'success' ? CheckCircle2 : status === 'error' ? XCircle : AlertTriangle;
  const color =
    status === 'success'
      ? 'text-[var(--color-success)]'
      : status === 'error'
        ? 'text-[var(--color-error)]'
        : 'text-[var(--color-accent)]';
  const border =
    status === 'success'
      ? 'border-[rgba(var(--color-success-rgb),0.35)]'
      : status === 'error'
        ? 'border-[rgba(var(--color-error-rgb),0.35)]'
        : 'border-[rgba(var(--color-accent-rgb),0.35)]';
  const bg =
    status === 'success'
      ? 'bg-[rgba(var(--color-success-rgb),0.08)]'
      : status === 'error'
        ? 'bg-[rgba(var(--color-error-rgb),0.08)]'
        : 'bg-[rgba(var(--color-accent-rgb),0.08)]';

  return (
    <div className={['rounded-xl border', border, bg].join(' ')}>
      <div className="flex items-start justify-between gap-3 px-4 py-3">
        <div className="flex items-start gap-3">
          <Icon className={['w-5 h-5 mt-0.5', color].join(' ')} />
          <div className="min-w-0">
            <div className={['font-semibold text-sm', color].join(' ')}>
              {validationStatus === 'passed'
                ? 'Validation passed'
                : validationStatus === 'skipped'
                  ? 'Validation skipped'
                  : validationStatus === 'failed'
                    ? 'Validation failed'
                    : 'Validation pending'}
            </div>
            <div className="text-xs text-[var(--color-text-muted)]">
              {validationStatus === 'passed'
                ? 'Build/test completed successfully.'
                : validationStatus === 'skipped'
                  ? 'Build validation did not run. This draft remains unverified.'
                  : validationStatus === 'failed'
                    ? 'Review errors and retry generation/validation.'
                    : 'Validation has not completed yet.'}
            </div>
            {validationStrategy && (
              <div className="mt-1 text-[10px] text-[var(--color-text-muted)]">
                Strategy: <span className="font-mono">{validationStrategy}</span>
              </div>
            )}
            {exportGateNote && (
              <div className="mt-1 text-[10px] text-[var(--color-text-muted)]">
                {exportGateNote}
              </div>
            )}
          </div>
        </div>
        <button
          type="button"
          aria-expanded={showLogs}
          onClick={() => setShowLogs((v) => !v)}
          className="px-3 py-1 rounded-lg bg-white/5 hover:bg-white/10 text-xs text-[var(--color-text-secondary)] transition-colors flex items-center gap-1"
        >
          {showLogs ? 'Hide logs' : 'Show logs'}
          {showLogs ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
        </button>
      </div>

      {integrationStatus && (
        <div className="px-4 pb-3">
          <div className="rounded-lg bg-black/25 border border-white/10 p-3">
            <div className="flex items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                {integrationStatus === 'success' ? (
                  <CheckCircle2 className="w-4 h-4 text-[var(--color-success)]" />
                ) : integrationStatus === 'warning' ? (
                  <AlertTriangle className="w-4 h-4 text-[var(--color-accent)]" />
                ) : (
                  <XCircle className="w-4 h-4 text-[var(--color-error)]" />
                )}
                <div className="text-xs font-semibold text-white">Integration checks</div>
              </div>
              <div className="text-[10px] text-[var(--color-text-muted)]">
                {[
                  integration?.passed_tests != null && integration?.total_tests != null
                    ? `${integration.passed_tests}/${integration.total_tests} passed`
                    : integrationChecks
                      ? `${integrationChecks.filter((c) => c?.passed === true).length}/${integrationChecks.length} passed`
                      : null,
                  integrationSkipped.length > 0 ? `${integrationSkipped.length} skipped` : null,
                ].filter(Boolean).join(' · ')}
              </div>
            </div>
            <div className="mt-1 text-[10px] text-[var(--color-text-muted)]">
              {integration?.note || 'Offline wiring checks only (does not verify live connectivity).'}
            </div>

            {integrationSkipped.length > 0 && (
              <div className="mt-2 space-y-1" data-testid="integration-skipped-checks">
                {integrationSkipped.map((s, idx) => (
                  <div key={idx} className="text-[10px] font-mono text-[var(--color-text-secondary)]">
                    <span className="text-[var(--color-accent)]">{s.id || 'check'} skipped</span>
                    <span className="ml-2">{s.reason || ''}</span>
                  </div>
                ))}
              </div>
            )}

            {integrationFailures.length > 0 && (
              <details className="mt-2 text-xs text-[var(--color-text-secondary)]">
                <summary className="cursor-pointer text-[var(--color-error)]">
                  {integrationFailures.length} failed check(s) to review
                </summary>
                <div className="mt-2 max-h-40 overflow-auto space-y-1 whitespace-pre-wrap [overflow-wrap:anywhere]">
                  {integrationFailures.map((f, idx) => (
                    <div key={idx} className="text-[10px] font-mono text-[var(--color-text-secondary)]">
                      <span className="text-[var(--color-error)]">{f.id || f.test || 'integration_check'}</span>
                      <span className="ml-2">{f.message || f.error || ''}</span>
                    </div>
                  ))}
                </div>
              </details>
            )}

            {integrationWarnings.length > 0 && (
              <details className="mt-2 text-xs text-[var(--color-text-muted)]">
                <summary className="cursor-pointer text-[var(--color-accent)]">
                  {integrationWarnings.length} integration warning(s) to review
                </summary>
                <div className="mt-2 max-h-40 overflow-auto space-y-2 whitespace-pre-wrap [overflow-wrap:anywhere]">
                  {integrationWarnings.map((w, idx) => <div key={idx}>{String(w)}</div>)}
                </div>
              </details>
            )}
          </div>
        </div>
      )}

      {parsedErrors.length > 0 && (
        <details className="px-4 pb-3 text-xs">
          <summary className="cursor-pointer font-semibold text-[var(--color-error)]">{parsedErrors.length} error(s) to review</summary>
          <div className="mt-2 max-h-40 overflow-auto space-y-1 whitespace-pre-wrap [overflow-wrap:anywhere]">
            {parsedErrors.map((e, idx) => (
              <div key={idx} className="text-xs font-mono text-[var(--color-text-secondary)]">
                <span className="text-[var(--color-error)]">{e.file || 'unknown'}:{e.line || '?'}</span>
                <span className="ml-2">{e.message || ''}</span>
              </div>
            ))}
          </div>
        </details>
      )}

      {rawErrors.length > 0 && parsedErrors.length === 0 && (
        <details className="px-4 pb-3 text-xs">
          <summary className="cursor-pointer font-semibold text-[var(--color-error)]">{rawErrors.length} error(s) to review</summary>
          <div className="mt-2 max-h-40 overflow-auto space-y-1 whitespace-pre-wrap [overflow-wrap:anywhere]">
            {rawErrors.map((e, idx) => (
              <div key={idx} className="text-xs font-mono text-[var(--color-text-secondary)]">
                {String(e)}
              </div>
            ))}
          </div>
        </details>
      )}

      {warnings.length > 0 && (
        <div className="px-4 pb-3">
          <button
            type="button"
            aria-expanded={showWarnings}
            onClick={() => setShowWarnings((v) => !v)}
            className="text-xs text-[var(--color-accent)] hover:text-[var(--color-accent-light)] flex items-center gap-1"
          >
            <AlertTriangle className="w-3 h-3" />
            {warnings.length} warning(s)
            {showWarnings ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
          </button>
          {showWarnings && (
            <div className="mt-2 rounded-lg bg-black/30 border border-white/10 p-2 max-h-40 overflow-auto my-scroll1">
              {warnings.map((w, idx) => (
                <div key={idx} className="text-[10px] font-mono text-[var(--color-text-secondary)] whitespace-pre-wrap">
                  {String(w)}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {showLogs && logs && (
        <div className="px-4 pb-4">
          <div ref={logRef} className="rounded-lg bg-black/30 border border-white/10 p-2 max-h-64 overflow-auto my-scroll1">
            <pre className="text-[10px] text-[var(--color-text-secondary)] whitespace-pre-wrap">{logs}</pre>
          </div>
        </div>
      )}
    </div>
  );
};

export default BuildStatusPane;
