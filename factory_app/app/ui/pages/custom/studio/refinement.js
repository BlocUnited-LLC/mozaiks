export const REFINEMENT_CHANGE_CLASSES = [
  {
    id: 'patch',
    label: 'Patch',
    icon: '🔧',
    description: 'Fix a specific issue or tweak a targeted file. Fastest path - stays within scoped files only.',
  },
  {
    id: 'design',
    label: 'Design',
    icon: '🎨',
    description: 'Revise the visual direction, layout, or schema design without changing core logic.',
  },
  {
    id: 'feature',
    label: 'Feature',
    icon: '✨',
    description: 'Add a new capability. Extends the existing plan while preserving what was built.',
  },
  {
    id: 'core',
    label: 'Core change',
    icon: '🔄',
    description: 'Fundamental change to the concept or goal. Restarts from the planning phase.',
    isDestructive: true,
  },
]

const REFINEMENT_PLACEHOLDERS = {
  patch: 'Describe the specific fix (e.g. "The login button does not redirect correctly")',
  design: 'Describe the design change (e.g. "Make the dashboard feel more minimal and dark")',
  feature: 'Describe the new capability (e.g. "Add a notifications panel for admin users")',
  core: 'Describe what needs to fundamentally change about this product',
}

export function getRefinementRequestPlaceholder(changeClass) {
  return REFINEMENT_PLACEHOLDERS[changeClass] || 'Describe the change you want. Mozaiks will classify and route it automatically.'
}

export function buildRefinementTriggerPayload({
  changeClass,
  artifactKind,
  artifactKey = null,
  artifactVersionId = null,
  rawUserRequest = null,
  sourceSurface = null,
}) {
  const trimmedRequest = typeof rawUserRequest === 'string' ? rawUserRequest.trim() : ''
  const refinementRequest = {
    artifact_kind: artifactKind,
    artifact_key: artifactKey || artifactKind,
    raw_user_request: trimmedRequest,
  }

  if (changeClass) {
    refinementRequest.declared_change_class = changeClass
  }

  if (artifactVersionId) {
    refinementRequest.artifact_version_id = artifactVersionId
  }
  if (sourceSurface) {
    refinementRequest.source_surface = sourceSurface
  }

  return {
    refinement_request: refinementRequest,
  }
}
