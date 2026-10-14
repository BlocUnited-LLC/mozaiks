import { useState, useEffect } from 'react';
import { PageTitleStyleError } from '../ui/theme/pageTitle.js';
import {
  getTheme,
  getCachedTheme,
  applyTheme,
  DEFAULT_THEME,
  getCurrentAppId,
} from './themeProvider';

export function useTheme(appId = null) {
  const resolvedAppId = appId || getCurrentAppId();
  const [result, setResult] = useState(null);
  const theme = getCachedTheme(resolvedAppId)
    || (result?.appId === resolvedAppId ? result.theme : null);

  useEffect(() => {
    let cancelled = false;

    async function loadTheme() {
      try {
        const loadedTheme = await getTheme(resolvedAppId);
        if (!cancelled && getCachedTheme(resolvedAppId) === loadedTheme) {
          applyTheme(loadedTheme);
          setResult({ appId: resolvedAppId, theme: loadedTheme });
        }
      } catch (error) {
        console.error('❌ [useTheme] Failed to load theme:', error);
        if (!cancelled) {
          if (error instanceof PageTitleStyleError) {
            setResult({ appId: resolvedAppId, error });
          } else {
            applyTheme(DEFAULT_THEME);
            setResult({ appId: resolvedAppId, theme: DEFAULT_THEME });
          }
        }
      }
    }

    loadTheme();

    return () => {
      cancelled = true;
    };
  }, [resolvedAppId]);

  if (result?.appId === resolvedAppId && result.error) throw result.error;
  return { theme: theme || DEFAULT_THEME, loading: !theme };
}

export default useTheme;
