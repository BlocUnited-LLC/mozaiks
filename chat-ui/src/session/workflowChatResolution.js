function uniqueTruthy(values) {
  const result = [];
  for (const value of values || []) {
    const normalized = String(value || '').trim();
    if (!normalized || result.includes(normalized)) {
      continue;
    }
    result.push(normalized);
  }
  return result;
}

export function normalizeWorkflowCandidate(workflowConfig, workflowName) {
  const raw = String(workflowName || '').trim();
  if (!raw) {
    return null;
  }
  return workflowConfig?.resolveKnownWorkflowName?.(raw) || raw;
}

export function buildWorkflowResolutionCandidates({
  workflowConfig,
  candidates = [],
  includeAvailable = false,
} = {}) {
  const preferredCandidates = Array.isArray(candidates) ? candidates : [candidates];
  const resolved = preferredCandidates
    .map((candidate) => normalizeWorkflowCandidate(workflowConfig, candidate))
    .filter(Boolean);

  if (includeAvailable && typeof workflowConfig?.getAvailableWorkflows === 'function') {
    const available = workflowConfig.getAvailableWorkflows()
      .map((candidate) => normalizeWorkflowCandidate(workflowConfig, candidate))
      .filter(Boolean);
    resolved.push(...available);
  }

  return uniqueTruthy(resolved);
}

export async function resolveWorkflowForChat({
  api,
  appId,
  chatId,
  workflowCandidates = [],
} = {}) {
  const candidates = uniqueTruthy(workflowCandidates);
  if (!api || !appId || !chatId || candidates.length === 0) {
    return {
      workflowName: null,
      sawMissingWorkflow: false,
      validationIncomplete: true,
    };
  }

  for (const workflowName of candidates) {
    const path = `/api/chats/exists/${encodeURIComponent(appId)}/${encodeURIComponent(workflowName)}/${encodeURIComponent(chatId)}`;
    try {
      let result = null;
      if (typeof api.get === 'function') {
        result = await api.get(path);
      } else if (typeof api.getHttpBaseUrl === 'function') {
        const response = await fetch(`${api.getHttpBaseUrl()}${path}`);
        if (!response.ok) {
          return {
            workflowName: null,
            sawMissingWorkflow: false,
            validationIncomplete: true,
          };
        }
        result = await response.json();
      } else {
        return {
          workflowName: null,
          sawMissingWorkflow: false,
          validationIncomplete: true,
        };
      }

      if (result?.exists === true) {
        return {
          workflowName,
          sawMissingWorkflow: false,
          validationIncomplete: false,
        };
      }
    } catch (error) {
      return {
        workflowName: null,
        sawMissingWorkflow: false,
        validationIncomplete: true,
        error,
      };
    }
  }

  return {
    workflowName: null,
    sawMissingWorkflow: true,
    validationIncomplete: false,
  };
}
