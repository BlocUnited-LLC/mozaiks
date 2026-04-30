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
  return REFINEMENT_PLACEHOLDERS[changeClass] || 'Select a change type above, then describe what you want...'
}

export function buildRefinementTriggerPayload({
  changeClass,
  artifactKind,
  artifactVersionId = null,
  rawUserRequest = null,
}) {
  const payload = {
    change_class: changeClass,
    artifact_kind: artifactKind,
  }

  if (artifactVersionId) {
    payload.artifact_version_id = artifactVersionId
  }

  const trimmedRequest = typeof rawUserRequest === 'string' ? rawUserRequest.trim() : ''
  if (trimmedRequest) {
    payload.raw_user_request = trimmedRequest
  }

  return payload
}