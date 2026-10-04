// ValueEngine concept artifact and structured review.
import { useState } from 'react';
import { Check, ChevronDown, PencilLine, X } from 'lucide-react';
import { workflowSurfaceStyles } from '@mozaiks/chat-ui/platform/workflowSurfaceStyles.js';
import { normalizePrimitiveActions } from '@mozaiks/chat-ui/core/ui/workflowPrimitiveUtils.js';

const asText = (value) => (typeof value === 'string' ? value.trim() : '');
const asArray = (value) => (Array.isArray(value) ? value : []);
const textList = (value) => asArray(value).map(asText).filter(Boolean);
const firstText = (...values) => values.map(asText).find(Boolean) || '';

const renderList = (items) => {
  const list = textList(items);
  return list.length ? (
    <ul className="list-disc space-y-1.5 pl-5 text-sm leading-relaxed text-muted-foreground">
      {list.map((item, index) => <li key={`${item}-${index}`} className="break-words">{item}</li>)}
    </ul>
  ) : <p className="text-sm text-muted-foreground">None specified.</p>;
};

const DetailField = ({ title, children }) => (
  <div className="space-y-1.5">
    <h4 className="font-sans text-sm font-medium text-foreground">{title}</h4>
    {children}
  </div>
);

const ConceptDetails = ({ title, children }) => (
  <details className="group border-t border-border">
    <summary className="flex cursor-pointer list-none items-center justify-between gap-3 px-5 py-4 text-sm font-medium text-foreground hover:bg-muted/40 focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary">
      {title}<ChevronDown aria-hidden="true" className="h-4 w-4 shrink-0 text-muted-foreground group-open:rotate-180" />
    </summary>
    <div className="space-y-5 px-5 pb-5 text-sm leading-relaxed text-muted-foreground">{children}</div>
  </details>
);

