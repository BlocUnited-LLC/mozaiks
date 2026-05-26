/**
 * DiscoveryBriefCard — Review summary for the ExistingAppDiscovery artifacts.
 *
 * This surfaces the current host product summary plus the first recommended
 * Mozaiks augmentation move. It is intentionally a review card, not a generic
 * data dump of every discovery field.
 */

import { useState } from 'react';

export default function DiscoveryBriefCard({ payload = {} }) {
  const {
    app_name = 'Unknown App',
    app_description = '',
    tech_stack = '',
    brand_theme_summary = '',
    brand_theme_evidence = {},
    adoption_level = 'embed',
    adoption_rationale = '',
    theme_adaptation_strategy = '',
    embed_theme_ready = false,
    discovery_brief = '',
    capability_count = 0,
    ai_accessible_count = 0,
    service_surface_count = 0,
    route_surface_count = 0,
    capabilities = [],
    unresolved_questions = [],
    auth_model = '',
    auth_delegation_model = '',
    ui_surface_preference = '',
    initial_workflows = [],
    ecosystem_bindings = [],
    artifact_version = '1.0',
  } = payload;

  const {
    appearance = null,
    colors = [],
    fonts = [],
    layout_hints = [],
  } = brand_theme_evidence;

  const [expandedSection, setExpandedSection] = useState('brief');

  const levelLabels = {
    embed: 'Embed',
    bridge: 'Bridge',
    ecosystem: 'Ecosystem',
    gradual_modernization: 'Gradual Modernization',
  };

  const levelDescriptions = {
    embed: 'Add Mozaiks surfaces to the host app without bridging backend capabilities yet.',
    bridge: 'AI surface + backend capability bridge for read/write access.',
    ecosystem: 'Bridge the host app and attach selected Mozaiks ecosystem modules.',
    gradual_modernization: 'Move approved surfaces toward Mozaiks-owned modules or overlays over time.',
  };

  const confidenceColors = {
    confirmed: 'text-success',
    inferred: 'text-warning',
    unverified: 'text-muted-foreground',
  };

  const priorityColors = {
    high: 'text-destructive',
    medium: 'text-warning',
    low: 'text-muted-foreground',
  };

  const toggleSection = (section) => {
    setExpandedSection(expandedSection === section ? null : section);
  };

  return (
    <div className="rounded-lg border border-border bg-card p-5 space-y-4">
      {/* Header */}
      <div className="flex items-start justify-between">
        <div>
          <h2 className="text-xl font-semibold text-foreground">{app_name}</h2>
          <p className="text-sm text-muted-foreground mt-1">{app_description}</p>
        </div>
        <span className="rounded-full bg-primary/15 px-3 py-1 text-xs font-medium text-primary">
          {levelLabels[adoption_level] || adoption_level}
        </span>
      </div>

      {/* Stats bar */}
      <div className="flex flex-wrap gap-4 text-sm">
        <div className="rounded-md bg-muted px-3 py-2">
          <span className="text-muted-foreground">Stack: </span>
          <span className="text-foreground font-medium">{tech_stack}</span>
        </div>
        <div className="rounded-md bg-muted px-3 py-2">
          <span className="text-muted-foreground">Capabilities: </span>
          <span className="text-foreground font-medium">{capability_count}</span>
        </div>
        <div className="rounded-md bg-muted px-3 py-2">
          <span className="text-muted-foreground">AI-accessible: </span>
          <span className="text-foreground font-medium">{ai_accessible_count}</span>
        </div>
        <div className="rounded-md bg-muted px-3 py-2">
          <span className="text-muted-foreground">Services: </span>
          <span className="text-foreground font-medium">{service_surface_count}</span>
        </div>
        <div className="rounded-md bg-muted px-3 py-2">
          <span className="text-muted-foreground">Routes: </span>
          <span className="text-foreground font-medium">{route_surface_count}</span>
        </div>
        <div className="rounded-md bg-muted px-3 py-2">
          <span className="text-muted-foreground">Theme Ready: </span>
          <span className={`font-medium ${embed_theme_ready ? 'text-success' : 'text-warning'}`}>
            {embed_theme_ready ? 'Yes' : 'Needs refinement'}
          </span>
        </div>
      </div>

      {/* Discovery Brief (collapsible) */}
      <div className="border border-border rounded-md">
        <button
          className="w-full flex items-center justify-between p-3 text-left text-sm font-medium text-foreground hover:bg-muted/50"
          onClick={() => toggleSection('brief')}
        >
          Discovery Brief
          <span className="text-muted-foreground">{expandedSection === 'brief' ? '−' : '+'}</span>
        </button>
        {expandedSection === 'brief' && (
          <div className="px-3 pb-3 text-sm text-muted-foreground whitespace-pre-line">
            {discovery_brief}
          </div>
        )}
      </div>

      <div className="border border-border rounded-md">
        <button
          className="w-full flex items-center justify-between p-3 text-left text-sm font-medium text-foreground hover:bg-muted/50"
          onClick={() => toggleSection('theme')}
        >
          Brand Theme
          <span className="text-muted-foreground">{expandedSection === 'theme' ? '−' : '+'}</span>
        </button>
        {expandedSection === 'theme' && (
          <div className="px-3 pb-3 space-y-3 text-sm">
            <div className="rounded-md bg-muted/50 p-3">
              <p className="text-foreground font-medium">Host Brand Summary</p>
              <p className="text-muted-foreground text-xs mt-1 whitespace-pre-line">
                {brand_theme_summary || 'No deterministic host-brand summary captured yet.'}
              </p>
            </div>
            {(appearance || colors.length > 0 || fonts.length > 0 || layout_hints.length > 0) && (
              <div className="grid grid-cols-1 gap-2">
                {appearance && (
                  <div className="rounded-md bg-muted/50 px-3 py-2">
                    <span className="text-muted-foreground text-xs">Appearance</span>
                    <p className="text-foreground text-sm capitalize">{appearance}</p>
                  </div>
                )}
                {colors.length > 0 && (
                  <div className="rounded-md bg-muted/50 px-3 py-2">
                    <span className="text-muted-foreground text-xs">Colors</span>
                    <p className="text-foreground text-sm">{colors.join(', ')}</p>
                  </div>
                )}
                {fonts.length > 0 && (
                  <div className="rounded-md bg-muted/50 px-3 py-2">
                    <span className="text-muted-foreground text-xs">Fonts</span>
                    <p className="text-foreground text-sm">{fonts.join(', ')}</p>
                  </div>
                )}
                {layout_hints.length > 0 && (
                  <div className="rounded-md bg-muted/50 px-3 py-2">
                    <span className="text-muted-foreground text-xs">Layout Hints</span>
                    <p className="text-foreground text-sm">{layout_hints.join(', ')}</p>
                  </div>
                )}
              </div>
            )}
          </div>
        )}
      </div>

      {/* Capabilities (collapsible) */}
      <div className="border border-border rounded-md">
        <button
          className="w-full flex items-center justify-between p-3 text-left text-sm font-medium text-foreground hover:bg-muted/50"
          onClick={() => toggleSection('capabilities')}
        >
          Capabilities ({capabilities.length})
          <span className="text-muted-foreground">{expandedSection === 'capabilities' ? '−' : '+'}</span>
        </button>
        {expandedSection === 'capabilities' && (
          <div className="px-3 pb-3 space-y-2">
            {capabilities.map((cap, idx) => (
              <div key={idx} className="flex items-center justify-between text-sm rounded-md bg-muted/50 px-3 py-2">
                <div>
                  <p className="text-foreground">{cap.name}</p>
                  {cap.delivery_surface && (
                    <p className="text-xs text-muted-foreground mt-0.5">{cap.delivery_surface}</p>
                  )}
                </div>
                <div className="flex items-center gap-2">
                  <span className={`text-xs ${confidenceColors[cap.confidence] || 'text-muted-foreground'}`}>
                    {cap.confidence}
                  </span>
                  {cap.agent_ready && (
                    <span className="rounded-full bg-success/15 px-2 py-0.5 text-xs text-success">
                      Ready
                    </span>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Integration Details (collapsible) */}
      <div className="border border-border rounded-md">
        <button
          className="w-full flex items-center justify-between p-3 text-left text-sm font-medium text-foreground hover:bg-muted/50"
          onClick={() => toggleSection('integration')}
        >
          Integration Plan
          <span className="text-muted-foreground">{expandedSection === 'integration' ? '−' : '+'}</span>
        </button>
        {expandedSection === 'integration' && (
          <div className="px-3 pb-3 space-y-2 text-sm">
            <div className="rounded-md bg-muted/50 p-3">
              <p className="text-foreground font-medium">
                {levelLabels[adoption_level] || adoption_level}
              </p>
              <p className="text-muted-foreground text-xs mt-1">
                {levelDescriptions[adoption_level] || ''}
              </p>
              {adoption_rationale && (
                <p className="text-muted-foreground text-xs mt-2 whitespace-pre-line">
                  {adoption_rationale}
                </p>
              )}
            </div>
            <div className="grid grid-cols-2 gap-2">
              <div className="rounded-md bg-muted/50 px-3 py-2">
                <span className="text-muted-foreground text-xs">Auth Model</span>
                <p className="text-foreground text-sm">{auth_model}</p>
              </div>
              <div className="rounded-md bg-muted/50 px-3 py-2">
                <span className="text-muted-foreground text-xs">Auth Delegation</span>
                <p className="text-foreground text-sm">{auth_delegation_model}</p>
              </div>
              <div className="rounded-md bg-muted/50 px-3 py-2 col-span-2">
                <span className="text-muted-foreground text-xs">UI Surface</span>
                <p className="text-foreground text-sm">{ui_surface_preference}</p>
              </div>
              <div className="rounded-md bg-muted/50 px-3 py-2 col-span-2">
                <span className="text-muted-foreground text-xs">Theme Adaptation</span>
                <p className="text-foreground text-sm">{theme_adaptation_strategy || 'Not specified'}</p>
              </div>
            </div>
            {(initial_workflows.length > 0 || ecosystem_bindings.length > 0) && (
              <div className="grid grid-cols-1 gap-2">
                {initial_workflows.length > 0 && (
                  <div className="rounded-md bg-muted/50 px-3 py-2">
                    <span className="text-muted-foreground text-xs">Initial Workflows</span>
                    <p className="text-foreground text-sm">{initial_workflows.join(', ')}</p>
                  </div>
                )}
                {ecosystem_bindings.length > 0 && (
                  <div className="rounded-md bg-muted/50 px-3 py-2">
                    <span className="text-muted-foreground text-xs">Ecosystem Bindings</span>
                    <p className="text-foreground text-sm">{ecosystem_bindings.join(', ')}</p>
                  </div>
                )}
              </div>
            )}
          </div>
        )}
      </div>

      {/* Unresolved Questions */}
      {unresolved_questions.length > 0 && (
        <div className="border border-border rounded-md">
          <button
            className="w-full flex items-center justify-between p-3 text-left text-sm font-medium text-foreground hover:bg-muted/50"
            onClick={() => toggleSection('questions')}
          >
            Unresolved Questions ({unresolved_questions.length})
            <span className="text-muted-foreground">{expandedSection === 'questions' ? '−' : '+'}</span>
          </button>
          {expandedSection === 'questions' && (
            <div className="px-3 pb-3 space-y-2">
              {unresolved_questions.map((q, idx) => (
                <div key={idx} className="rounded-md bg-warning/10 border border-warning/30 px-3 py-2 text-sm">
                  <p className="text-foreground">{q.question}</p>
                  <span className={`text-xs ${priorityColors[q.priority] || 'text-muted-foreground'}`}>
                    {q.priority} priority
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Footer */}
      <div className="flex items-center justify-between text-xs text-muted-foreground pt-2 border-t border-border">
        <span>Existing App Artifact v{artifact_version}</span>
        <span>Review the host product and first augmentation move</span>
      </div>
    </div>
  );
}
