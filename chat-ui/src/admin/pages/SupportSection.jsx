import {
  StatCard,
  SectionFrame,
  SectionHeading,
  Badge,
  ErrorBox,
  EmptyState,
  Spinner,
  AdminExtensionPanels,
  useAdminFetch,
} from '../components/AdminPrimitives.jsx'

function ErroredRunsTable({ runs }) {
  return (
    <div className="overflow-x-auto rounded-lg border border-border">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-border bg-muted/50">
            {['Workflow', 'App', 'User', 'Turns', 'Runtime'].map(h => (
              <th key={h} className="px-3 py-2 text-left text-xs font-medium text-muted-foreground">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {runs.map((run) => (
            <tr key={run.chat_id} className="border-b border-border/50 hover:bg-muted/30 transition-colors">
              <td className="px-3 py-2 font-medium text-foreground">{run.workflow_name}</td>
              <td className="px-3 py-2 text-muted-foreground">{run.app_id}</td>
              <td className="px-3 py-2 text-muted-foreground truncate max-w-[140px]">{run.user_id}</td>
              <td className="px-3 py-2 text-muted-foreground">{run.agent_turns}</td>
              <td className="px-3 py-2 text-muted-foreground">
                {run.runtime_sec != null ? `${run.runtime_sec.toFixed(1)}s` : '—'}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export function SupportSection({ extensionPanels }) {
  const { data: stats, loading: statsLoading, error: statsError } = useAdminFetch('/api/admin/stats', 30000)
  const { data: runsData } = useAdminFetch('/api/admin/runs')

  const totalErrors = stats?.total_errors ?? 0
  const totalTurns = stats?.total_agent_turns ?? 1
  const errorRate = totalTurns > 0 ? ((totalErrors / totalTurns) * 100).toFixed(1) : '0.0'

  // Surface runs that ended abnormally (no ended_at after many turns is suspicious)
  const runs = runsData?.runs ?? []
  const stalledRuns = runs.filter((r) => !r.ended_at && (r.agent_turns ?? 0) > 30)

  return (
    <SectionFrame
      title="Support"
      description="Error monitoring, stalled runs, and operational support surfaces."
    >
      {statsError ? (
        <ErrorBox message={`Stats unavailable: ${statsError}`} />
      ) : (
        <div className="grid gap-3 sm:grid-cols-3">
          <StatCard
            label="Total Errors"
            value={statsLoading ? '…' : totalErrors}
            sub="Runtime error count"
            accent={totalErrors > 0}
          />
          <StatCard
            label="Active Runs"
            value={statsLoading ? '…' : stats?.active_chats ?? 0}
            sub="Currently running workflows"
          />
          <StatCard
            label="Error Rate"
            value={statsLoading ? '…' : `${errorRate}%`}
            sub="Errors per agent turn"
          />
        </div>
      )}

      {stalledRuns.length > 0 && (
        <>
          <SectionHeading>Stalled Runs</SectionHeading>
          <p className="text-xs text-muted-foreground mb-3">
            Runs with no end timestamp and more than 30 agent turns — may require investigation.
          </p>
          <ErroredRunsTable runs={stalledRuns} />
        </>
      )}

      {totalErrors === 0 && stalledRuns.length === 0 && !statsLoading && !statsError && (
        <div className="mt-4">
          <div className="rounded-lg border border-success/30 bg-success/10 px-4 py-3 text-sm text-success">
            No errors or stalled runs detected.
          </div>
        </div>
      )}

      {extensionPanels.length > 0 && (
        <>
          <SectionHeading>Support Panels</SectionHeading>
          <AdminExtensionPanels panels={extensionPanels} />
        </>
      )}

      {extensionPanels.length === 0 && (totalErrors > 0 || stalledRuns.length > 0) && (
        <div className="mt-4">
          <EmptyState>
            Register support panels via your module's{' '}
            <code className="font-mono text-xs">admin.yaml</code> using the{' '}
            <code className="font-mono text-xs">support</code> section to surface custom diagnostics here.
          </EmptyState>
        </div>
      )}
    </SectionFrame>
  )
}
