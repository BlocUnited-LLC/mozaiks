/**
 * AppAdminDashboard — panels a connected app backend contributes to the
 * framework-owned admin shell.
 *
 * This is a panel group, not a route. The admin shell (AdminPage) calls
 * `useAppBackendAdminPanels()` once and renders each panel on the admin page
 * named by its `section`.
 *
 * ## Contract
 *
 * `GET {app_backend_url}/api/admin/config` returns:
 *   { "schema_version": "mozaiks.admin.app_backend.v1", "panels": [...] }
 *
 * A backend with no such route (404) contributes no panels; that is not an
 * error. Each panel declares one renderer:
 * - `renderer: "schema"` — `layout + sections[]`, the same primitive sections
 *   as module admin panels. Relative `config.api_endpoint` values are served by
 *   the app backend.
 * - `renderer: "custom_component"` — a component key registered in the active
 *   app root `ui/index.js`. Receives `{ backendUrl, auth, panel }`.
 * - `renderer: "builtin"` — one of the shipped panels below. Each one reads
 *   its data from the app backend, so the backend must serve these routes:
 *     stats          GET  /api/admin/stats
 *     users          GET  /api/admin/users, POST /api/admin/users/action
 *     subscriptions  GET  /api/admin/stats
 *   A backend that declares a built-in panel without serving its routes gets
 *   an "unavailable" message in place of the panel.
 */

import { useState, useEffect, useCallback, useMemo } from 'react';
import { useChatUI } from '../context/ChatUIContext';
import { getComponent } from '../registry/componentRegistry';
import {
  StatCard as AdminStatCard,
  SectionHeading as AdminSectionHeading,
  Badge as AdminBadge,
  ErrorBox as AdminErrorBox,
  Spinner as AdminSpinner,
  MissingPanelComponent,
} from '../admin/components/AdminPrimitives.jsx';
import AdminSchemaPanel from '../admin/components/AdminSchemaPanel.jsx';
import { parseAppBackendAdminConfig } from '../admin/contracts/appAdminContract.js';

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

function prefixPanelApiEndpoints(panel, backendUrl) {
  if (!backendUrl || !panel || panel.renderer !== 'schema' || !Array.isArray(panel.sections)) {
    return panel;
  }

  const prefixSection = (section) => {
    if (!section || typeof section !== 'object') return section;
    const config = section.config && typeof section.config === 'object' ? { ...section.config } : {};
    const endpoint = config.api_endpoint;
    if (typeof endpoint === 'string' && endpoint.startsWith('/')) {
      config.api_endpoint = `${backendUrl}${endpoint}`;
    }
    if (Array.isArray(config.children)) {
      config.children = config.children.map(prefixSection);
    }
    return { ...section, config };
  };

  return {
    ...panel,
    sections: panel.sections.map(prefixSection),
  };
}

// ---------------------------------------------------------------------------
// Panel source — the connected app backend's admin config
// ---------------------------------------------------------------------------

/**
 * Load the app backend's admin panels once for the whole admin shell.
 *
 * Returns `{ backendUrl, auth, panels, loading, error }`. With no backend
 * configured, or a backend that has no admin config route, `panels` is empty
 * and `error` is null.
 */
export function useAppBackendAdminPanels() {
  const { config, auth } = useChatUI();
  const backendUrl = getAppBackendUrl(config);
  const [state, setState] = useState({ raw: null, loading: Boolean(backendUrl), error: null });

  useEffect(() => {
    if (!backendUrl) {
      setState({ raw: null, loading: false, error: null });
      return undefined;
    }
    let current = true;
    setState({ raw: null, loading: true, error: null });
    (async () => {
      try {
        const res = await fetchWithAuth(`${backendUrl}/api/admin/config`, {}, auth);
        if (res.status === 404) {
          if (current) setState({ raw: null, loading: false, error: null });
          return;
        }
        if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
        const raw = await res.json();
        if (current) setState({ raw, loading: false, error: null });
      } catch (e) {
        if (current) setState({ raw: null, loading: false, error: e.message });
      }
    })();
    return () => { current = false; };
  }, [backendUrl, auth]);

  const parsed = useMemo(
    () => (state.raw === null ? { panels: [], issues: [] } : parseAppBackendAdminConfig(state.raw)),
    [state.raw],
  );

  useEffect(() => {
    if (parsed.issues.length > 0 && process.env.NODE_ENV === 'development') {
      console.warn('⚠️ [AppAdminDashboard] Invalid app backend admin config', parsed.issues);
    }
  }, [parsed]);

  const error = state.error
    || (parsed.issues.length > 0 && parsed.panels.length === 0 ? parsed.issues[0] : null);

  return { backendUrl, auth, panels: parsed.panels, loading: state.loading, error };
}

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
// Built-in panel: Subscriptions
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
// Panel registry — the shipped built-in panels
// ---------------------------------------------------------------------------

const BUILT_IN_PANELS = {
  stats:         { component: StatsPanel },
  users:         { component: UsersPanel },
  subscriptions: { component: SubscriptionsPanel },
};

// ---------------------------------------------------------------------------
// Panel group
// ---------------------------------------------------------------------------

/**
 * Render app-backend panels for one admin page. `panels` is the subset whose
 * `section` is that page (from `useAppBackendAdminPanels().panels`).
 */
export function AppAdminPanels({ panels = [], backendUrl = null, auth = null }) {
  if (!panels.length) return null;

  return (
    <>
      {panels.map((panelConfig) => {
        const id = panelConfig.id;
        const label = panelConfig.label || id;
        const resolvedPanel = prefixPanelApiEndpoints(panelConfig, backendUrl);

        let body = null;
        if (panelConfig.renderer === 'schema') {
          body = <AdminSchemaPanel panel={resolvedPanel} />;
        } else if (panelConfig.renderer === 'builtin') {
          // parseAppBackendAdminConfig admits only shipped builtin_panel ids.
          const Panel = BUILT_IN_PANELS[panelConfig.builtin_panel].component;
          body = <Panel backendUrl={backendUrl} auth={auth} />;
        } else {
          const Custom = getComponent(panelConfig.component);
          body = Custom
            ? <Custom backendUrl={backendUrl} auth={auth} panel={resolvedPanel} />
            : <MissingPanelComponent componentName={panelConfig.component} />;
        }

        return (
          <div key={id}>
            <AdminSectionHeading>{label}</AdminSectionHeading>
            {body}
          </div>
        );
      })}
    </>
  );
}
