/**
 * AdminPage — First-class framework admin dashboard.
 *
 * Lives in chat-ui alongside ChatPage. Registered in coreComponents.js so
 * every app gets it automatically — no platform/extensions.js wiring needed.
 *
 * Access is gated by the "admin" role (client-side guard here + backend
 * enforcement on all /api/admin/* routes).
 *
 * Panels driven by platform/config/admin.json:
 *   { "panels": ["stats", "runs", "sessions"] }
 *
 * Custom panels: add a string to admin.json "panels" and register a React
 * component with that name via platform/extensions.js. The framework renders
 * the slot; the app fills it.
 */

import { useState, useEffect, useCallback } from 'react';
import { useChatUI } from '../context/ChatUIContext';

const API_BASE =
  (typeof import.meta !== 'undefined' && import.meta.env?.VITE_API_URL) ||
  'http://localhost:8000';

// ---------------------------------------------------------------------------
// Data fetching
// ---------------------------------------------------------------------------

function useAdminData(endpoint, intervalMs = 0) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const load = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}${endpoint}`);
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
      setData(await res.json());
      setError(null);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [endpoint]);

  useEffect(() => {
    load();
    if (intervalMs > 0) {
      const id = setInterval(load, intervalMs);
      return () => clearInterval(id);
    }
  }, [load, intervalMs]);

  return { data, loading, error, refresh: load };
}

// ---------------------------------------------------------------------------
// Primitive UI
// ---------------------------------------------------------------------------

function StatCard({ label, value, sub, accent = false }) {
  return (
    <div className={`rounded-xl border p-5 flex flex-col gap-1 ${accent ? 'border-primary/40 bg-primary/10' : 'border-border bg-card'}`}>
      <span className="text-xs text-muted-foreground uppercase tracking-wide">{label}</span>
      <span className={`text-3xl font-bold ${accent ? 'text-primary' : 'text-foreground'}`}>
        {value ?? '—'}
      </span>
      {sub && <span className="text-xs text-muted-foreground">{sub}</span>}
    </div>
  );
}

function SectionHeading({ children }) {
  return (
    <h2 className="text-sm font-semibold text-muted-foreground uppercase tracking-widest mt-6 mb-3">
      {children}
    </h2>
  );
}

function Badge({ children, variant = 'default' }) {
  const styles = {
    default: 'bg-muted text-muted-foreground',
    success: 'bg-success/20 text-success',
    warning: 'bg-warning/20 text-warning',
    error:   'bg-destructive/20 text-destructive',
    primary: 'bg-primary/20 text-primary',
  };
  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${styles[variant]}`}>
      {children}
    </span>
  );
}

function ErrorBox({ message }) {
  return (
    <div className="rounded-lg border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm text-destructive">
      {message}
    </div>
  );
}

function Spinner() {
  return (
    <div className="flex items-center gap-2 text-muted-foreground text-sm">
      <div className="h-4 w-4 animate-spin rounded-full border-2 border-primary border-t-transparent" />
      Loading…
    </div>
  );
}

// ---------------------------------------------------------------------------
// Built-in panels
// ---------------------------------------------------------------------------

function StatsPanel() {
  const { data, loading, error } = useAdminData('/api/admin/stats', 15000);

  if (loading) return <Spinner />;
  if (error) return <ErrorBox message={`Stats unavailable: ${error}`} />;

  const totalTokens = (data.total_prompt_tokens ?? 0) + (data.total_completion_tokens ?? 0);

  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
      <StatCard label="Active Chats"        value={data.active_chats}     accent />
      <StatCard label="Total Runs"          value={data.tracked_chats} />
      <StatCard label="Agent Turns"         value={data.total_agent_turns} />
      <StatCard label="Errors"              value={data.total_errors}
        sub={data.total_errors > 0 ? 'check runs panel' : 'clean'} />
      <StatCard label="Prompt Tokens"       value={data.total_prompt_tokens?.toLocaleString()} />
      <StatCard label="Completion Tokens"   value={data.total_completion_tokens?.toLocaleString()} />
      <StatCard label="Total Tokens"        value={totalTokens.toLocaleString()} />
      <StatCard label="Est. Cost"           value={`$${(data.total_cost ?? 0).toFixed(4)}`} />
    </div>
  );
}

