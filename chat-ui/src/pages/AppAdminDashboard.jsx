/**
 * AppAdminDashboard — app-owner admin panel group.
 *
 * The canonical visible admin routes are /admin and /admin/* via AdminPortal.
 * This module provides the app-owner panels embedded inside that unified admin
 * shell.
 *
 * ## How panels work
 *
 * The connected app backend is authoritative for which panels are active.
 * `GET {app_backend_url}/api/admin/config` returns:
 *   { panels: [{ id, label, order, plugin? }, ...] }
 *
 * Built-in panel IDs are rendered by components in BUILT_IN_PANELS below.
 * Unknown panel IDs are resolved from the componentRegistry — register a
 * React component via platform/extensions.js using the panel id as the name.
 *
 * ## Adding a custom panel
 *
 * Backend/module: declare in modules/{module}/admin.yaml
 *   panels:
 *     - id: my_module.stats
 *       label: My Module Stats
 *       section: usage
 *       order: 20
 *
 * Frontend: register in platform/extensions.js (or your app's index.js)
 *   import { registerComponent } from '@mozaiks/chat-ui/registry';
 *   import MyModuleStatsPanel from './MyModuleStatsPanel';
 *   registerComponent('my_plugin_stats', MyModuleStatsPanel);
 *
 * Custom panels receive: { backendUrl, auth } props.
 *
 * ## Built-in panels
 *   stats         — user/activity overview stats
 *   users         — paginated user table with suspend/unsuspend
 *   subscriptions — tier breakdown (shown when monetization is enabled)
 */

import { useState, useEffect, useCallback } from 'react';
import { useChatUI } from '../context/ChatUIContext';
import { getComponent } from '../registry/componentRegistry';
import {
  StatCard as AdminStatCard,
  SectionHeading as AdminSectionHeading,
  Badge as AdminBadge,
  ErrorBox as AdminErrorBox,
  Spinner as AdminSpinner,
} from '../admin/components/AdminPrimitives.jsx';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function getAppBackendUrl(config) {
  return (
    config?.appBackendUrl ||
    config?.app_backend_url ||
    (typeof import.meta !== 'undefined' && import.meta.env?.VITE_APP_BACKEND_URL) ||
    null
  );
}

async function fetchWithAuth(url, options = {}, auth = null) {
  const token = await auth?.getToken?.();
  const headers = { 'Content-Type': 'application/json', ...(options.headers || {}) };
  if (token) headers['Authorization'] = `Bearer ${token}`;
  return fetch(url, { ...options, headers });
}

function useAdminData(backendUrl, path, auth, intervalMs = 0) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    if (!backendUrl) { setLoading(false); return; }
    try {
      const res = await fetchWithAuth(`${backendUrl}${path}`, {}, auth);
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      setData(await res.json());
      setError(null);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [backendUrl, path, auth]);

  useEffect(() => {
    load();
    if (intervalMs > 0) {
      const id = setInterval(load, intervalMs);
      return () => clearInterval(id);
    }
  }, [load, intervalMs]);

  return { data, loading, error, refresh: load };
}

// AdminStatCard, AdminSectionHeading, AdminBadge, AdminErrorBox, AdminSpinner
// are re-exported from AdminPrimitives — imported above.
export { AdminStatCard, AdminSectionHeading, AdminBadge, AdminErrorBox, AdminSpinner };

// ---------------------------------------------------------------------------
// Built-in panel: Stats
// ---------------------------------------------------------------------------

