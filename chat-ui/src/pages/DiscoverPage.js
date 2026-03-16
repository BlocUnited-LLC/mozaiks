import React, { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import Header from '../components/layout/Header';
import Footer from '../components/layout/Footer';
import { Card } from '../adminPortalRegistry';
import { useChatUI } from '../context/ChatUIContext';
import useTheme from '../styles/useTheme';
import { fetchAvailableModules } from '../coreBridge';
import { useNavigation } from '../providers/NavigationProvider';
import { useNavigationActions } from '../navigation/useNavigationActions';

const normalizeToken = (value) => (typeof value === 'string' ? value.trim().toLowerCase() : '');

const pageGroupToken = (page) => (
  normalizeToken(
    page?.group
    || page?.surface
    || page?.meta?.group
    || page?.meta?.surface
  )
);

const isDiscoverPage = (page) => {
  if (!page) return false;
  if (page.discover === true || page?.meta?.discover === true) return true;
  const group = pageGroupToken(page);
  return group === 'discover' || group === 'discovery';
};

const isNavigablePage = (page) => Boolean(page && (page.path || page.href || page.trigger));

export default function DiscoverPage() {
  const navigate = useNavigate();
  const { user, loading, logout } = useChatUI();
  const { theme: chatTheme, loading: themeLoading } = useTheme();
  const { pages } = useNavigation();
  const handleNavigationItem = useNavigationActions();
  const [moduleCount, setModuleCount] = useState(0);

  useEffect(() => {
    let cancelled = false;

    const loadModules = async () => {
      try {
        const payload = await fetchAvailableModules();
        if (cancelled) return;
        const modules = Array.isArray(payload?.modules) ? payload.modules : [];
        setModuleCount(modules.filter((item) => item?.enabled !== false).length);
      } catch (_err) {
        if (!cancelled) setModuleCount(0);
      }
    };

    loadModules();
    return () => {
      cancelled = true;
    };
  }, []);

  const { discoverPages, otherPages } = useMemo(() => {
    const navPages = Array.isArray(pages) ? pages : [];
    const navigable = navPages.filter(isNavigablePage);
    const discoverAssigned = navigable
      .filter(isDiscoverPage)
      .filter((page) => page.path !== '/discover');
    const discoverPaths = new Set(discoverAssigned.map((page) => page.path).filter(Boolean));
    const nonDiscover = navigable
      .filter((page) => page.path !== '/discover')
      .filter((page) => !discoverPaths.has(page.path));

    return {
      discoverPages: discoverAssigned,
      otherPages: nonDiscover,
    };
  }, [pages]);

  const handleHeaderAction = (actionId, action = null) => {
    if (actionId === 'discover') {
      navigate('/discover');
      return;
    }
    if (actionId === 'signout' || action?.action === 'signout') {
      logout();
      return;
    }
    if (actionId === 'navigate' || action?.action === 'navigate') {
      const target = action?.path || action?.href;
      if (!target) return;
      if (/^https?:\/\//i.test(target)) {
        window.location.href = target;
        return;
      }
      navigate(target);
      return;
    }
    if (action?.path || action?.href || action?.trigger) {
      handleNavigationItem(action);
    }
  };

  if (loading) {
    return (
      <div className="min-h-screen bg-neutral-950 flex items-center justify-center">
        <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-cyan-500"></div>
      </div>
    );
  }

  return (
    <div className="min-h-screen flex flex-col bg-[radial-gradient(circle_at_top_right,_rgba(14,165,233,0.18),_transparent_32%),linear-gradient(180deg,_#090f1d,_#11192a)]">
      <Header user={user} chatTheme={chatTheme} themeLoading={themeLoading} onAction={handleHeaderAction} />
      <main className="flex-1 pt-20 pb-12 px-4">
        <div className="max-w-6xl mx-auto space-y-8">
          <div className="space-y-2">
            <h1 className="text-3xl font-bold text-white">Discover</h1>
            <p className="text-slate-300">
              Launch discovery-assigned pages and review other persistent app surfaces.
            </p>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
            <Card title="Discovery Pages">
              <div className="text-4xl font-semibold text-cyan-300">{discoverPages.length}</div>
            </Card>
            <Card title="Other Pages">
              <div className="text-4xl font-semibold text-indigo-300">{otherPages.length}</div>
            </Card>
            <Card title="Enabled Modules">
              <div className="text-4xl font-semibold text-emerald-300">{moduleCount}</div>
            </Card>
          </div>

          <Card title="Discovery Launcher">
            {discoverPages.length === 0 ? (
              <p className="text-slate-400">No discovery pages assigned yet.</p>
            ) : (
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                {discoverPages.map((page) => (
                  <button
                    key={page.id || page.path || page.href}
                    type="button"
                    onClick={() => handleNavigationItem(page)}
                    className="text-left rounded-2xl border border-cyan-400/20 bg-cyan-500/5 hover:bg-cyan-500/10 px-4 py-4 transition-colors"
                  >
                    <div className="text-white font-medium">{page.label || page.id || page.path}</div>
                    <div className="text-slate-400 text-xs mt-1">{page.path || page.href || 'trigger'}</div>
                    <div className="text-slate-300 text-sm mt-2">{page.meta?.title || 'Discovery surface'}</div>
                  </button>
                ))}
              </div>
            )}
          </Card>

          <Card title="Other Persistent Pages">
            {otherPages.length === 0 ? (
              <p className="text-slate-400">No non-discovery pages detected in navigation.</p>
            ) : (
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                {otherPages.map((page) => (
                  <button
                    key={page.id || page.path || page.href}
                    type="button"
                    onClick={() => handleNavigationItem(page)}
                    className="text-left rounded-2xl border border-indigo-400/20 bg-indigo-500/5 hover:bg-indigo-500/10 px-4 py-4 transition-colors"
                  >
                    <div className="text-white font-medium">{page.label || page.id || page.path}</div>
                    <div className="text-slate-400 text-xs mt-1">{page.path || page.href || 'trigger'}</div>
                    <div className="text-slate-300 text-sm mt-2">{page.meta?.title || 'Persistent surface'}</div>
                  </button>
                ))}
              </div>
            )}
          </Card>
        </div>
      </main>
      <Footer chatTheme={chatTheme} />
    </div>
  );
}
