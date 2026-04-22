/**
 * AdminPage — unified admin shell.
 *
 * /admin is the canonical admin route. App-owner panels, module panels, and
 * runtime/operator panels are composed here while keeping their backend
 * authorities separate.
 *
 * Access is gated by the "admin" role (client-side guard here + backend
 * enforcement on all /api/admin/* routes).
 *
 * Runtime panels are driven by platform/config/admin.json:
 *   { "panels": { "runtime": ["stats", "runs", "sessions"] } }
 *
 * App and module panels come from the connected app backend's /api/admin/config.
 * Modules contribute panels through their module admin contract.
 */

import { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import { useChatUI } from '../context/ChatUIContext';
import { AppAdminPanels } from './AppAdminDashboard.jsx';
import { getComponent } from '../registry/componentRegistry';
import { BuilderWorkspaceLayout } from '../studio/components/BuilderWorkspaceNav.jsx';

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

function SectionFrame({ title, description, children }) {
  return (
    <section className="rounded-xl border border-border bg-card p-5">
      <div className="mb-4">
        <h2 className="text-lg font-semibold text-foreground">{title}</h2>
        {description ? <p className="mt-1 text-sm text-muted-foreground">{description}</p> : null}
      </div>
      {children}
    </section>
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

function BuilderWorkspacePanel() {
  const navigate = useNavigate();
  const tools = [
    {
      id: 'studio-home',
      label: 'Studio',
      description: 'Workspace status, app intent, and build readiness.',
      path: '/studio',
    },
    {
      id: 'studio-build',
      label: 'Build',
      description: 'Draft a build request and route it into the right workflow.',
      path: '/studio/build',
    },
  ];

  return (
    <div className="grid gap-3 md:grid-cols-2">
      {tools.map((tool) => (
        <button
          key={tool.id}
          type="button"
          onClick={() => navigate(tool.path)}
          className="rounded-lg border border-border bg-background p-4 text-left transition-colors hover:bg-muted"
        >
          <span className="block text-sm font-semibold text-foreground">{tool.label}</span>
          <span className="mt-1 block text-xs text-muted-foreground">{tool.description}</span>
        </button>
      ))}
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

function normalizeModulePanels(configPanels) {
  if (configPanels && Array.isArray(configPanels.modules)) {
    return configPanels.modules;
  }
  return [];
}

function normalizeRuntimePanels(configPanels) {
  if (Array.isArray(configPanels)) {
    return configPanels;
  }
  if (configPanels && Array.isArray(configPanels.runtime)) {
    return configPanels.runtime;
  }
  return ['stats', 'runs', 'sessions'];
}

function getPanelId(panelConfig) {
  return typeof panelConfig === 'string' ? panelConfig : panelConfig?.id;
}

function DeclarativeModulePanel({ panel }) {
  const actions = Array.isArray(panel?.actions) ? panel.actions : [];
  const description = panel?.description || panel?.summary;

  return (
    <div className="rounded-lg border border-border bg-background p-4">
      {description ? <p className="text-sm text-muted-foreground">{description}</p> : null}
      <div className="mt-3 flex flex-wrap gap-2 text-xs text-muted-foreground">
        {panel?.module_id ? <Badge>{panel.module_id}</Badge> : null}
        {panel?.renderer ? <Badge>{panel.renderer}</Badge> : null}
        {panel?.data_source ? <Badge>{panel.data_source}</Badge> : null}
      </div>
      {actions.length > 0 ? (
        <div className="mt-4 flex flex-wrap gap-2">
          {actions.map((action) => {
            const id = typeof action === 'string' ? action : action?.id;
            const label = typeof action === 'object' ? action?.label || id : id;
            if (!id) return null;
            return (
              <button
                key={id}
                type="button"
                className="rounded-md border border-border px-3 py-1.5 text-xs text-foreground hover:bg-muted"
              >
                {label}
              </button>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}

function ModuleAdminPanels({ panels }) {
  if (!panels.length) return null;

  return (
    <>
      {panels.map((panelConfig) => {
        const panelId = getPanelId(panelConfig);
        if (!panelId) return null;
        const label = typeof panelConfig === 'object' && panelConfig?.label ? panelConfig.label : panelId;
        const componentName =
          typeof panelConfig === 'object' && panelConfig?.component ? panelConfig.component : panelId;
        const Custom = getComponent(componentName);

        return (
          <div key={panelId}>
            <SectionHeading>{label}</SectionHeading>
            {Custom ? <Custom panel={panelConfig} /> : <DeclarativeModulePanel panel={panelConfig} />}
          </div>
        );
      })}
    </>
  );
}

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

  const activeRuntimePanels = normalizeRuntimePanels(config?.panels);
  const activeModulePanels = normalizeModulePanels(config?.panels);

  return (
    <BuilderWorkspaceLayout>
      <div className="space-y-6">
        <div className="mb-8 flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold text-foreground">Admin Portal</h1>
            <p className="text-sm text-muted-foreground mt-1">
              App, module, and runtime administration for {user?.name || user?.email || 'this app'}
            </p>
          </div>
          <Badge variant="primary">admin</Badge>
        </div>

        <SectionFrame
          title="App Administration"
          description="App-owner, user, subscription, settings, and module panels from the connected app backend."
        >
          <AppAdminPanels embedded />
        </SectionFrame>

        <SectionFrame
          title="Builder Workspace"
          description="Admin-only app creation and refinement tools."
        >
          <BuilderWorkspacePanel />
        </SectionFrame>

        {activeModulePanels.length > 0 ? (
          <SectionFrame
            title="Module Administration"
            description="Module-level controls and operational panels."
          >
            <ModuleAdminPanels panels={activeModulePanels} />
          </SectionFrame>
        ) : null}

        <SectionFrame
          title="Runtime Operations"
          description="Mozaiks workflow runtime observability. These panels are visible only to runtime/platform operators."
        >
          {activeRuntimePanels.map((panelConfig) => {
            const panelId = getPanelId(panelConfig);
            const built = BUILT_IN_PANELS[panelId];
            if (!built) return null;
            const label = typeof panelConfig === 'object' && panelConfig?.label ? panelConfig.label : built.label;
            const Panel = built.component;
            return (
              <div key={panelId}>
                <SectionHeading>{label}</SectionHeading>
                <Panel />
              </div>
            );
          })}
        </SectionFrame>
      </div>
    </BuilderWorkspaceLayout>
  );
}
