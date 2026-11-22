import {
  StatCard,
  SectionHeading,
  Badge,
  ErrorBox,
  Spinner,
  useAdminFetch,
} from '../components/AdminPrimitives.jsx'
import {
  USAGE_ENDPOINT,
  formatCost,
  formatCount,
  formatDuration,
  sessionStatus,
  summarizeUsage,
  usageByChatId,
} from '../adminUsage.js'

// ---------------------------------------------------------------------------
// Framework panels for the Usage page
// ---------------------------------------------------------------------------

/** Token and cost totals from the runtime usage ledger. */
export function StatsPanel({ usageState }) {
  const { data, loading, error } = usageState

  if (!data && loading) return <Spinner />
  if (!data && error) return <ErrorBox message={`AI usage unavailable: ${error}`} />

  const summary = summarizeUsage(data)
  if (!summary) return <ErrorBox message="AI usage unavailable: the server sent no totals." />

  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-5">
      <StatCard label="Total tokens" value={formatCount(summary.totalTokens)} sub={summary.windowNote} accent />
      <StatCard label="Input tokens" value={formatCount(summary.promptTokens)} />
      <StatCard label="Output tokens" value={formatCount(summary.completionTokens)} />
      <StatCard label="Model calls" value={formatCount(summary.modelCalls)} sub={summary.capped ? summary.windowNote : undefined} />
      <StatCard label="Estimated cost" value={formatCost(summary.cost)} sub={summary.costNote} />
    </div>
  )
}

/** Recent runs, each joined to its token and cost row from the usage ledger. */
export function RunsPanel({ usageState }) {
  const runsState = useAdminFetch('/api/admin/runs', 10000)

  if (!runsState.data && runsState.loading) return <Spinner />
  if (!runsState.data && runsState.error) return <ErrorBox message={`Runs unavailable: ${runsState.error}`} />

  const runs = runsState.data?.runs ?? []
  const usageByRun = usageByChatId(usageState.data)
  const usageWindow = summarizeUsage(usageState.data)

  return (
    <div>
      <div className="flex items-center justify-between mb-3">
        <span className="text-sm text-muted-foreground">
          {runs.length} recent run{runs.length !== 1 ? 's' : ''}
        </span>
        <button
          type="button"
          onClick={() => { runsState.refresh(); usageState.refresh() }}
          className="text-xs text-primary hover:text-primary/80 transition-colors"
        >
          Refresh
        </button>
      </div>
      {usageWindow?.capped ? (
        <p className="mb-3 text-xs text-muted-foreground">
          Tokens and cost are counted from the {usageWindow.windowNote.toLowerCase()}. An older run
          shows —, and a run that began before them shows only part of its total.
        </p>
      ) : null}
      {runs.length === 0 ? (
        <p className="text-sm text-muted-foreground italic">No runs recorded yet.</p>
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
                const status = sessionStatus(run.status)
                // A run with no ledger row either made no model call or falls
                // outside the calls the usage endpoint totals; show no number.
                // A run whose models have no price shows no dollar figure.
                const usage = usageByRun.get(run.chat_id)
                return (
                  <tr key={run.chat_id} className="border-b border-border/50 hover:bg-muted/30 transition-colors">
                    <td className="px-3 py-2 font-medium text-foreground">{run.workflow_name}</td>
                    <td className="px-3 py-2 text-muted-foreground">{run.app_id}</td>
                    <td className="px-3 py-2 text-muted-foreground truncate max-w-[120px]">{run.user_id}</td>
                    <td className="px-3 py-2">
                      <Badge variant={status.variant}>{status.label}</Badge>
                    </td>
                    <td className="px-3 py-2 text-muted-foreground">{formatCount(run.agent_turns)}</td>
                    <td className="px-3 py-2 text-muted-foreground">{formatCount(usage?.total_tokens)}</td>
                    <td className="px-3 py-2 text-muted-foreground">
                      {formatCost(usage?.priced ? usage.estimated_cost_usd : null)}
                    </td>
                    <td className="px-3 py-2 text-muted-foreground">{formatDuration(run.runtime_sec)}</td>
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
// Panel registry for this page — ids match DEFAULT_RUNTIME_PANELS on the server
// ---------------------------------------------------------------------------

const USAGE_PANELS = {
  stats: { label: 'AI Usage',    component: StatsPanel },
  runs:  { label: 'Recent Runs', component: RunsPanel },
}

// ---------------------------------------------------------------------------
// Section
// ---------------------------------------------------------------------------

export function UsageSection({ runtimePanels = [] }) {
  // One usage request feeds both panels.
  const usageState = useAdminFetch(USAGE_ENDPOINT, 60000)

  return (
    <section>
      {runtimePanels.map((panelConfig) => {
        const built = USAGE_PANELS[panelConfig?.id]
        if (!built) return null
        const Panel = built.component
        return (
          <div key={panelConfig.id}>
            <SectionHeading>{panelConfig.label || built.label}</SectionHeading>
            <Panel usageState={usageState} />
          </div>
        )
      })}
    </section>
  )
}