function StatsPanel({ backendUrl, auth }) {
  const { data, loading, error } = useAdminData(backendUrl, '/api/admin/stats', auth, 30000);

  if (loading) return <AdminSpinner />;
  if (error) return <AdminErrorBox message={`Stats unavailable: ${error}`} />;

  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
      <AdminStatCard label="Total Users"   value={data?.total_users}      accent />
      <AdminStatCard label="Active (30d)"  value={data?.active_users_30d} />
      <AdminStatCard label="New (7d)"      value={data?.new_users_7d} />
      <AdminStatCard label="Suspended"     value={data?.suspended_users}
        sub={data?.suspended_users > 0 ? 'review in Users' : 'none'} />
      <AdminStatCard label="Free"          value={data?.tier_breakdown?.free} />
      <AdminStatCard label="Pro"           value={data?.tier_breakdown?.pro} />
      {data?.tier_breakdown?.enterprise != null && (
        <AdminStatCard label="Enterprise"  value={data.tier_breakdown.enterprise} />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Built-in panel: Users
// ---------------------------------------------------------------------------

function UsersPanel({ backendUrl, auth }) {
  const [page, setPage] = useState(1);
  const [search, setSearch] = useState('');
  const [filter, setFilter] = useState('');
  const [actionLoading, setActionLoading] = useState(false);
  const [actionError, setActionError] = useState(null);

  const query = new URLSearchParams({
    page,
    limit: 20,
    ...(search && { q: search }),
    ...(filter && { disabled: filter }),
  }).toString();

  const { data, loading, error, refresh } = useAdminData(backendUrl, `/api/admin/users?${query}`, auth);

  const runAction = useCallback(async (action, userId) => {
    setActionError(null);
    setActionLoading(true);
    try {
      const res = await fetchWithAuth(`${backendUrl}/api/admin/users/action`, {
        method: 'POST',
        body: JSON.stringify({ action, targetIds: [userId] }),
      }, auth);
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      await refresh();
    } catch (e) {
      setActionError(e.message);
    } finally {
      setActionLoading(false);
    }
  }, [backendUrl, auth, refresh]);

  const users = data?.items ?? [];
  const totalPages = data?.pages ?? 1;

  return (
    <div>
      <div className="flex flex-col sm:flex-row gap-3 mb-4">
        <input
          className="flex-1 rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground focus:outline-none focus:ring-2 focus:ring-primary/40"
          placeholder="Search username or email…"
          value={search}
          onChange={e => { setSearch(e.target.value); setPage(1); }}
        />
        <select
          className="rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground focus:outline-none"
          value={filter}
          onChange={e => { setFilter(e.target.value); setPage(1); }}
        >
          <option value="">All users</option>
          <option value="false">Active only</option>
          <option value="true">Suspended only</option>
        </select>
        <button
          onClick={refresh}
          className="text-xs text-primary hover:text-primary/80 transition-colors px-2"
        >
          Refresh
        </button>
      </div>

      {actionError && <div className="mb-3"><AdminErrorBox message={actionError} /></div>}

      {loading ? <AdminSpinner /> : error ? <AdminErrorBox message={`Users unavailable: ${error}`} /> : (
        <>
          <div className="text-sm text-muted-foreground mb-2">
            {data?.total ?? 0} user{data?.total !== 1 ? 's' : ''} · page {page} of {totalPages}
          </div>
          <div className="overflow-x-auto rounded-lg border border-border">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border bg-muted/50">
                  {['Username', 'Email', 'Status', 'Tier', 'Joined', 'Actions'].map(h => (
                    <th key={h} className="px-3 py-2 text-left text-xs font-medium text-muted-foreground">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {users.length === 0 ? (
                  <tr>
                    <td colSpan={6} className="px-3 py-6 text-center text-muted-foreground text-sm italic">
                      No users found.
                    </td>
                  </tr>
                ) : users.map(u => (
                  <tr key={u.id} className="border-b border-border/50 hover:bg-muted/30 transition-colors">
                    <td className="px-3 py-2 font-medium text-foreground">{u.username}</td>
                    <td className="px-3 py-2 text-muted-foreground truncate max-w-[160px]">{u.email || '—'}</td>
                    <td className="px-3 py-2">
                      <AdminBadge variant={u.disabled ? 'error' : 'success'}>
                        {u.disabled ? 'suspended' : 'active'}
                      </AdminBadge>
                    </td>
                    <td className="px-3 py-2">
                      <AdminBadge variant="default">{u.subscription_tier || 'free'}</AdminBadge>
                    </td>
                    <td className="px-3 py-2 text-muted-foreground text-xs">
                      {u.createdAt ? new Date(u.createdAt).toLocaleDateString() : '—'}
                    </td>
                    <td className="px-3 py-2">
                      <button
                        disabled={actionLoading}
                        onClick={() => runAction(u.disabled ? 'unsuspendUser' : 'suspendUser', u.id)}
                        className={`text-xs transition-colors disabled:opacity-50 ${
                          u.disabled
                            ? 'text-success hover:text-success/80'
                            : 'text-destructive hover:text-destructive/80'
                        }`}
                      >
                        {u.disabled ? 'Unsuspend' : 'Suspend'}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {totalPages > 1 && (
            <div className="flex gap-2 justify-end mt-3">
              <button
                disabled={page <= 1}
                onClick={() => setPage(p => p - 1)}
                className="text-xs text-primary disabled:opacity-40 hover:text-primary/80 transition-colors"
              >
                ← Prev
              </button>
              <button
                disabled={page >= totalPages}
                onClick={() => setPage(p => p + 1)}
                className="text-xs text-primary disabled:opacity-40 hover:text-primary/80 transition-colors"
              >
                Next →
              </button>
            </div>
          )}
        </>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Built-in panel: Subscriptions (shown when MONETIZATION=1)
// ---------------------------------------------------------------------------

function SubscriptionsPanel({ backendUrl, auth }) {
  const { data, loading, error } = useAdminData(backendUrl, '/api/admin/stats', auth, 60000);

  if (loading) return <AdminSpinner />;
  if (error) return <AdminErrorBox message={`Subscriptions unavailable: ${error}`} />;

  const tiers = Object.entries(data?.tier_breakdown ?? {});

  return (
    <div className="rounded-xl border border-border bg-card overflow-hidden">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border bg-muted/50">
            {['Tier', 'Users', 'Share'].map(h => (
              <th key={h} className="px-4 py-2 text-left text-xs font-medium text-muted-foreground">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {tiers.map(([tier, count]) => {
            const total = data?.total_users || 1;
            const pct = ((count / total) * 100).toFixed(1);
            return (
              <tr key={tier} className="border-b border-border/50 hover:bg-muted/30 transition-colors">
                <td className="px-4 py-3">
                  <AdminBadge variant={tier === 'pro' ? 'primary' : tier === 'enterprise' ? 'warning' : 'default'}>
                    {tier}
                  </AdminBadge>
                </td>
                <td className="px-4 py-3 font-medium text-foreground">{count}</td>
                <td className="px-4 py-3 text-muted-foreground">
                  <div className="flex items-center gap-2">
                    <div className="flex-1 h-1.5 rounded-full bg-muted overflow-hidden max-w-[80px]">
                      <div
                        className="h-full rounded-full bg-primary/60"
                        style={{ width: `${pct}%` }}
                      />
                    </div>
                    <span className="text-xs">{pct}%</span>
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Panel registry — add new built-in panels here
// ---------------------------------------------------------------------------

const BUILT_IN_PANELS = {
  stats:         { component: StatsPanel },
  users:         { component: UsersPanel },
  subscriptions: { component: SubscriptionsPanel },
};

const APP_PANEL_SECTION_BY_ID = {
  stats: 'overview',
  users: 'users',
  subscriptions: 'billing',
};

function normalizeAppPanelSection(panelConfig, id) {
  const raw =
    typeof panelConfig === 'object'
      ? panelConfig.section || panelConfig.category || panelConfig.group
      : null;
  const value = String(raw || APP_PANEL_SECTION_BY_ID[id] || 'overview')
    .trim()
    .toLowerCase()
    .replace(/_/g, '-');

  if (value === 'user' || value === 'access' || value === 'users-access') return 'users';
  if (value === 'subscriptions' || value === 'payments' || value === 'revenue') return 'billing';
  if (value === 'runtime' || value === 'usage-health' || value === 'health') return 'usage';
  if (value === 'audit' || value === 'logs') return 'activity';
  return value || 'overview';
}

function normalizeAppAdminPanel(panelConfig) {
  const id = typeof panelConfig === 'string' ? panelConfig : panelConfig?.id;
  if (!id) return null;
  if (typeof panelConfig === 'string') {
    return {
      id,
      label: id,
      section: normalizeAppPanelSection(null, id),
    };
  }
  return {
    ...panelConfig,
    id,
    label: panelConfig?.label || id,
    section: normalizeAppPanelSection(panelConfig, id),
  };
}

function normalizeAppAdminPanels(configPanels) {
  let panels;
  if (Array.isArray(configPanels)) {
    panels = configPanels;
  } else if (configPanels && typeof configPanels === 'object') {
    const appPanels = Array.isArray(configPanels.app) ? configPanels.app : [];
    const modulePanels = Array.isArray(configPanels.modules) ? configPanels.modules : [];
    panels = [...appPanels, ...modulePanels];
  } else {
    panels = [
      { id: 'stats', label: 'Overview' },
      { id: 'users', label: 'Users' },
    ];
  }

  const normalized = panels.map(normalizeAppAdminPanel).filter(Boolean);
  if (normalized.length > 0) {
    return normalized;
  }
  return [
    { id: 'stats', label: 'Overview', section: 'overview' },
    { id: 'users', label: 'Users', section: 'users' },
  ];
}

// ---------------------------------------------------------------------------
// Root
// ---------------------------------------------------------------------------

export function AppAdminPanels({
  embedded = false,
  section = null,
  emptyState = null,
  showNoBackend = true,
} = {}) {
  const { user, config, auth } = useChatUI();
  const backendUrl = getAppBackendUrl(config);

  // Fetch panel config from backend — authoritative source of which panels are active
  const { data: adminConfig } = useAdminData(
    backendUrl, '/api/admin/config', auth
  );

  // Client-side role guard (backend enforces independently)
  const isAdmin = user?.roles?.includes('admin') ?? true; // fallback = no-auth dev mode
  if (!isAdmin) {
    return (
      <div className="flex items-center justify-center min-h-[60vh]">
        <div className="rounded-xl border border-destructive/40 bg-destructive/10 p-8 text-center max-w-sm">
          <h1 className="text-lg font-bold text-destructive mb-2">Access Denied</h1>
          <p className="text-sm text-muted-foreground">Admin role required.</p>
        </div>
      </div>
    );
  }

  if (!backendUrl) {
    if (!showNoBackend) {
      return emptyState || null;
    }
    return (
      <div className="flex items-center justify-center min-h-[60vh]">
        <div className="rounded-xl border border-border bg-card p-8 text-center max-w-sm">
          <h1 className="text-lg font-semibold text-foreground mb-2">No Backend Connected</h1>
          <p className="text-sm text-muted-foreground">
            Connect an app backend and set{' '}
            <code className="text-xs bg-muted px-1 rounded">appBackendUrl</code>{' '}
            in your ChatUI config to enable app administration.
          </p>
        </div>
      </div>
    );
  }

  // Default panel list while config is loading — prevents flash of empty dashboard
  const activePanels = normalizeAppAdminPanels(adminConfig?.panels)
    .filter((panel) => !section || panel.section === section);

  const content = (
    <>
      {!embedded && (
        <div className="mb-8 flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold text-foreground">App Admin</h1>
            <p className="text-sm text-muted-foreground mt-1">
              Manage users and monitor your app
            </p>
          </div>
          <AdminBadge variant="warning">admin</AdminBadge>
        </div>
      )}

      {activePanels.length === 0 ? emptyState : activePanels.map((panelConfig) => {
        const id = panelConfig.id;
        const label = panelConfig.label || id;

        // 1. Built-in panel
        const built = BUILT_IN_PANELS[id];
        if (built) {
          const Panel = built.component;
          return (
            <div key={id}>
              <AdminSectionHeading>{label}</AdminSectionHeading>
              <Panel backendUrl={backendUrl} auth={auth} />
            </div>
          );
        }

        // 2. Custom panel — resolved from componentRegistry
        //    Register via: registerComponent('my_panel_id', MyPanelComponent)
        const Custom = getComponent(id);
        if (Custom) {
          return (
            <div key={id}>
              <AdminSectionHeading>{label}</AdminSectionHeading>
              <Custom backendUrl={backendUrl} auth={auth} />
            </div>
          );
        }

        // 3. Unknown panel id — skip silently in production, warn in dev
        if (process.env.NODE_ENV === 'development') {
          console.warn(`[AppAdminDashboard] Unknown panel id "${id}" — register a component with this name to render it.`);
        }
        return null;
      })}
    </>
  );

  if (embedded) {
    return <div>{content}</div>;
  }

  return (
    <div className="min-h-screen bg-background px-4 py-8 sm:px-8">
      <div className="mx-auto max-w-6xl">
        {content}
      </div>
    </div>
  );
}

export default function AppAdminDashboard() {
  return <AppAdminPanels />;
}
