import React from 'react';

const MobileArtifactDrawer = ({
  state = 'hidden',
  onStateChange = () => {},
  onClose = () => {},
  artifactContent = null,
  hasUnseenChat = false,
  hasUnseenArtifact = false,
  viewMode = false,
  onExitView = null
}) => {
  const isExpanded = viewMode || state === 'expanded';
  const isHidden = state === 'hidden';
  const isPeek = !isExpanded && !isHidden;
  const heightClass = viewMode ? 'h-screen' : (isExpanded ? 'h-[calc(100vh-5rem)]' : isHidden ? 'h-0' : 'h-20');

  const baseContainerClasses = 'relative w-full bg-[rgba(3,6,15,0.96)] backdrop-blur-2xl border border-[rgba(var(--color-primary-light-rgb),0.45)] shadow-[0_-12px_40px_rgba(2,6,23,0.65)] overflow-hidden transition-all duration-300 pointer-events-auto flex flex-col';
  const expandedShapeClasses = 'w-full rounded-none rounded-t-3xl';
  const peekShapeClasses = 'w-full rounded-[2rem] shadow-[0_22px_55px_rgba(2,6,23,0.7)] border-[rgba(var(--color-primary-light-rgb),0.6)]';
  const containerClasses = `${heightClass} ${baseContainerClasses} ${isExpanded ? expandedShapeClasses : ''} ${isPeek ? peekShapeClasses : ''}`;

  const handleToggle = () => {
    if (viewMode) {
      return;
    }
    if (isExpanded) {
      onStateChange('peek');
      if (typeof onClose === 'function') {
        onClose();
      }
    } else {
      onStateChange('expanded');
    }
  };

  const contentVisibilityClasses = state === 'expanded'
    ? 'opacity-100 pointer-events-auto'
    : 'opacity-0 pointer-events-none';

  return (
    <div className="absolute inset-x-0 bottom-0 z-40 pointer-events-none">
      <div className={`${containerClasses}`}>
        {!viewMode && (
          <button
            type="button"
            onClick={handleToggle}
            className="flex items-center justify-between px-3 py-3 sm:px-5 sm:py-4 bg-transparent text-left gap-2"
          >
            <div className="flex items-center gap-2 sm:gap-3 min-w-0 flex-1">
              <div className="w-9 h-9 sm:w-11 sm:h-11 rounded-xl sm:rounded-2xl bg-gradient-to-br from-[var(--color-primary)] to-[var(--color-secondary)] flex items-center justify-center shadow-lg p-1.5 sm:p-2 flex-shrink-0">
                <img
                  src="/assets/mozaik_logo.svg"
                  alt="Mozaiks"
                  className="w-full h-full object-contain"
                  onError={(e) => {
                    e.currentTarget.onerror = null;
                    e.currentTarget.src = '/assets/mozaik.png';
                  }}
                />
              </div>
              <div className="min-w-0 flex-1">
                <p className="text-xs sm:text-sm font-semibold text-white tracking-wide truncate">Artifact Workspace</p>
                <p className="text-[10px] sm:text-[11px] uppercase tracking-[0.15em] sm:tracking-[0.2em] text-[rgba(255,255,255,0.55)] truncate">
                  {isExpanded ? 'Swipe down' : 'Tap to expand'}
                </p>
              </div>
            </div>
            <div className="flex items-center gap-1.5 sm:gap-2 flex-shrink-0">
              {hasUnseenChat && (
                <span className="hidden sm:inline-flex px-2 sm:px-3 py-0.5 sm:py-1 rounded-full text-[10px] sm:text-[11px] font-semibold bg-[rgba(var(--color-error-rgb),0.15)] text-[var(--color-error)] border border-[rgba(var(--color-error-rgb),0.4)]">
                  <span className="hidden sm:inline">Chat updated</span>
                  <span className="sm:hidden">New</span>
                </span>
              )}
              {hasUnseenArtifact && !isExpanded && (
                <span className="w-2 h-2 sm:w-2.5 sm:h-2.5 rounded-full bg-[var(--color-secondary)] shadow-[0_0_12px_rgba(var(--color-secondary-rgb),0.8)]" />
              )}
              <span className={`inline-flex items-center justify-center rounded-full border border-white/15 text-white/80 text-sm w-8 h-8 sm:w-10 sm:h-10 transition-transform ${isExpanded ? 'rotate-180' : ''}`}>
                <svg
                  xmlns="http://www.w3.org/2000/svg"
                  fill="none"
                  viewBox="0 0 24 24"
                  strokeWidth={2.5}
                  stroke="currentColor"
                  className="w-4 h-4 sm:w-5 sm:h-5"
                >
                  <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
                </svg>
              </span>
            </div>
          </button>
        )}

        <div className={`flex-1 overflow-hidden transition-opacity duration-200 ${contentVisibilityClasses}`}>
          <div className="h-full overflow-y-auto px-2 sm:px-4 pb-4 sm:pb-6">
            {artifactContent ? (
              <div className="h-full">{artifactContent}</div>
            ) : (
              <div className="h-full flex items-center justify-center px-4">
                <div className="w-24 h-24 sm:w-32 sm:h-32 bg-gradient-to-br from-[var(--color-primary)]/25 to-[var(--color-secondary)]/25 rounded-2xl sm:rounded-3xl border-2 border-[var(--color-primary-light)]/50 flex items-center justify-center backdrop-blur-sm shadow-2xl">
                  <img
                    src="/assets/mozaik_logo.svg"
                    alt="Mozaiks Logo"
                    className="w-16 h-16 sm:w-20 sm:h-20"
                    onError={(e) => {
                      e.currentTarget.onerror = null;
                      e.currentTarget.src = '/assets/mozaik.png';
                    }}
                  />
                </div>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};

export default MobileArtifactDrawer;