const ConceptBlueprintContent = ({ payload = {}, onResponse, toolCallId, sourceWorkflowName, generatedWorkflowName }) => {
  const [feedback, setFeedback] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [reviewError, setReviewError] = useState('');
  const reviewActions = normalizePrimitiveActions(payload).filter((action) =>
    ['approve', 'request_changes', 'cancel'].includes(action.id));
  const changeActions = reviewActions.filter((action) => action.id === 'request_changes');
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
    } catch (error) {
      const message = error?.message;
      // The response adapter supplies these user-facing messages without server/provider details.
      const safeMessage = typeof message === 'string'
        && /^(This review is no longer active\.|The server did not confirm your decision\.|Your decision was not accepted\.)/.test(message);
      setReviewError(safeMessage ? message : 'The review could not be submitted.');
    } finally {
      setSubmitting(false);
    }
  };
  const reviewButton = (action) => {
    const Icon = { approve: Check, request_changes: PencilLine, cancel: X }[action.id];
    return (
      <button key={action.id} type="button" disabled={submitting} onClick={() => submitReview(action)}
        className={`inline-flex items-center justify-center gap-2 rounded-lg border px-4 py-2.5 font-sans text-sm font-medium focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary disabled:opacity-50 ${action.id === 'approve' ? 'border-primary bg-primary text-primary-foreground' : 'border-border bg-background text-foreground'}`}>
        <Icon className="h-4 w-4 shrink-0" aria-hidden="true" />{action.label}
      </button>
    );
  };
  const blueprint = payload && typeof payload.blueprint === 'object' ? payload.blueprint : null;
  const endpoints = asArray(payload.api_endpoints).filter((item) => item && typeof item === 'object');
  const conceptOverview = asText(payload.concept_overview);
  const appName = firstText(blueprint?.app_name, blueprint?.appName, blueprint?.name, blueprint?.app, payload?.workflow?.name);
  const valueProp = firstText(blueprint?.value_proposition, blueprint?.valueProposition, blueprint?.tagline);
  const targetUser = firstText(blueprint?.target_user, blueprint?.targetUser, blueprint?.target_users?.[0]?.persona);
  const brandIntent = blueprint?.brand_intent || blueprint?.brandIntent;
  const coreFeatures = textList(blueprint?.mvp_scope?.core_features || blueprint?.mvp_scope?.coreFeatures);
  const deferredFeatures = blueprint?.mvp_scope?.deferred_features || blueprint?.mvp_scope?.deferredFeatures;
  const differentiators = blueprint?.unique_differentiators || blueprint?.uniqueDifferentiators;
  const benefit = valueProp || conceptOverview.split(/(?<=[.!?])\s+/)[0];

  return (
    <article className={`${workflowSurfaceStyles.primaryPanel} min-w-0 font-sans text-foreground`}>
      <header className="space-y-2 px-5 pt-5">
        <p className="text-xs font-medium text-muted-foreground">App concept</p>
        <h2 className="break-words font-sans text-xl font-semibold leading-snug">{appName || asText(payload.title) || 'Your app concept'}</h2>
        {benefit && <p className="line-clamp-2 text-sm leading-relaxed text-muted-foreground">{benefit}</p>}
      </header>

      {onResponse && payload.review_id && reviewActions.length > 0 && (
        <section aria-label="Concept review" className="space-y-3 px-5 pt-4">
          {submitted ? <p role="status" className="text-sm text-muted-foreground">Review submitted</p> : (
            <>
              <div className="flex flex-wrap gap-2">{reviewActions.filter((action) => action.id !== 'request_changes').map(reviewButton)}</div>
              {reviewError && <p role="alert" className="text-sm text-destructive">{reviewError}</p>}
              <details className="group">
                <summary className="inline-flex cursor-pointer list-none items-center gap-1.5 py-1 text-sm text-muted-foreground hover:text-foreground focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary">
                  {changeActions.length ? 'Request changes' : 'Add a note'}
                  <ChevronDown aria-hidden="true" className="h-4 w-4 group-open:rotate-180" />
                </summary>
                <div className="space-y-3 pt-3">
                  <label className="block space-y-2">
                    <span className="text-sm font-medium">Requested changes</span>
                    <textarea value={feedback} onChange={(event) => setFeedback(event.target.value)}
                      maxLength={4000} rows={3} disabled={submitting}
                      placeholder="Tell us what you would like to change."
                      className="block w-full rounded-lg border border-border bg-background p-3 font-sans text-sm text-foreground" />
                  </label>
                  <div className="flex flex-wrap gap-2">{changeActions.map(reviewButton)}</div>
                </div>
              </details>
            </>
          )}
        </section>
      )}

      <section aria-label="Core features" className="space-y-2 px-5 py-5">
        <h3 className="font-sans text-sm font-medium">Core features</h3>
        {coreFeatures.length ? (
          <ul className="space-y-2 text-sm leading-relaxed text-muted-foreground">
            {coreFeatures.slice(0, 3).map((feature, index) => (
              <li key={`${feature}-${index}`} className="flex items-start gap-2">
                <Check aria-hidden="true" className="mt-1 h-4 w-4 shrink-0 text-primary" />
                <span className="line-clamp-2 min-w-0 break-words">{feature}</span>
              </li>
            ))}
          </ul>
        ) : <p className="text-sm text-muted-foreground">Core features have not been specified yet.</p>}
        {coreFeatures.length > 3 && <p className="text-xs text-muted-foreground">{coreFeatures.length - 3} more in Product details below.</p>}
      </section>

      <ConceptDetails title="Product details">
        {valueProp && <DetailField title="Benefit"><p className="break-words">{valueProp}</p></DetailField>}
        <DetailField title="Overview"><p className="whitespace-pre-wrap break-words">{conceptOverview || 'Not specified.'}</p></DetailField>
        <DetailField title="Who it is for"><p>{targetUser || 'Not specified.'}</p></DetailField>
        <DetailField title="All core features">{renderList(coreFeatures)}</DetailField>
        <DetailField title="For later">{renderList(deferredFeatures)}</DetailField>
        <DetailField title="What makes it different">{renderList(differentiators)}</DetailField>
      </ConceptDetails>

      <ConceptDetails title="Design details">
        <DetailField title="Style"><p>{firstText(brandIntent?.style_summary, brandIntent?.styleSummary) || 'Not specified.'}</p></DetailField>
        <DetailField title="Appearance"><p>{firstText(brandIntent?.appearance_hint, brandIntent?.appearanceHint) || 'Not specified.'}</p></DetailField>
        <DetailField title="Brand keywords">{renderList(brandIntent?.brand_keywords || brandIntent?.brandKeywords)}</DetailField>
        <DetailField title="Experience goals">{renderList(brandIntent?.experience_goals || brandIntent?.experienceGoals)}</DetailField>
        <DetailField title="Screens and interactions">{renderList(blueprint?.app_ui_requirements || blueprint?.appUiRequirements)}</DetailField>
      </ConceptDetails>

      <ConceptDetails title="Technical details">
        <DetailField title="Capability packs">{renderList(blueprint?.capability_pack_hints || blueprint?.capabilityPackHints)}</DetailField>
        <DetailField title="AI capabilities">{renderList(blueprint?.agentic_capabilities || blueprint?.agenticCapabilities)}</DetailField>
        <DetailField title="API endpoints">
          {endpoints.length ? (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead><tr>
                  <th scope="col" className="py-2 pr-3 font-medium">Method</th>
                  <th scope="col" className="py-2 pr-3 font-medium">Path</th>
                  <th scope="col" className="py-2 font-medium">Description</th>
                </tr></thead>
                <tbody className="align-top">
                  {endpoints.map((endpoint, index) => (
                    <tr key={`endpoint-${index}`} className="border-t border-border">
                      <td className="py-2 pr-3 font-mono">{asText(endpoint.method) || '—'}</td>
                      <td className="break-all py-2 pr-3 font-mono">{asText(endpoint.path) || '—'}</td>
                      <td className="py-2">{asText(endpoint.description) || asText(endpoint.name)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : <p>No endpoints specified.</p>}
        </DetailField>
        <DetailField title="Build references">
          <dl className="space-y-2 break-all text-xs">
            <div><dt className="font-medium">Workflow</dt><dd>{generatedWorkflowName || sourceWorkflowName || 'ValueEngine'}</dd></div>
            {toolCallId && <div><dt className="font-medium">Event</dt><dd>{toolCallId}</dd></div>}
            {asText(payload.app_id) && <div><dt className="font-medium">App ID</dt><dd>{payload.app_id}</dd></div>}
          </dl>
        </DetailField>
      </ConceptDetails>
    </article>
  );
};

const ConceptBlueprint = (props) => <ConceptBlueprintContent key={props.payload?.review_id} {...props} />;
export default ConceptBlueprint;
