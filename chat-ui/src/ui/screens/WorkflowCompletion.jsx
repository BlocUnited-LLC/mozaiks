/**
 * WorkflowCompletion — workflow-agnostic completion screen.
 *
 * Registered as a transition component (transition_type: workflow_complete).
 * Receives { transition, onResolve } from TransitionScreen; context fields
 * (workflowName, summary) are read from transition.context, which TransitionScreen
 * merges from the shell's pendingTransitionContext before passing to this component.
 *
 * Direct-use props (workflowName, completionMessage, summary, onContinue,
 * continueCta) are still accepted so callers that bypass the transition system
 * continue to work.
 */

import { TransitionActionPanel, TransitionActionButton, useTransitionMotion } from '@mozaiks/chat-ui/platform';

const SUMMARY_FIELDS = [
  ['filesGenerated', 'Files Generated', true],
  ['duration', 'Duration', false],
  ['tokensUsed', 'Tokens Used', false],
];

// A summary object whose fields are all empty is still truthy, so gating the
// panel on the object alone renders a "Run Summary" heading with nothing under
// it. Gate on having something to show instead.
const summaryRowsOf = (summary) => {
  if (!summary || typeof summary === 'string') return [];
  return SUMMARY_FIELDS.map(([key, label, emphasis]) => [key, label, summary[key], emphasis]).filter(
    ([, , value]) => value !== undefined && value !== null && value !== '',
  );
};

const WorkflowCompletion = ({
  // Transition component contract (preferred path)
  transition = null,
  onResolve = null,
  // Direct-use fallbacks (non-transition callers)
  workflowName: workflowNameProp = 'Workflow',
  completionMessage: completionMessageProp = null,
  summary: summaryProp = null,
  onContinue = null,
  continueCta = 'Continue',
}) => {
  const ctx = transition?.context || {};
  const workflowName = ctx.workflowName || workflowNameProp;
  const completionMessage =
    ctx.completionMessage ||
    completionMessageProp ||
    `Your ${workflowName} workflow has completed successfully!`;
  const summary = ctx.summary || summaryProp;

  const summaryText = typeof summary === 'string' ? summary.trim() : '';
  const summaryRows = summaryRowsOf(summary);
  const hasSummary = Boolean(summaryText) || summaryRows.length > 0;

  const { entered, prefersReducedMotion } = useTransitionMotion();

  const handleContinue = () => {
    if (onResolve) {
      onResolve(null);
      return;
    }
    if (onContinue && typeof onContinue === 'function') {
      onContinue();
    }
  };

  return (
    <TransitionActionPanel
      eyebrow="Workflow Complete"
      title={workflowName}
      body={completionMessage}
      icon="success"
      entered={entered}
      prefersReducedMotion={prefersReducedMotion}
    >
      {hasSummary && (
        <div className="mx-auto mb-6 max-w-sm rounded-xl border border-border bg-muted/60 p-4 text-left">
          <p className="mb-3 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            Run Summary
          </p>
          {summaryText ? (
            <p className="text-sm text-muted-foreground">{summaryText}</p>
          ) : (
            <div className="space-y-2">
              {summaryRows.map(([key, label, value, emphasis]) => (
                <div key={key} className="flex justify-between text-sm">
                  <span className="text-muted-foreground">{label}</span>
                  <span className={emphasis ? 'font-medium text-primary' : 'text-foreground'}>
                    {value}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
      <TransitionActionButton
        label={continueCta}
        variant="primary"
        onClick={handleContinue}
      />
    </TransitionActionPanel>
  );
};

export default WorkflowCompletion;
