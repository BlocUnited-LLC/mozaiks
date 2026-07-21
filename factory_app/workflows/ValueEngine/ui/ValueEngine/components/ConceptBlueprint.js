// ==============================================================================
// FILE: ChatUI/src/workflows/ValueEngine/components/ConceptBlueprint.js
// DESCRIPTION: ValueEngine "Concept Blueprint" artifact (display-only)
// ==============================================================================

import { useMemo } from 'react';
import {
  Bot,
  Layers3,
  ListChecks,
  MonitorSmartphone,
  Palette,
  Route,
  Sparkles,
  Target,
} from 'lucide-react';
import { workflowSurfaceStyles } from '@mozaiks/chat-ui/platform/workflowSurfaceStyles.js';

const asText = (value) => (typeof value === 'string' ? value.trim() : '');

const asArray = (value) => (Array.isArray(value) ? value : []);

const renderList = (items) => {
  const list = asArray(items).filter((x) => typeof x === 'string' && x.trim());
  if (!list.length) return <div className="text-xs text-muted-foreground">None</div>;
  return (
    <ul className="space-y-1">
      {list.map((item, idx) => (
        <li key={`${item}-${idx}`} className="text-sm text-muted-foreground">
          - {item}
        </li>
      ))}
    </ul>
  );
};

