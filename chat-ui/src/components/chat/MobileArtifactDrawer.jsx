import React, { useEffect, useState } from 'react';

const MobileArtifactDrawer = ({
  state = 'hidden',
  onStateChange = () => {},
  onClose = () => {},
  artifactContent = null,
  isMobile = true,
  collapseButtonRef = null,
  desktopWidth = '100%',
  hasUnseenChat = false,
  hasUnseenArtifact = false,
  viewMode = false,
  chatTheme = null,
  onExitView = null
}) => {
  const isExpanded = viewMode || state === 'expanded';
  const isHidden = state === 'hidden';
  const isVisible = !isHidden && isExpanded;
  const [artifactVisited, setArtifactVisited] = useState(false);
  useEffect(() => {
    if (isVisible) setArtifactVisited(true);
  }, [isVisible]);

  const handleCollapse = () => {
    if (viewMode) {
      if (typeof onExitView === 'function') onExitView();
      else onStateChange('peek');
      if (typeof onClose === 'function') onClose();
      return;
    }
    onStateChange('peek');
    if (typeof onClose === 'function') onClose();
  };

  // One owner retains the mounted artifact across both visibility and width changes.
  if (!isVisible && !artifactVisited) return null;

  return (
    <div
      className={isMobile
        ? 'absolute inset-0 z-40 min-h-0 pointer-events-none'
        : 'relative flex flex-col min-w-0 min-h-0 h-full self-stretch transition-all duration-500 ease-in-out pt-0'}
      hidden={!isVisible}
      inert={!isVisible}
      aria-hidden={!isVisible}
      style={{
        ...(isMobile ? { paddingBottom: 'env(safe-area-inset-bottom, 0px)' } : { width: desktopWidth }),
        display: isVisible ? undefined : 'none',
      }}
    >
      <div
        className={isMobile
          ? 'h-full min-h-0 w-full rounded-t-3xl bg-[rgba(3,6,15,0.96)] backdrop-blur-2xl border border-[rgba(var(--color-primary-light-rgb),0.35)] border-b-0 shadow-[0_-12px_40px_rgba(2,6,23,0.65)] flex flex-col pointer-events-auto overflow-hidden'
          : 'flex flex-1 flex-col min-h-0 h-full'}
      >
        {/* Drag handle / collapse tap target */}
        {isMobile && <button
          ref={collapseButtonRef}
          type="button"
          onClick={handleCollapse}
          className="flex items-center justify-center pt-3 pb-2 w-full flex-shrink-0"
          aria-label="Collapse artifact workspace"
        >
          <div className="w-10 h-1 rounded-full bg-white/25" />
        </button>}

        {/* Content */}
        <div className={isMobile
          ? 'flex flex-1 flex-col min-h-0 overflow-hidden px-3 pb-4'
          : 'flex flex-1 flex-col min-h-0 h-full overflow-visible pt-0'}>
          {artifactContent ?? (
            <div className="h-full flex items-center justify-center text-white/30 text-sm">
              No artifact yet
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default MobileArtifactDrawer;
