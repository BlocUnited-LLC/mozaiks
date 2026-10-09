export const refinementOutput = (response) => {
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
