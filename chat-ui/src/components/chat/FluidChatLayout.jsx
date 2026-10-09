import React, { useLayoutEffect, useRef } from 'react';
import MobileArtifactDrawer from './MobileArtifactDrawer';
import '../../styles/mobile.css';

/**
 * FluidChatLayout - Adaptive persistent chat interface
 *
 * Keeps chat and artifact content mounted across four layout presentations:
 * 1. Full Chat (100% width, no artifact)
 * 2. Split View (chat 50% + artifact 50%)
 * 3. Minimized Chat (chat rail 10% + artifact 90%)
 * 4. View (artifact 100%, conversation hidden)
 * Mobile presents the same artifact subtree in a drawer over the conversation.
 */
const FluidChatLayout = ({
  // Layout state
  layoutMode = 'full', // 'full' | 'split' | 'minimized' | 'view'
  onLayoutChange = () => {},

  // Content components
  chatContent = null,
  artifactContent = null,
  isMobile = false,
  mobileDrawerState = 'peek',
  onMobileDrawerStateChange = () => {},
  onArtifactClose = () => {},
  onExitView = null,

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
  const drawerVisible = mobileDrawerState !== 'hidden'
    && (mobileDrawerState === 'expanded' || layoutMode === 'view');
  const mobileDrawerOpen = isMobile && drawerVisible;
  const wasMobileDrawerOpen = useRef(false);
  const returnFocus = useRef(null);
  const chatPaneRef = useRef(null);
  const collapseButtonRef = useRef(null);

  useLayoutEffect(() => {
    if (mobileDrawerOpen && !wasMobileDrawerOpen.current) {
      returnFocus.current = document.activeElement;
      collapseButtonRef.current?.focus();
    } else if (!mobileDrawerOpen && wasMobileDrawerOpen.current) {
      const previous = returnFocus.current;
      const usable = previous?.isConnected
        && previous !== document.body
        && !previous.closest('[inert], [hidden]');
      const conversation = chatPaneRef.current;
      const fallback = conversation?.querySelector('textarea, [role="textbox"]') || conversation;
      (usable ? previous : fallback)?.focus();
      returnFocus.current = null;
    }
    wasMobileDrawerOpen.current = mobileDrawerOpen;
  }, [mobileDrawerOpen]);

  const chatVisible = isMobile ? !drawerVisible : layout.chatVisible;
  const composerVisible = chatVisible && (isMobile || layoutMode !== 'minimized');
  const panelContainer =
    'relative flex flex-col min-w-0 min-h-0 h-full self-stretch transition-all duration-500 ease-in-out pt-0';

  return (
    <div className={`flex h-full min-h-0 relative overflow-hidden ${isMobile || layoutMode === 'view' || layoutMode === 'full' ? 'gap-0 p-0' : 'gap-2 p-2'} items-stretch`}>
      {/* Retain the conversation while its presentation is hidden or minimized. */}
      <div
        ref={chatPaneRef}
        tabIndex={-1}
        className={`${panelContainer} chat-pane-transition`}
        hidden={!isMobile && !layout.chatVisible}
        inert={!chatVisible}
        aria-hidden={!chatVisible}
        style={{ width: isMobile ? '100%' : layout.chatWidth, display: !isMobile && !layout.chatVisible ? 'none' : undefined }}
      >
        {/* Chat Content - ChatInterface owns its neon frame */}
        <div
          className="flex-1 min-h-0 overflow-visible h-full pt-0 flex flex-col"
          inert={!composerVisible}
          aria-hidden={!composerVisible}
          hidden={!isMobile && layoutMode === 'minimized'}
          style={{ display: !isMobile && layoutMode === 'minimized' ? 'none' : undefined }}
        >
          {chatContent}
        </div>

        {/* Minimized Chat - Show vertical text */}
        {!isMobile && layoutMode === 'minimized' && (
          <button
            type="button"
            aria-label="Expand conversation"
            onClick={() => onLayoutChange('split')}
            className="flex-1 flex flex-col items-center justify-center p-2"
          >
            <div className="flex flex-col items-center gap-3">
              <svg aria-hidden="true" className="w-6 h-6" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5">
                <path strokeLinecap="round" strokeLinejoin="round" d="M7 18l-4 3V5a2 2 0 012-2h14a2 2 0 012 2v11a2 2 0 01-2 2H7z" />
              </svg>
              <div className="flex flex-col items-center gap-1">
                <span
                  className="text-sm font-semibold text-white"
                  style={{ writingMode: 'vertical-rl', textOrientation: 'mixed' }}
                >
                  Conversation
                </span>
                <span
                  className="text-xs text-gray-500"
                  style={{ writingMode: 'vertical-rl', textOrientation: 'mixed' }}
                >
                  Expand
                </span>
              </div>
            </div>
          </button>
        )}
      </div>

      {/* Artifact Panel - relies on ArtifactPanel component for styling */}
      <MobileArtifactDrawer
        isMobile={isMobile}
        collapseButtonRef={collapseButtonRef}
        state={isMobile ? mobileDrawerState : (layout.artifactVisible ? 'expanded' : 'peek')}
        desktopWidth={layout.artifactWidth}
        onStateChange={onMobileDrawerStateChange}
        onClose={onArtifactClose}
        onExitView={onExitView}
        viewMode={layoutMode === 'view'}
        artifactContent={artifactContent}
      />
    </div>
  );
};

export default FluidChatLayout;
