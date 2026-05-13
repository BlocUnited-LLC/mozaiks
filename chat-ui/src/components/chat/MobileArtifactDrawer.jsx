import React from 'react';

const MobileArtifactDrawer = ({
  state = 'hidden',
  onStateChange = () => {},
  onClose = () => {},
  artifactContent = null,
  hasUnseenChat = false,
  hasUnseenArtifact = false,
  viewMode = false,
  chatTheme = null,
  onExitView = null
}) => {
  const isExpanded = viewMode || state === 'expanded';
  const isHidden = state === 'hidden';

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

  const expandedStyle = !viewMode && isExpanded
    ? { height: 'calc(100dvh - env(safe-area-inset-top, 0px) - var(--shell-header-height, 4rem))' }
    : undefined;

  // Peek state: nothing rendered — toggle button in the header handles open
  if (isHidden || !isExpanded) return null;

  return (
    <div
      className="absolute inset-x-0 bottom-0 z-40 pointer-events-none"
      style={{ paddingBottom: 'env(safe-area-inset-bottom, 0px)' }}
    >
      <div
        className="w-full rounded-t-3xl bg-[rgba(3,6,15,0.96)] backdrop-blur-2xl border border-[rgba(var(--color-primary-light-rgb),0.35)] border-b-0 shadow-[0_-12px_40px_rgba(2,6,23,0.65)] flex flex-col pointer-events-auto overflow-hidden"
        style={expandedStyle}
      >
        {/* Drag handle / collapse tap target */}
        <button
          type="button"
          onClick={handleCollapse}
          className="flex items-center justify-center pt-3 pb-2 w-full flex-shrink-0"
          aria-label="Collapse artifact workspace"
        >
          <div className="w-10 h-1 rounded-full bg-white/25" />
        </button>

        {/* Content */}
        <div className="flex-1 overflow-y-auto px-3 pb-4">
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
