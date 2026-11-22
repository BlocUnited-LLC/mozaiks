import {
  SectionHeading,
  Badge,
  ErrorBox,
  Spinner,
  useAdminFetch,
} from '../components/AdminPrimitives.jsx'
import { formatDuration, formatTimestamp, sessionStatus } from '../adminUsage.js'

// ---------------------------------------------------------------------------
// Framework panel for the Operations page
// ---------------------------------------------------------------------------

/**
 * Recent sessions: what is running and how recent ones ended. Token and cost
 * numbers live on the Usage page; this endpoint carries none.
 */
export function SessionsPanel() {
  const { data, loading, error } = useAdminFetch('/api/admin/sessions?limit=25', 15000)

  if (!data && loading) return <Spinner />
  if (!data && error) return <ErrorBox message={`Sessions unavailable: ${error}`} />
  // The endpoint answers 200 with an `error` field when its query fails.
  if (data?.error) return <ErrorBox message={`Sessions unavailable: ${data.error}`} />

  const sessions = data?.sessions ?? []

  return (
    <div>
      <span className="text-sm text-muted-foreground block mb-3">
        {sessions.length} most recent session{sessions.length !== 1 ? 's' : ''}
      </span>
      {sessions.length === 0 ? (
        <p className="text-sm text-muted-foreground italic">No sessions recorded yet.</p>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-border">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border bg-muted/50">
                {['Workflow', 'App', 'User', 'Status', 'Duration', 'Started'].map(h => (
                  <th key={h} className="px-3 py-2 text-left text-xs font-medium text-muted-foreground">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {sessions.map((s) => {
                const status = sessionStatus(s.status)
                return (
                  <tr key={s.id} className="border-b border-border/50 hover:bg-muted/30 transition-colors">
                    <td className="px-3 py-2 font-medium text-foreground">{s.workflow_name}</td>
                    <td className="px-3 py-2 text-muted-foreground">{s.app_id}</td>
                    <td className="px-3 py-2 text-muted-foreground truncate max-w-[140px]">{s.user_id}</td>
                    <td className="px-3 py-2">
                      <Badge variant={status.variant}>{status.label}</Badge>
                    </td>
                    <td className="px-3 py-2 text-muted-foreground">{formatDuration(s.duration_sec)}</td>
                    <td className="px-3 py-2 text-muted-foreground text-xs">{formatTimestamp(s.created_at)}</td>
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

const OPERATIONS_PANELS = {
  sessions: { label: 'Recent Sessions', component: SessionsPanel },
}

// ---------------------------------------------------------------------------
// Section
// ---------------------------------------------------------------------------

export function OperationsSection({ runtimePanels = [] }) {
  return (
    <section>
      {runtimePanels.map((panelConfig) => {
        const built = OPERATIONS_PANELS[panelConfig?.id]
        if (!built) return null
        const Panel = built.component
        return (
          <div key={panelConfig.id}>
            <SectionHeading>{panelConfig.label || built.label}</SectionHeading>
            <Panel />
          </div>
        )
      })}
    </section>
  )
}
