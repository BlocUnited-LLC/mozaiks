import { useEffect, useRef } from 'react';
import { useChatUI } from '../context/ChatUIContext';

/**
 * Hook to enable widget mode for any page outside of ChatPage.
 *
 * Widget mode shows the chat interface as a floating persistent widget
 * and allows users to return to their active workflow via the 🧠 brain toggle.
 *
 * Usage:
 * ```javascript
 * function MyNewPage() {
 *   useWidgetMode();
 *   return <div>My page content</div>;
 * }
 *
 * // Optional: suppress the widget UI on this page (ChatPage will not cover the route)
 * function FullscreenPage() {
 *   useWidgetMode({ showWidget: false });
 *   return <div>...</div>;
 * }
 * ```
 *
 * When the user is in Ask mode and clicks the 🧠 brain icon:
 * - Navigates back to /chat
 * - Fetches the most recent IN_PROGRESS workflow
 * - Switches to workflow mode
 * - Resumes the workflow conversation
 */
export function useWidgetMode(options = {}) {
  const { enabled = true, showWidget = true } = options || {};
  const {
    isInWidgetMode,
    setIsInWidgetMode,
    layoutMode,
    setPreviousLayoutMode,
    isWidgetVisible,
    setIsWidgetVisible,
  } = useChatUI();
  const previousWidgetVisibleRef = useRef(null);

  useEffect(() => {
    if (!enabled) {
      return undefined;
    }

    // Allow pages to suppress the widget UI while keeping widget mode active
    // (so ChatPage doesn't cover the route).
    if (previousWidgetVisibleRef.current === null) {
      previousWidgetVisibleRef.current = isWidgetVisible;
    }
    setIsWidgetVisible(Boolean(showWidget));

    // Enter widget mode when component mounts
    if (!isInWidgetMode) {
      console.log('🧭 [WIDGET_MODE] Entering widget mode (persistent chat on non-ChatPage route)');
      setPreviousLayoutMode(layoutMode);
      setIsInWidgetMode(true);
    }

    // DO NOT exit widget mode on unmount - this would clear the flag during navigation.
    // ChatPage will handle clearing isInWidgetMode after it processes the return from widget mode.
    return () => {
      // Restore widget visibility when leaving this page (do NOT clear widget mode here).
      try {
        if (previousWidgetVisibleRef.current !== null) {
          setIsWidgetVisible(previousWidgetVisibleRef.current);
        } else {
          setIsWidgetVisible(true);
        }
      } catch (_) {
        /* ignore */
      }
    };
    // Intentionally run once on mount; ChatPage clears widget mode after it processes returns.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return { isInWidgetMode };
}
