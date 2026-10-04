// ==============================================================================
// FILE: factory_app/workflows/AppGenerator/ui/useAppValidationWorkbench.js
// DESCRIPTION: Lightweight state + normalization helpers for AppWorkbench
// ==============================================================================

import { useEffect, useMemo, useState } from 'react';

const safeString = (v) => (typeof v === 'string' ? v : v == null ? '' : String(v));

const normalizeFilesMap = (raw) => {
  const out = {};
  if (!raw || typeof raw !== 'object') return out;
  for (const [k, v] of Object.entries(raw)) {
    if (!k) continue;
    if (typeof v === 'string') out[k] = v;
    else if (v != null) out[k] = String(v);
  }
  return out;
};

const normalizeValidation = (raw) => {
  if (!raw || typeof raw !== 'object') return {};
  return raw;
};

const normalizeValidationStatus = (raw) => {
  if (raw == null) return null;
  const value = safeString(raw).trim().toLowerCase();
  return value || null;
};

const normalizeValidationStrategy = (raw) => {
  if (raw == null) return null;
  const value = safeString(raw).trim().toLowerCase();
  return value || null;
};

const normalizeIntegrationResult = (raw) => {
  if (!raw || typeof raw !== 'object') return null;
  return raw;
};

const pickDefaultFile = (filesMap) => {
  const keys = Object.keys(filesMap || {});
  if (!keys.length) return null;
  const preferred = ['package.json', 'README.md', 'src/main.jsx', 'src/main.tsx', 'src/App.jsx', 'src/App.tsx'];
  for (const p of preferred) {
    if (filesMap[p] != null) return p;
  }
  return keys.sort((a, b) => a.localeCompare(b))[0];
};

export function useAppValidationWorkbench(payload, themeConfig, candidateResult = null) {
  const workbench = useMemo(() => {
    if (!payload || typeof payload !== 'object') return {};
    return payload.workbench && typeof payload.workbench === 'object' ? payload.workbench : payload;
  }, [payload]);

  const initialFiles = useMemo(() => {
    return normalizeFilesMap(
      workbench.generated_files ||
      workbench.generatedFiles ||
      workbench.files_map ||
      workbench.filesMap ||
      workbench.files ||
      {}
    );
  }, [workbench]);

  const [filesMap, setFilesMap] = useState(initialFiles);
  useEffect(() => setFilesMap(initialFiles), [initialFiles]);

  // A refinement carries evidence for its own exact snapshot. Never fill gaps
  // from the parent bundle, even while the editor keeps that previous version.
  const validationWorkbench = useMemo(() => {
    if (!candidateResult) return workbench;
    const evidence = normalizeValidation(candidateResult.validation_result);
    const build = normalizeValidation(evidence.app_validation_result);
    const acceptance = normalizeIntegrationResult(evidence.app_bundle_acceptance_result);
    const status = evidence.validation_status || (candidateResult.status === 'failed' ? 'failed' : 'pending');
    const hasPassedChecks = acceptance?.passed === true && build.validation_status === 'passed';
    return {
      validation_result: { ...build, ...evidence },
      app_validation_status: status === 'passed' && !hasPassedChecks ? 'pending' : status,
      app_validation_strategy_used: build.validation_strategy || evidence.validation_strategy || null,
      app_validation_preview_url: build.preview_url || null,
      integration_test_result: acceptance,
      integration_tests_passed: acceptance?.passed ?? null,
    };
  }, [candidateResult, workbench]);

  const validationResult = useMemo(() => {
    return normalizeValidation(
      validationWorkbench.validation_result ||
      validationWorkbench.validationResult ||
      validationWorkbench.app_validation_result ||
      validationWorkbench.appValidationResult ||
      validationWorkbench.validation ||
      {}
    );
  }, [validationWorkbench]);

  const validationStatus = useMemo(() => {
    const explicitStatus =
      validationWorkbench.app_validation_status ??
      validationWorkbench.appValidationStatus ??
      validationResult.validation_status ??
      validationResult.validationStatus;
    const normalized = normalizeValidationStatus(explicitStatus);
    if (normalized) return normalized;
    if (validationResult.success === true) return 'passed';
    if (validationResult.success === false) return 'failed';
    return 'pending';
  }, [validationWorkbench, validationResult]);

  const validationStrategy = useMemo(() => {
    return normalizeValidationStrategy(
      validationWorkbench.app_validation_strategy_used ||
      validationWorkbench.appValidationStrategyUsed ||
      validationResult.validation_strategy ||
      validationResult.validationStrategy ||
      null
    );
  }, [validationWorkbench, validationResult]);

  const previewUrl = useMemo(() => {
    return safeString(
      validationWorkbench.preview_url ||
      validationWorkbench.previewUrl ||
      validationWorkbench.app_validation_preview_url ||
      validationWorkbench.appValidationPreviewUrl ||
      validationResult.preview_url ||
      validationResult.previewUrl ||
      ''
    ) || null;
  }, [validationWorkbench, validationResult]);

  const integrationTestResult = useMemo(() => {
    return normalizeIntegrationResult(
      validationWorkbench.integration_test_result ||
      validationWorkbench.integrationTestResult ||
      validationWorkbench.integration_result ||
      validationWorkbench.integrationResult ||
      null
    );
  }, [validationWorkbench]);

  const integrationPassed = useMemo(() => {
    const passed =
      validationWorkbench.integration_tests_passed ??
      validationWorkbench.integrationTestsPassed ??
      integrationTestResult?.passed ??
      integrationTestResult?.success ??
      null;
    if (passed == null) return null;
    return Boolean(passed);
  }, [validationWorkbench, integrationTestResult]);

  const [selectedPath, setSelectedPath] = useState(() => pickDefaultFile(initialFiles));
  useEffect(() => {
    const next = pickDefaultFile(filesMap);
    setSelectedPath((prev) => (prev && filesMap?.[prev] != null ? prev : next));
  }, [filesMap]);

  const editorConfig = themeConfig?.artifacts?.['code-editor'] || {};

  const currentContent = useMemo(() => {
    if (!selectedPath) return '';
    return filesMap?.[selectedPath] != null ? safeString(filesMap[selectedPath]) : '';
  }, [filesMap, selectedPath]);

  const updateFileContent = (path, nextContent) => {
    if (!path) return;
    setFilesMap((prev) => ({ ...(prev || {}), [path]: safeString(nextContent) }));
  };

  return {
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
    editorConfig,
  };
}
