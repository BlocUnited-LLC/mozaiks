export const ChatPageViewWidget = ({
  widgetOverlayOpen,
  onOpen,
  onClose,
  brandLogoSrc,
  onBrandImageError,
  appDisplayName,
  onAskClick,
  onWorkflowClick,
  chatContent,
}) => {
  return (
    <div className={`flex flex-col-reverse items-end gap-2 ${widgetOverlayOpen ? 'mr-[20px] mb-[40px]' : 'mr-3 mb-3'}`}>
      {!widgetOverlayOpen && (
        <button
          type="button"
          onClick={onOpen}
          className="pointer-events-auto group relative w-20 h-20 rounded-2xl bg-gradient-to-br from-[var(--color-primary)] to-[var(--color-secondary)] shadow-[0_8px_32px_rgba(15,23,42,0.6)] border-2 border-[rgba(var(--color-primary-light-rgb),0.5)] hover:shadow-[0_16px_48px_rgba(51,240,250,0.4)] hover:scale-105 transition-all duration-300 flex items-center justify-center"
          title="Open chat"
          aria-label="Open chat"
        >
          <div className="absolute inset-0 rounded-2xl bg-gradient-to-br from-[rgba(var(--color-primary-light-rgb),0.2)] to-transparent opacity-0 group-hover:opacity-100 transition-opacity"></div>
          <img
            src={brandLogoSrc}
            alt="Mozaiks"
            className="w-11 h-11 relative z-10 group-hover:scale-110 transition-transform"
            onError={onBrandImageError}
          />
        </button>
      )}

      <div
        className="w-[26rem] max-w-[calc(100vw-2.5rem)] h-[50vh] md:h-[70vh] min-h-[360px] transition-all duration-300"
        style={{
          opacity: widgetOverlayOpen ? 1 : 0,
          transform: widgetOverlayOpen ? 'translateY(0)' : 'translateY(1.5rem)',
          pointerEvents: widgetOverlayOpen ? 'auto' : 'none',
        }}
      >
        <button
          type="button"
          onClick={onClose}
          className="pointer-events-auto relative group mt-[-1px] z-20"
          title="Minimize chat"
        >
          <div className="w-32 h-8 rounded-t-2xl bg-gradient-to-r from-[rgba(var(--color-primary-rgb),0.4)] to-[rgba(var(--color-secondary-rgb),0.4)] border-t border-l border-r border-[rgba(var(--color-primary-light-rgb),0.4)] backdrop-blur-sm flex items-center justify-center group-hover:bg-gradient-to-r group-hover:from-[rgba(var(--color-primary-rgb),0.6)] group-hover:to-[rgba(var(--color-secondary-rgb),0.6)] transition-all">
            <svg className="w-5 h-5 text-[var(--color-primary-light)] group-hover:text-white transition-colors" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth="2.5">
              <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
            </svg>
          </div>
        </button>

        <div className="h-full bg-gradient-to-br from-gray-900/95 via-slate-900/95 to-black/95 backdrop-blur-xl border border-[rgba(var(--color-primary-light-rgb),0.3)] rounded-2xl rounded-tr-none shadow-2xl overflow-hidden flex flex-col">
          <div className="flex-shrink-0 bg-[rgba(0,0,0,0.6)] border-b border-[rgba(var(--color-primary-light-rgb),0.2)] backdrop-blur-xl">
            <div className="flex flex-row items-center justify-between px-3 py-2.5 sm:px-4 sm:py-3">
              <button
                type="button"
                onClick={onAskClick}
                className="flex items-center gap-2 sm:gap-3 focus:outline-none focus:ring-2 focus:ring-[var(--color-primary-light)]/60 rounded-xl min-w-0 flex-1"
                title="Open Chat Station"
              >
                <span className="w-9 h-9 sm:w-10 sm:h-10 rounded-lg sm:rounded-xl flex items-center justify-center shadow-lg flex-shrink-0 bg-gradient-to-br from-[var(--color-secondary)] to-[var(--color-primary)]">
                  <span className="text-xl sm:text-2xl" role="img" aria-hidden="true">🧠</span>
                </span>
                <span className="text-left min-w-0 flex-1">
                  <span className="block text-sm sm:text-lg font-bold text-white tracking-tight truncate">{`Ask ${appDisplayName}`}</span>
                  <span className="block text-[10px] sm:text-xs text-gray-400 truncate">Chat Station</span>
                </span>
              </button>

              <button
                type="button"
                onClick={onWorkflowClick}
                className="group relative p-2 rounded-lg bg-gradient-to-r from-[rgba(var(--color-primary-rgb),0.1)] to-[rgba(var(--color-secondary-rgb),0.1)] border border-[rgba(var(--color-primary-light-rgb),0.3)] hover:border-[rgba(var(--color-primary-light-rgb),0.6)] transition-all duration-300 backdrop-blur-sm flex-shrink-0"
                title="Resume Workflow"
              >
                <img
                  src={brandLogoSrc}
                  className="w-8 h-8 opacity-70 group-hover:opacity-100 transition-all duration-300 group-hover:scale-105"
                  alt="Workflow"
                  onError={onBrandImageError}
                />
                <div className="absolute inset-0 bg-[rgba(var(--color-primary-light-rgb),0.1)] rounded-lg blur opacity-0 group-hover:opacity-100 transition-opacity duration-300 -z-10"></div>
              </button>
            </div>
          </div>

          <div className="flex-1 overflow-hidden">
            {chatContent}
          </div>
        </div>
      </div>
    </div>
  );
};