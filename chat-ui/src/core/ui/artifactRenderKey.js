// Matches RunBuildBinding.BuildIdentity; this scopes React state, not access.
const BUILD_IDENTITY = /^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/;
const isName = value => typeof value === 'string' && value.length > 0 && value.trim() === value;

export function artifactRenderKey(event, fallbackKey) {
  const payload = event?.payload || {};
  const workflow = event?.workflow_name || payload.workflow_name;
  const component = event?.component_type || payload.component_type || event?.tool_name || payload.tool_name;
  const family = payload.artifact_kind ?? payload.build_family;
  if ((event?.awaiting_response ?? payload.awaiting_response) !== false
    || (event?.interaction_type ?? payload.interaction_type) !== 'ui_surface'
    || (event?.display ?? payload.display) !== 'artifact'
    || family !== 'app_bundle'
    || (payload.build_family !== undefined && payload.build_family !== 'app_bundle')
    || !isName(workflow) || !isName(component)
    || typeof payload.target_app_id !== 'string' || !BUILD_IDENTITY.test(payload.target_app_id)
    || typeof payload.build_registry_id !== 'string' || !BUILD_IDENTITY.test(payload.build_registry_id)) {
    return fallbackKey;
  }
  return `app-bundle:${JSON.stringify([workflow, component, payload.target_app_id, payload.build_registry_id])}`;
}
