import { useState, useCallback } from 'react'
import {
  StatCard,
  SectionFrame,
  SectionHeading,
  Badge,
  ErrorBox,
  Spinner,
  AdminExtensionPanels,
  useAdminFetch,
} from '../components/AdminPrimitives.jsx'

// ---------------------------------------------------------------------------
// Built-in runtime panels
// ---------------------------------------------------------------------------

function StatsPanel() {
  const { data, loading, error } = useAdminFetch('/api/admin/stats', 15000)

  if (loading) return <Spinner />
  if (error) return <ErrorBox message={`Stats unavailable: ${error}`} />

  const totalTokens = (data.total_prompt_tokens ?? 0) + (data.total_completion_tokens ?? 0)

  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
      <StatCard label="Active Chats"      value={data.active_chats}     accent />
      <StatCard label="Total Runs"        value={data.tracked_chats} />
      <StatCard label="Agent Turns"       value={data.total_agent_turns} />
      <StatCard label="Errors"            value={data.total_errors}
        sub={data.total_errors > 0 ? 'check activity panel' : 'clean'} />
      <StatCard label="Prompt Tokens"     value={data.total_prompt_tokens?.toLocaleString()} />
      <StatCard label="Completion Tokens" value={data.total_completion_tokens?.toLocaleString()} />
      <StatCard label="Total Tokens"      value={totalTokens.toLocaleString()} />
      <StatCard label="Est. Cost"         value={`$${(data.total_cost ?? 0).toFixed(4)}`} />
    </div>
  )
}

function RunsPanel() {
  const { data, loading, error, refresh } = useAdminFetch('/api/admin/runs', 10000)

  if (loading) return <Spinner />
  if (error) return <ErrorBox message={`Runs unavailable: ${error}`} />

  const runs = data?.runs ?? []

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
                const active = !run.ended_at
                const tokens = (run.prompt_tokens ?? 0) + (run.completion_tokens ?? 0)
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
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Built-in panel registry for this section
// ---------------------------------------------------------------------------

const USAGE_PANELS = {
  stats: { label: 'System Stats',  component: StatsPanel },
  runs:  { label: 'Active Runs',   component: RunsPanel },
}

// ---------------------------------------------------------------------------
// Section
// ---------------------------------------------------------------------------

export function UsageSection({ runtimePanels, extensionPanels }) {
  return (
    <SectionFrame title="Usage" description="Workflow runs, cost, errors, and app runtime health.">
      {runtimePanels.map((panelConfig) => {
        const id = typeof panelConfig === 'string' ? panelConfig : panelConfig?.id
        const built = USAGE_PANELS[id]
        if (!built) return null
        const label = typeof panelConfig === 'object' && panelConfig?.label ? panelConfig.label : built.label
        const Panel = built.component
        return (
          <div key={id}>
            <SectionHeading>{label}</SectionHeading>
            <Panel />
          </div>
        )
      })}
      <AdminExtensionPanels panels={extensionPanels} />
    </SectionFrame>
  )
}
