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
      eyebrow="Build Complete"
      title="Congratulations!"
      body={completionMessage}
      icon="success"
      entered={entered}
      prefersReducedMotion={prefersReducedMotion}
    >
      {summary && (
        <div className="mx-auto mb-6 max-w-sm rounded-xl border border-border bg-muted/60 p-4 text-left">
          <p className="mb-3 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            Run Summary
          </p>
          {typeof summary === 'string' ? (
            <p className="text-sm text-muted-foreground">{summary}</p>
          ) : (
            <div className="space-y-2">
              {summary.filesGenerated !== undefined && (
                <div className="flex justify-between text-sm">
                  <span className="text-muted-foreground">Files Generated</span>
                  <span className="font-medium text-primary">{summary.filesGenerated}</span>
                </div>
              )}
              {summary.duration && (
                <div className="flex justify-between text-sm">
                  <span className="text-muted-foreground">Duration</span>
                  <span className="text-foreground">{summary.duration}</span>
                </div>
              )}
              {summary.tokensUsed && (
                <div className="flex justify-between text-sm">
                  <span className="text-muted-foreground">Tokens Used</span>
                  <span className="text-foreground">{summary.tokensUsed}</span>
                </div>
              )}
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
