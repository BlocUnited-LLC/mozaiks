import { useState, useEffect } from 'react';
import {
  getTheme,
  applyTheme,
  DEFAULT_THEME,
  getCurrentAppId,
} from './themeProvider';

export function useTheme(appId = null) {
  const [theme, setTheme] = useState(DEFAULT_THEME);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;

    async function loadTheme() {
      try {
        const eid = appId || getCurrentAppId();
        const loadedTheme = await getTheme(eid);
        if (!cancelled) {
          setTheme(loadedTheme);
          applyTheme(loadedTheme);
          setLoading(false);
        }
      } catch (error) {
        console.error('❌ [useTheme] Failed to load theme:', error);
        if (!cancelled) {
          setTheme(DEFAULT_THEME);
          applyTheme(DEFAULT_THEME);
          setLoading(false);
        }
      }
    }

    loadTheme();

    return () => {
      cancelled = true;
    };
  }, [appId]);

  return { theme, loading };
}

export default useTheme;
