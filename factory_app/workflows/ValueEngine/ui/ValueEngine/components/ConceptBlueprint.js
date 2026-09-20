// ==============================================================================
// ValueEngine concept artifact and structured review.
// ==============================================================================

import { useMemo, useState } from 'react';
import {
  Bot,
  Check,
  Layers3,
  ListChecks,
  MonitorSmartphone,
  Palette,
  PencilLine,
  Route,
  Sparkles,
  Target,
  X,
} from 'lucide-react';
import { workflowSurfaceStyles } from '@mozaiks/chat-ui/platform/workflowSurfaceStyles.js';
import { normalizePrimitiveActions } from '@mozaiks/chat-ui/core/ui/workflowPrimitiveUtils.js';

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

const ConceptBlueprintContent = ({ payload = {}, onResponse, toolCallId, sourceWorkflowName, generatedWorkflowName }) => {
  const [feedback, setFeedback] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [reviewError, setReviewError] = useState('');
  const reviewActions = normalizePrimitiveActions(payload).filter((action) =>
    ['approve', 'request_changes', 'cancel'].includes(action.id));
  const submitReview = async (action) => {
    if (!onResponse || submitting || submitted) return;
    setSubmitting(true);
    setReviewError('');
    try {
      await onResponse({
        action: action.id,
        approved: action.approved,
        review_id: payload.review_id,
        rationale: feedback.trim(),
      });
      setSubmitted(true);
    } catch {
      setReviewError('The review could not be submitted.');
    } finally {
      setSubmitting(false);
    }
  };
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
      <div className="px-5 py-4 border-b border-border bg-muted/40">
        <div className="flex flex-col items-start justify-between gap-4 sm:flex-row">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-3">
              <div className="shrink-0 rounded-lg bg-primary/20 p-2.5 ring-2 ring-primary/50">
                <Sparkles className="w-5 h-5 text-[var(--color-primary-light)]" />
              </div>
              <div className="min-w-0">
                <h2 className="text-xl font-heading font-bold text-foreground break-words">{title}</h2>
                {appName && (
                  <div className="text-sm text-muted-foreground truncate">{appName}</div>
                )}
              </div>
            </div>
            <div className="mt-2 text-[11px] text-muted-foreground break-words">
              {generatedWorkflowName || sourceWorkflowName || 'ValueEngine'} • event {toolCallId || 'n/a'}
              {appId ? ` • app_id ${appId}` : null}
            </div>
          </div>
          {valueProp && (
            <div className="inline-flex min-w-0 items-start gap-2 text-primary text-sm max-w-full sm:max-w-[280px]">
              <Target className="w-4 h-4 shrink-0 mt-0.5" />
              <span className="break-words">{valueProp}</span>
            </div>
          )}
        </div>
      </div>

      <div className="p-5 space-y-5">
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
      {onResponse && payload.review_id && (
        <section aria-label="Concept review" className="border-t border-border px-5 py-4 space-y-3 text-foreground">
          {submitted ? <p role="status" className="text-sm text-muted-foreground">Review submitted</p> : (
            <>
              <label className="block space-y-2">
                <span className="text-sm font-medium">Requested changes</span>
                <textarea value={feedback} onChange={(event) => setFeedback(event.target.value)}
                  maxLength={4000} rows={3} disabled={submitting}
                  className="block w-full rounded-md border border-border bg-background p-3 text-sm text-foreground" />
              </label>
              {reviewError && <p role="alert" className="text-sm text-destructive">{reviewError}</p>}
              <div className="flex flex-wrap gap-2">
                {reviewActions.map((action) => {
                  const Icon = { approve: Check, request_changes: PencilLine, cancel: X }[action.id];
                  return (
                    <button key={action.id} type="button" disabled={submitting} onClick={() => submitReview(action)}
                      className={`inline-flex items-center justify-center gap-2 rounded-md border px-4 py-2 text-sm font-medium disabled:opacity-50 ${action.id === 'approve' ? 'border-primary bg-primary text-primary-foreground' : 'border-border bg-background text-foreground'}`}>
                      <Icon className="h-4 w-4 shrink-0" aria-hidden="true" />{action.label}
                    </button>
                  );
                })}
              </div>
            </>
          )}
        </section>
      )}
    </div>
  );
};

const ConceptBlueprint = (props) => <ConceptBlueprintContent key={props.payload?.review_id} {...props} />;

export default ConceptBlueprint;

