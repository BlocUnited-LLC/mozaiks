/**
 * WorkflowCompletion - Workflow-agnostic completion screen
 * 
 * Displays a congratulations message when a workflow completes successfully.
 * Shows workflow summary and provides a "Continue to Mozaiks" button that
 * redirects to https://www.mozaiks.ai/
 */

import React from 'react';
import { colors, typography, components } from '../../styles/artifactDesignSystem';

const WorkflowCompletion = ({
  workflowName = 'Workflow',
  completionMessage = 'Your workflow has completed successfully!',
  summary = null,
  onContinue = null,
}) => {
  const handleContinue = () => {
    console.log('🎉 [COMPLETION] User clicked Continue - redirecting to Mozaiks');
    
    // Call optional callback first (for analytics, cleanup, etc.)
    if (onContinue && typeof onContinue === 'function') {
      try {
        onContinue();
      } catch (err) {
        console.error('❌ [COMPLETION] onContinue callback error:', err);
      }
    }
    
    // Redirect to Mozaiks homepage
    window.location.href = 'https://www.mozaiks.ai/';
  };

  return (
    <div className="flex items-center justify-center min-h-[400px] p-6">
      <div className={`${components.panel.artifact} max-w-2xl w-full text-center`}>
        {/* Success Icon */}
        <div className="mb-6">
          <div className="inline-flex items-center justify-center w-24 h-24 rounded-full bg-gradient-to-br from-emerald-500/20 to-green-500/20 border-2 border-emerald-500/40">
            <svg 
              className="w-12 h-12 text-emerald-400" 
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
        <h2 className={`${typography.heading.h2} ${colors.brand.primaryLight.text} mb-4`}>
          🎉 Congratulations!
        </h2>

        {/* Message */}
        <p className={`${typography.body.lg} ${colors.text.secondary} mb-6 leading-relaxed`}>
          {completionMessage}
        </p>

        {/* Workflow Name Badge */}
        <div className="mb-8">
          <div className="inline-flex items-center px-4 py-2 rounded-full bg-gray-800/60 border border-gray-700">
            <span className={`${typography.label.sm} ${colors.text.muted} mr-2`}>
              Workflow:
            </span>
            <span className={`${typography.label.md} ${colors.brand.primaryLight.text}`}>
              {workflowName}
            </span>
          </div>
        </div>

        {/* Optional Summary */}
        {summary && (
          <div className="mb-8 p-4 bg-gray-800/40 rounded-lg border border-gray-700/50">
            <h3 className={`${typography.label.md} ${colors.text.primary} mb-3`}>
              Summary
            </h3>
            <div className={`${typography.body.sm} ${colors.text.secondary} text-left space-y-2`}>
              {typeof summary === 'string' ? (
                <p>{summary}</p>
              ) : (
                <>
                  {summary.filesGenerated && (
                    <div className="flex justify-between">
                      <span>Files Generated:</span>
                      <span className={colors.brand.primaryLight.text}>{summary.filesGenerated}</span>
                    </div>
                  )}
                  {summary.duration && (
                    <div className="flex justify-between">
                      <span>Duration:</span>
                      <span className={colors.text.primary}>{summary.duration}</span>
                    </div>
                  )}
                  {summary.tokensUsed && (
                    <div className="flex justify-between">
                      <span>Tokens Used:</span>
                      <span className={colors.text.primary}>{summary.tokensUsed}</span>
                    </div>
                  )}
                </>
              )}
            </div>
          </div>
        )}

        {/* Continue Button */}
        <button
          onClick={handleContinue}
          className={`${components.button.primary} text-lg px-8 py-4 min-w-[280px] group transition-all duration-300 hover:scale-105 hover:shadow-lg hover:shadow-emerald-500/20`}
        >
          <span className="flex items-center justify-center gap-2">
            Continue to Mozaiks
            <svg 
              className="w-5 h-5 transition-transform duration-300 group-hover:translate-x-1" 
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

        {/* Footer Note */}
        <p className={`${typography.body.xs} ${colors.text.muted} mt-6`}>
          You'll be redirected to Mozaiks to explore more workflows
        </p>
      </div>
    </div>
  );
};

export default WorkflowCompletion;
