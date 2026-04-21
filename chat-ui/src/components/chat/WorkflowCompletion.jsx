/**
 * WorkflowCompletion - Workflow-agnostic completion screen.
 *
 * Props:
 *   workflowName         — display name of the completed workflow
 *   completionMessage    — primary message shown under the title
 *   summary              — optional: string OR { filesGenerated, duration, tokensUsed }
 *   onContinue           — called when the user clicks the CTA; if null a generic CTA is shown
 *   continueCta          — CTA button label (default: "Continue")
 */

const WorkflowCompletion = ({
  workflowName = 'Workflow',
  completionMessage = 'Your workflow has completed successfully!',
  summary = null,
  onContinue = null,
  continueCta = 'Continue',
}) => {
  const handleContinue = () => {
    if (onContinue && typeof onContinue === 'function') {
      onContinue();
    }
  };

  return (
    <div className="flex items-center justify-center min-h-[400px] p-6">
      <div className="bg-card border border-border rounded-xl p-8 max-w-2xl w-full text-center shadow-lg">
        {/* Success icon */}
        <div className="mb-6">
          <div className="inline-flex items-center justify-center w-24 h-24 rounded-full bg-success/10 border-2 border-success/40">
            <svg
              className="w-12 h-12 text-success"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M5 13l4 4L19 7"
              />
            </svg>
          </div>
        </div>

        {/* Title */}
        <h2 className="text-2xl font-bold text-primary mb-4">Congratulations!</h2>

        {/* Message */}
        <p className="text-base text-muted-foreground mb-6 leading-relaxed">{completionMessage}</p>

        {/* Workflow badge */}
        <div className="mb-8">
          <div className="inline-flex items-center px-4 py-2 rounded-full bg-muted border border-border">
            <span className="text-xs text-muted-foreground mr-2">Workflow:</span>
            <span className="text-sm font-semibold text-primary">{workflowName}</span>
          </div>
        </div>

        {/* Optional summary */}
        {summary && (
          <div className="mb-8 p-4 bg-muted rounded-lg border border-border text-left space-y-2">
            <h3 className="text-sm font-semibold text-foreground mb-3">Summary</h3>
            {typeof summary === 'string' ? (
              <p className="text-sm text-muted-foreground">{summary}</p>
            ) : (
              <>
                {summary.filesGenerated !== undefined && (
                  <div className="flex justify-between text-sm">
                    <span className="text-muted-foreground">Files Generated:</span>
                    <span className="text-primary font-medium">{summary.filesGenerated}</span>
                  </div>
                )}
                {summary.duration !== undefined && (
                  <div className="flex justify-between text-sm">
                    <span className="text-muted-foreground">Duration:</span>
                    <span className="text-foreground">{summary.duration}</span>
                  </div>
                )}
                {summary.tokensUsed !== undefined && (
                  <div className="flex justify-between text-sm">
                    <span className="text-muted-foreground">Tokens Used:</span>
                    <span className="text-foreground">{summary.tokensUsed}</span>
                  </div>
                )}
              </>
            )}
          </div>
        )}

        {/* CTA */}
        {onContinue && (
          <button
            onClick={handleContinue}
            className="bg-primary text-primary-foreground px-8 py-3 rounded-lg text-base font-semibold min-w-[200px] group transition-all duration-200 hover:bg-primary/90 hover:scale-105 hover:shadow-lg hover:shadow-primary/20"
          >
            <span className="flex items-center justify-center gap-2">
              {continueCta}
              <svg
                className="w-5 h-5 transition-transform duration-200 group-hover:translate-x-1"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M13 7l5 5m0 0l-5 5m5-5H6"
                />
              </svg>
            </span>
          </button>
        )}
      </div>
    </div>
  );
};

export default WorkflowCompletion;
