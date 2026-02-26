import React from 'react';

/**
 * FluidChatLayout - Adaptive persistent chat interface
 *
 * Manages 3 fluid states with smooth transitions:
 * 1. Full Chat (100% width, no artifact)
 * 2. Split View (chat 50% + artifact 50%)
 * 3. Minimized Chat (chat 60px sidebar + artifact 100%)
 *
 * The chat never disappears - it just transforms based on context.
 */
const FluidChatLayout = ({
  // Layout state
  layoutMode = 'full', // 'full' | 'split' | 'minimized' | 'view'
  onLayoutChange = () => {},

  // Content components
  chatContent = null,
  artifactContent = null,

  // Control handlers
  onToggleArtifact = () => {},
  onToggleChat = () => {},

  // Visual state
  isArtifactAvailable = false,
  hasActiveChat = true,
}) => {
  // Calculate widths based on layout mode
  const getLayoutStyles = () => {
    switch (layoutMode) {
      case 'full':
        return {
          chatWidth: '100%',
          artifactWidth: '0%',
          chatVisible: true,
          artifactVisible: false,
        };
      case 'split':
        return {
          chatWidth: '50%',
          artifactWidth: '50%',
          chatVisible: true,
          artifactVisible: true,
        };
      case 'minimized':
        return {
          chatWidth: '10%',
          artifactWidth: '90%',
          chatVisible: true,
          artifactVisible: true,
        };
      case 'view':
        return {
          chatWidth: '0%',
          artifactWidth: '100%',
          chatVisible: false,
          artifactVisible: true,
        };
      default:
        return {
          chatWidth: '100%',
          artifactWidth: '0%',
          chatVisible: true,
          artifactVisible: false,
        };
    }
  };

  const layout = getLayoutStyles();
  const panelContainer =
    'relative flex flex-col min-h-0 h-full self-stretch transition-all duration-500 ease-in-out pt-0';

  return (
    <div className={`flex h-full min-h-0 relative overflow-hidden ${layoutMode === 'view' ? 'gap-0 p-0' : 'gap-2 p-2'} items-stretch`}>
      {/* Chat Panel - hidden in view mode */}
      {layout.chatVisible && (
        <div
          className={`${panelContainer} chat-pane-transition`}
          style={{ width: layout.chatWidth }}
        >
          {/* Chat Content - ChatInterface owns its neon frame */}
          {layoutMode !== 'minimized' && (
            <div className="flex-1 min-h-0 overflow-visible h-full pt-0 flex flex-col">
              {chatContent}
            </div>
          )}

          {/* Minimized Chat - Show vertical text */}
          {layoutMode === 'minimized' && (
            <div className="flex-1 flex flex-col items-center justify-center p-2">
              <div className="flex flex-col items-center gap-3">
                <div className="w-10 h-10 rounded-full bg-gradient-to-br from-[var(--color-primary)]/20 to-[var(--color-secondary)]/20 flex items-center justify-center">
                  <span className="text-lg font-semibold text-white">M</span>
                </div>
                <div className="flex flex-col items-center gap-1">
                  <span
                    className="text-sm font-semibold text-white"
                    style={{ writingMode: 'vertical-rl', textOrientation: 'mixed' }}
                  >
                    MozaiksAI
                  </span>
                  <span
                    className="text-xs text-gray-500"
                    style={{ writingMode: 'vertical-rl', textOrientation: 'mixed' }}
                  >
                    Click to expand
                  </span>
                </div>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Artifact Panel - relies on ArtifactPanel component for styling */}
      {layout.artifactVisible && (
        <div
          className={`${panelContainer} artifact-panel h-full`}
          style={{ width: layout.artifactWidth }}
        >
          <div className="flex-1 min-h-0 overflow-visible h-full pt-0 flex flex-col">
            {artifactContent}
          </div>
        </div>
      )}
    </div>
  );
};

export default FluidChatLayout;
