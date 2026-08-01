import React from "react";
import { getComponent } from "../../registry/componentRegistry";

function activityComponentCandidates(metadata = {}) {
  const workflowName = String(metadata.workflow_name || metadata.workflow || "").trim();
  const componentType = String(
    metadata.activity_component_type
    || metadata.component_type
    || metadata.component
    || ""
  ).trim();
  const displayVariant = String(metadata.activity_display_variant || metadata.display_variant || "").trim();

  return [
    workflowName && componentType ? `${workflowName}:${componentType}` : null,
    workflowName && displayVariant ? `${workflowName}:${displayVariant}` : null,
    componentType || null,
    displayVariant || null,
  ].filter(Boolean);
}

export function resolveActivityComponent(metadata = {}) {
  for (const candidate of activityComponentCandidates(metadata)) {
    const Component = getComponent(candidate);
    if (Component) return Component;
  }
  return null;
}

export default function ActivityRenderer({ metadata = {}, message = "" }) {
  const Component = resolveActivityComponent(metadata);
  if (!Component) return null;

  return <Component metadata={metadata} message={message} />;
}
