import React from 'react';
import UIToolRenderer from '../../core/ui/UIToolRenderer';
import ArtifactActionsBar from '../actions/ArtifactActionsBar';
import ArtifactLoadingState from '../../ui/primitives/ArtifactLoadingState';
import {
  applyBrandImageFallback,
  getBrandLogoSrc,
} from '../../styles/brandAssets';

const ArtifactPanel = ({
  onClose,
  isMobile = false,
  isEmbedded = false,
  messages = [],
  workflowName = null,
  viewMode = false,
  onExitView = null,
  onArtifactAction = null,
  actionStatusMap = null,
  chatTheme = null,
  floatingWidget = null,
  loading = false,
}) => {
  const isMobileEmbedded = Boolean(isMobile && isEmbedded);
  const isViewSurface = Boolean(viewMode);
  const brandLogoSrc = getBrandLogoSrc(chatTheme);
  const hasMessages = Array.isArray(messages) && messages.length > 0;
  const showLoadingState = Boolean(loading);

  const containerClasses = isMobileEmbedded
    ? 'flex flex-col w-full h-full min-h-0'
    : (isMobile
        ? 'fixed inset-0 z-50 min-h-0'
        : 'flex flex-col w-full h-full min-h-0 self-stretch transition-all duration-500 ease-in-out');

  // Match ChatInterface styling exactly for consistent neon border effect
  const contentClasses = isMobileEmbedded
    ? 'relative flex flex-col flex-1 min-h-0 h-full rounded-[26px] border border-[rgba(var(--color-primary-light-rgb),0.35)] shadow-[0_-18px_45px_rgba(0,0,0,0.75)] bg-[rgba(4,8,18,0.95)] backdrop-blur-lg artifact-panel artifact-panel-mobile'
    : isMobile
      ? 'relative w-full h-full flex flex-col artifact-panel'
      : 'relative flex flex-col flex-1 min-h-0 h-full rounded-2xl border border-[rgba(var(--color-primary-light-rgb),0.3)] md:overflow-hidden overflow-visible shadow-2xl bg-gradient-to-br from-white/5 to-[rgba(var(--color-primary-rgb),0.05)] backdrop-blur-sm cosmic-ui-module artifact-panel p-0';

  const scrollPaddingClass = isMobile
    ? 'px-2 py-2 sm:px-3 sm:py-3'
    : (isViewSurface ? 'p-0' : 'px-2 py-2 sm:px-3 sm:py-3 md:p-6');
  const contentStackClass = isViewSurface
    ? 'flex-1 min-h-0 flex flex-col'
    : 'space-y-3 sm:space-y-4 md:space-y-6 flex-1';

  const isCoreArtifactPayload = (payload, uiToolId = null) => {
    const type = payload?.artifact_type || payload?.data?.artifact_type || uiToolId;
    return typeof type === 'string' && type.startsWith('core.');
  };

  const emptyStateTitle = workflowName ? `${workflowName} artifacts` : 'Artifact canvas';
  const emptyStateMessage = isViewSurface
    ? 'This surface is ready for generated artifacts, but nothing has been published into it yet.'
    : 'Artifacts from workflow tools will appear here once the session publishes a UI payload.';

  const loadingSurface = (
    <ArtifactLoadingState
      chatTheme={chatTheme}
      title="Rendering artifact"
      message="Preparing the generated surface for display."
      className="min-h-full md:min-h-[500px]"
    />
  );

  return (
    <div className={containerClasses}>
      {/* Mobile backdrop */}
      {isMobile && !isEmbedded && (
        <div className="absolute inset-0 bg-black/70 backdrop-blur-sm" onClick={onClose} />
      )}

      {/* Panel Content */}
      <div className={contentClasses} style={{ overflow: 'clip' }}>
        {/* Artifact Content Area - match chat scroll treatment */}
        <div className="flex-1 min-h-0 relative overflow-hidden" role="region" aria-label="Artifact output stream">
          <div className="absolute inset-0 pointer-events-none bg-[rgba(0,0,0,0.45)] z-0" />

          <div className={`absolute inset-0 overflow-y-auto ${scrollPaddingClass} my-scroll1 z-10 h-full flex flex-col`}>
            {/* If no content, show just the Mozaiks logo */}
            {showLoadingState && !hasMessages ? (
              loadingSurface
            ) : (!messages || messages.length === 0) ? (
              <div className="flex flex-1 items-center justify-center min-h-full md:min-h-[500px]">
                <div className="max-w-md w-full rounded-3xl border border-[rgba(var(--color-primary-light-rgb),0.28)] bg-[rgba(6,11,25,0.72)] shadow-[0_24px_60px_rgba(2,6,23,0.55)] backdrop-blur-xl px-6 py-8 sm:px-8 sm:py-10 text-center">
                  <div className="mx-auto mb-5 w-24 h-24 sm:w-28 sm:h-28 bg-gradient-to-br from-[var(--color-primary)]/20 to-[var(--color-secondary)]/20 rounded-2xl sm:rounded-3xl border-2 border-[var(--color-primary-light)]/45 flex items-center justify-center backdrop-blur-sm shadow-2xl">
                    <img
                      src={brandLogoSrc}
                      alt="Mozaiks Logo"
                      className="w-16 h-16 sm:w-20 sm:h-20"
                      onError={applyBrandImageFallback}
                    />
                  </div>
                  <div className="text-[11px] uppercase tracking-[0.28em] text-[rgba(var(--color-primary-light-rgb),0.72)] mb-3 heading-font">
                    {emptyStateTitle}
                  </div>
                  <div className="text-lg sm:text-xl font-semibold text-white mb-2 heading-font">
                    No artifacts yet
                  </div>
                  <div className="text-sm sm:text-base text-[rgba(226,232,240,0.78)] leading-6">
                    {emptyStateMessage}
                  </div>
                </div>
              </div>
            ) : (
              <div className={contentStackClass}>
                {showLoadingState && hasMessages && (
                  <div className="pointer-events-none absolute inset-0 z-20 flex items-center justify-center bg-[rgba(4,8,18,0.38)] backdrop-blur-[1px]">
                    <div className="w-full max-w-3xl px-2 sm:px-0">
                      <ArtifactLoadingState
                        chatTheme={chatTheme}
                        title="Rendering artifact"
                        message="Preparing the generated surface for display."
                      />
                    </div>
                  </div>
                )}
                {messages.map((m, idx) => {
                  // If message has a workflow tool_call, render the actual UI component
                  if (m.toolCall && m.toolCall.tool_name) {
                    const payload = m.toolCall.payload || {};
                    const actions = Array.isArray(payload.actions)
                      ? payload.actions.filter(action => (action?.scope || 'artifact') !== 'row')
                      : [];
                    const isCoreArtifact = isCoreArtifactPayload(payload, m.toolCall.tool_name);
                    const wrapperClass = isViewSurface
                      ? 'app-component-wrapper flex-1 min-h-0 flex flex-col'
                      : 'app-component-wrapper';
                    const rendererClass = isViewSurface
                      ? 'app-ui-component flex-1 min-h-0'
                      : 'app-ui-component';
                    return (
                      <div key={m.id || idx} className={wrapperClass}>
                        <UIToolRenderer
                          event={m.toolCall}
                          onResponse={m.toolCall.onResponse}
                          onArtifactAction={onArtifactAction}
                          actionStatusMap={actionStatusMap}
                          className={rendererClass}
                        />
                        {!isCoreArtifact && (
                          <ArtifactActionsBar
                            actions={actions}
                            artifactPayload={payload}
                            onAction={onArtifactAction}
                            actionStatusMap={actionStatusMap}
                          />
                        )}
                      </div>
                    );
                  }

                  // Fallback: render as development view
                  let parsed = null;
                  try {
                    const jsonMatch = (typeof m.content === 'string') ? m.content.match(/\{[\s\S]*\}/) : null;
                    if (jsonMatch) parsed = JSON.parse(jsonMatch[0]);
                    else if (typeof m.content === 'object') parsed = m.content;
                  } catch (e) { parsed = null; }

                  return (
                    <div key={m.id || idx} className={isViewSurface ? 'flex-1 min-h-0 flex flex-col' : ''}>
                      <div className="bg-gray-800/40 border border-gray-700/60 rounded-lg sm:rounded-xl p-3 sm:p-4 md:p-5 backdrop-blur-sm">
                      <div className="flex items-start justify-between mb-2 sm:mb-3 gap-2">
                        <div className="min-w-0 flex-1">
                          <div className="text-[10px] sm:text-xs font-medium text-[var(--color-primary-light)] mb-1 truncate">{m.agentName || 'System'}</div>
                          <div className="text-xs sm:text-sm text-gray-200 font-semibold">Component Data</div>
                        </div>
                        <button
                          onClick={() => {
                            if (navigator.clipboard && parsed) {
                              navigator.clipboard.writeText(JSON.stringify(parsed, null, 2));
                            }
                          }}
                          className="text-[10px] sm:text-xs px-2 py-1 sm:px-3 sm:py-1.5 bg-gray-700/50 hover:bg-gray-600/50 rounded-md sm:rounded-lg text-gray-200 hover:text-white transition-all border border-gray-600/50 flex-shrink-0"
                        >
                          Copy
                        </button>
                      </div>
                      {parsed ? (
                        <pre className="text-[10px] sm:text-xs text-gray-300 bg-black/40 p-2 sm:p-3 md:p-4 rounded-lg overflow-auto max-h-60 sm:max-h-80 border border-gray-700/30 font-mono">
                          {JSON.stringify(parsed, null, 2)}
                        </pre>
                      ) : (
                        <div className="text-xs sm:text-sm text-gray-400">Component data not available.</div>
                      )}
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </div>

        {floatingWidget && (
          <div
            className="absolute bottom-0 right-0"
            style={{ zIndex: 60, pointerEvents: 'auto' }}
          >
            {floatingWidget}
          </div>
        )}
      </div>
    </div>
  );
};

export default ArtifactPanel;