const ConceptBlueprint = ({ payload = {}, toolCallId, sourceWorkflowName, generatedWorkflowName }) => {
  const blueprint = payload && typeof payload.blueprint === 'object' ? payload.blueprint : null;
  const endpoints = asArray(payload.api_endpoints).filter((x) => x && typeof x === 'object');

  const title = asText(payload.title) || 'Concept Blueprint';
  const appId = asText(payload.app_id);
  const conceptOverview = asText(payload.concept_overview);

  const appName = useMemo(() => {
    const candidates = [
      blueprint?.app_name,
      blueprint?.appName,
      blueprint?.name,
      blueprint?.app,
      payload?.workflow?.name,
    ];
    return candidates.map(asText).find(Boolean) || null;
  }, [blueprint, payload]);

  const valueProp = useMemo(() => {
    const candidates = [blueprint?.value_proposition, blueprint?.valueProposition, blueprint?.tagline];
    return candidates.map(asText).find(Boolean) || null;
  }, [blueprint]);

  const targetUser = useMemo(() => {
    const candidates = [blueprint?.target_user, blueprint?.targetUser, blueprint?.target_users?.[0]?.persona];
    return candidates.map(asText).find(Boolean) || null;
  }, [blueprint]);

  const brandIntent = blueprint?.brand_intent || blueprint?.brandIntent || null;
  const brandStyleSummary = useMemo(() => {
    const candidates = [brandIntent?.style_summary, brandIntent?.styleSummary];
    return candidates.map(asText).find(Boolean) || null;
  }, [brandIntent]);
  const appearanceHint = useMemo(() => {
    const candidates = [brandIntent?.appearance_hint, brandIntent?.appearanceHint];
    return candidates.map(asText).find(Boolean) || null;
  }, [brandIntent]);

  const coreFeatures = blueprint?.mvp_scope?.core_features || blueprint?.mvp_scope?.coreFeatures;
  const deferredFeatures = blueprint?.mvp_scope?.deferred_features || blueprint?.mvp_scope?.deferredFeatures;
  const differentiators = blueprint?.unique_differentiators || blueprint?.uniqueDifferentiators;
  const brandKeywords = blueprint?.brand_intent?.brand_keywords || blueprint?.brandIntent?.brandKeywords;
  const experienceGoals = blueprint?.brand_intent?.experience_goals || blueprint?.brandIntent?.experienceGoals;
  const appUiRequirements = blueprint?.app_ui_requirements || blueprint?.appUiRequirements;
  const capabilityPackHints = blueprint?.capability_pack_hints || blueprint?.capabilityPackHints;
  const agenticCapabilities = blueprint?.agentic_capabilities || blueprint?.agenticCapabilities;

  const panelClass = workflowSurfaceStyles.primaryPanel;

  return (
    <div className={panelClass}>
      <div className="px-5 py-4 border-b border-white/10 bg-black/35">
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="flex items-center gap-3">
              <div className="rounded-lg bg-primary/20 p-2.5 ring-2 ring-primary/50">
                <Sparkles className="w-5 h-5 text-[var(--color-primary-light)]" />
              </div>
              <div className="min-w-0">
                <div className="text-xl font-heading font-black text-foreground">{title}</div>
                {appName && (
                  <div className="text-sm text-muted-foreground truncate">{appName}</div>
                )}
              </div>
            </div>
            <div className="mt-2 text-[11px] text-muted-foreground">
              {generatedWorkflowName || sourceWorkflowName || 'ValueEngine'} • event {toolCallId || 'n/a'}
              {appId ? ` • app_id ${appId}` : null}
            </div>
          </div>
          {valueProp && (
            <div className="inline-flex items-center gap-2 rounded-lg border-2 border-primary/45 bg-primary/12 text-primary px-4 py-2 text-xs font-sans font-bold uppercase tracking-wide max-w-[360px]">
              <Target className="w-4 h-4" />
              <span className="truncate">{valueProp}</span>
            </div>
          )}
        </div>
      </div>

      <div className="p-5 gap-5">
        <div className="rounded-lg border border-border bg-muted/75 p-5">
          <div className="flex items-center gap-2 mb-2">
            <Layers3 className="w-4 h-4 text-[var(--color-primary-light)]" />
            <div className="text-sm font-heading font-bold text-foreground">Overview</div>
          </div>
          <div className="text-sm whitespace-pre-wrap text-muted-foreground">
            {conceptOverview || 'No concept_overview provided.'}
          </div>
        </div>

        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <div className="rounded-xl border border-border bg-card p-6">
            <div className="flex items-center gap-2 mb-2">
              <ListChecks className="w-4 h-4 text-[var(--color-primary-light)]" />
              <div className="text-sm font-heading font-bold text-foreground">MVP Scope</div>
            </div>
            <div className="grid grid-cols-1 gap-3">
              <div>
                <div className="text-xs text-muted-foreground mb-1">Core features</div>
                {renderList(coreFeatures)}
              </div>
              <div>
                <div className="text-xs text-muted-foreground mb-1">Deferred features</div>
                {renderList(deferredFeatures)}
              </div>
            </div>
          </div>

          <div className="rounded-xl border border-border bg-card p-6">
            <div className="flex items-center gap-2 mb-2">
              <Target className="w-4 h-4 text-[var(--color-primary-light)]" />
              <div className="text-sm font-heading font-bold text-foreground">Positioning</div>
            </div>
            <div className="space-y-3">
              <div>
                <div className="text-xs text-muted-foreground mb-1">Target user</div>
                <div className="text-sm text-muted-foreground">{targetUser || 'Not specified'}</div>
              </div>
              <div>
                <div className="text-xs text-muted-foreground mb-1">Unique differentiators</div>
                {renderList(differentiators)}
              </div>
            </div>
          </div>
        </div>

        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <div className="rounded-xl border border-border bg-card p-6">
            <div className="flex items-center gap-2 mb-2">
              <Palette className="w-4 h-4 text-[var(--color-primary-light)]" />
              <div className="text-sm font-heading font-bold text-foreground">Brand Direction</div>
            </div>
            <div className="space-y-3">
              <div>
                <div className="text-xs text-muted-foreground mb-1">Style summary</div>
                <div className="text-sm text-muted-foreground">
                  {brandStyleSummary || 'Not specified'}
                </div>
              </div>
              <div>
                <div className="text-xs text-muted-foreground mb-1">Appearance hint</div>
                <div className="text-sm text-muted-foreground">{appearanceHint || 'Not specified'}</div>
              </div>
              <div>
                <div className="text-xs text-muted-foreground mb-1">Brand keywords</div>
                {renderList(brandKeywords)}
              </div>
              <div>
                <div className="text-xs text-muted-foreground mb-1">Experience goals</div>
                {renderList(experienceGoals)}
              </div>
            </div>
          </div>

          <div className="rounded-xl border border-border bg-card p-6">
            <div className="flex items-center gap-2 mb-2">
              <MonitorSmartphone className="w-4 h-4 text-[var(--color-primary-light)]" />
              <div className="text-sm font-heading font-bold text-foreground">Experience Hints</div>
            </div>
            <div className="space-y-3">
              <div>
                <div className="text-xs text-muted-foreground mb-1">App UI requirements</div>
                {renderList(appUiRequirements)}
              </div>
              <div>
                <div className="text-xs text-muted-foreground mb-1">Capability pack hints</div>
                {renderList(capabilityPackHints)}
              </div>
              <div>
                <div className="text-xs text-muted-foreground mb-1">Agentic capabilities</div>
                <div className="flex items-center gap-2">
                  <Bot className="w-4 h-4 text-[var(--color-primary-light)]" />
                  <div className="min-w-0 flex-1">{renderList(agenticCapabilities)}</div>
                </div>
              </div>
            </div>
          </div>
        </div>

        <div className="rounded-xl border border-border bg-card p-6">
          <div className="flex items-center gap-2 mb-2">
            <Route className="w-4 h-4 text-[var(--color-primary-light)]" />
            <div className="text-sm font-heading font-bold text-foreground">API Endpoints</div>
            <div className="text-xs text-muted-foreground">{endpoints.length ? `(${endpoints.length})` : ''}</div>
          </div>

          {!endpoints.length ? (
            <div className="text-xs text-muted-foreground">No api_endpoints provided.</div>
          ) : (
            <div className="overflow-auto">
              <table className="w-full text-left text-sm">
                <thead>
                  <tr className="text-xs text-muted-foreground">
                    <th className="py-2 pr-3 font-semibold">Method</th>
                    <th className="py-2 pr-3 font-semibold">Path</th>
                    <th className="py-2 font-semibold">Description</th>
                  </tr>
                </thead>
                <tbody className="align-top">
                  {endpoints.slice(0, 50).map((ep, idx) => (
                    <tr key={`ep-${idx}`} className="border-t border-border">
                      <td className="py-2 pr-3 font-mono text-muted-foreground">{asText(ep.method) || '-'}</td>
                      <td className="py-2 pr-3 font-mono text-muted-foreground">{asText(ep.path) || '-'}</td>
                      <td className="py-2 text-muted-foreground">{asText(ep.description) || asText(ep.name) || ''}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default ConceptBlueprint;