function RunsPanel() {
  const { data, loading, error, refresh } = useAdminData('/api/admin/runs', 10000);

  if (loading) return <Spinner />;
  if (error) return <ErrorBox message={`Runs unavailable: ${error}`} />;

  const runs = data?.runs ?? [];

  return (
    <div>
      <div className="flex items-center justify-between mb-3">
        <span className="text-sm text-muted-foreground">
          {runs.length} tracked run{runs.length !== 1 ? 's' : ''}
        </span>
        <button onClick={refresh} className="text-xs text-primary hover:text-primary/80 transition-colors">
          Refresh
        </button>
      </div>
      {runs.length === 0 ? (
        <p className="text-sm text-muted-foreground italic">No runs tracked in memory.</p>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-border">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border bg-muted/50">
                {['Workflow', 'App', 'User', 'Status', 'Turns', 'Tokens', 'Cost', 'Runtime'].map(h => (
                  <th key={h} className="px-3 py-2 text-left text-xs font-medium text-muted-foreground">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {runs.map((run) => {
                const active = !run.ended_at;
                const tokens = (run.prompt_tokens ?? 0) + (run.completion_tokens ?? 0);
                return (
                  <tr key={run.chat_id} className="border-b border-border/50 hover:bg-muted/30 transition-colors">
                    <td className="px-3 py-2 font-medium text-foreground">{run.workflow_name}</td>
                    <td className="px-3 py-2 text-muted-foreground">{run.app_id}</td>
                    <td className="px-3 py-2 text-muted-foreground truncate max-w-[120px]">{run.user_id}</td>
                    <td className="px-3 py-2">
                      <Badge variant={active ? 'primary' : 'success'}>
                        {active ? 'running' : 'done'}
                      </Badge>
                    </td>
                    <td className="px-3 py-2 text-muted-foreground">{run.agent_turns}</td>
                    <td className="px-3 py-2 text-muted-foreground">{tokens.toLocaleString()}</td>
                    <td className="px-3 py-2 text-muted-foreground">${(run.cost ?? 0).toFixed(4)}</td>
                    <td className="px-3 py-2 text-muted-foreground">
                      {run.runtime_sec != null ? `${run.runtime_sec.toFixed(1)}s` : '—'}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function SessionsPanel() {
  const { data, loading, error } = useAdminData('/api/admin/sessions?limit=25');

  if (loading) return <Spinner />;
  if (error) return <ErrorBox message={`Sessions unavailable: ${error}`} />;

  const sessions = data?.sessions ?? [];

  return (
    <div>
      <span className="text-sm text-muted-foreground block mb-3">
        {sessions.length} most recent session{sessions.length !== 1 ? 's' : ''}
      </span>
      {sessions.length === 0 ? (
        <p className="text-sm text-muted-foreground italic">No sessions found.</p>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-border">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border bg-muted/50">
                {['Workflow', 'App', 'Status', 'Duration', 'Tokens', 'Cost', 'Started'].map(h => (
                  <th key={h} className="px-3 py-2 text-left text-xs font-medium text-muted-foreground">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {sessions.map((s) => {
                const completed = s.status === 1;
                const tokens = (s.usage_prompt_tokens_final ?? 0) + (s.usage_completion_tokens_final ?? 0);
                const started = s.created_at ? new Date(s.created_at).toLocaleString() : '—';
                return (
                  <tr key={s.id} className="border-b border-border/50 hover:bg-muted/30 transition-colors">
                    <td className="px-3 py-2 font-medium text-foreground">{s.workflow_name}</td>
                    <td className="px-3 py-2 text-muted-foreground">{s.app_id}</td>
                    <td className="px-3 py-2">
                      <Badge variant={completed ? 'success' : 'warning'}>
                        {completed ? 'complete' : 'in progress'}
                      </Badge>
                    </td>
                    <td className="px-3 py-2 text-muted-foreground">
                      {s.duration_sec != null ? `${s.duration_sec.toFixed(1)}s` : '—'}
                    </td>
                    <td className="px-3 py-2 text-muted-foreground">{tokens.toLocaleString()}</td>
                    <td className="px-3 py-2 text-muted-foreground">
                      {s.usage_total_cost_final != null ? `$${s.usage_total_cost_final.toFixed(4)}` : '—'}
                    </td>
                    <td className="px-3 py-2 text-muted-foreground text-xs">{started}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Panel registry
// ---------------------------------------------------------------------------

const BUILT_IN_PANELS = {
  stats:    { label: 'System Stats',    component: StatsPanel },
  runs:     { label: 'Active Runs',     component: RunsPanel },
  sessions: { label: 'Recent Sessions', component: SessionsPanel },
};

// ---------------------------------------------------------------------------
// Root
// ---------------------------------------------------------------------------

export default function AdminPage() {
  const { user } = useChatUI();
  const { data: config } = useAdminData('/api/admin/config');

  // Client-side role guard (backend enforces independently)
  const isAdmin = user?.roles?.includes('admin') ?? true; // true fallback = no-auth dev mode
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

  const activePanels = config?.panels ?? ['stats', 'runs', 'sessions'];

  return (
    <div className="min-h-screen bg-background px-4 py-8 sm:px-8">
      <div className="mx-auto max-w-6xl">

        <div className="mb-8 flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold text-foreground">Admin Portal</h1>
            <p className="text-sm text-muted-foreground mt-1">
              Runtime observability for {user?.name || user?.email || 'this app'}
            </p>
          </div>
          <Badge variant="primary">admin</Badge>
        </div>

        {activePanels.map((panelId) => {
          const built = BUILT_IN_PANELS[panelId];
          if (!built) return null; // custom panels rendered by app via extensions.js
          const Panel = built.component;
          return (
            <div key={panelId}>
              <SectionHeading>{built.label}</SectionHeading>
              <Panel />
            </div>
          );
        })}

      </div>
    </div>
  );
}
