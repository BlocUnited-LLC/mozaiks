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

export function useAppValidationWorkbench(payload, themeConfig) {
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

  const validationResult = useMemo(() => {
    return normalizeValidation(
      workbench.validation_result ||
      workbench.validationResult ||
      workbench.app_validation_result ||
      workbench.appValidationResult ||
      workbench.validation ||
      {}
    );
  }, [workbench]);

  const validationStatus = useMemo(() => {
    const explicitStatus =
      workbench.app_validation_status ??
      workbench.appValidationStatus ??
      validationResult.validation_status ??
      validationResult.validationStatus;
    const normalized = normalizeValidationStatus(explicitStatus);
    if (normalized) return normalized;
    if (validationResult.success === true) return 'passed';
    if (validationResult.success === false) return 'failed';
    return 'pending';
  }, [workbench, validationResult]);

  const validationStrategy = useMemo(() => {
    return normalizeValidationStrategy(
      workbench.app_validation_strategy_used ||
      workbench.appValidationStrategyUsed ||
      validationResult.validation_strategy ||
      validationResult.validationStrategy ||
      null
    );
  }, [workbench, validationResult]);

  const previewUrl = useMemo(() => {
    return safeString(
      workbench.preview_url ||
      workbench.previewUrl ||
      workbench.app_validation_preview_url ||
      workbench.appValidationPreviewUrl ||
      validationResult.preview_url ||
      validationResult.previewUrl ||
      ''
    ) || null;
  }, [workbench, validationResult]);

  const integrationTestResult = useMemo(() => {
    return normalizeIntegrationResult(
      workbench.integration_test_result ||
      workbench.integrationTestResult ||
      workbench.integration_result ||
      workbench.integrationResult ||
      null
    );
  }, [workbench]);

  const integrationPassed = useMemo(() => {
    const passed =
      workbench.integration_tests_passed ??
      workbench.integrationTestsPassed ??
      integrationTestResult?.passed ??
      integrationTestResult?.success ??
      null;
    if (passed == null) return null;
    return Boolean(passed);
  }, [workbench, integrationTestResult]);

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
