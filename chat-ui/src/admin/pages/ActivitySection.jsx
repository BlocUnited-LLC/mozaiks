import {
  SectionFrame,
  SectionHeading,
  Badge,
  ErrorBox,
  EmptyState,
  Spinner,
  AdminExtensionPanels,
  useAdminFetch,
} from '../components/AdminPrimitives.jsx'

// ---------------------------------------------------------------------------
// Built-in runtime panel
// ---------------------------------------------------------------------------

function SessionsPanel() {
  const { data, loading, error } = useAdminFetch('/api/admin/sessions?limit=25')

  if (loading) return <Spinner />
  if (error) return <ErrorBox message={`Sessions unavailable: ${error}`} />

  const sessions = data?.sessions ?? []

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
                const completed = s.status === 1
                const tokens = (s.usage_prompt_tokens_final ?? 0) + (s.usage_completion_tokens_final ?? 0)
                const started = s.created_at ? new Date(s.created_at).toLocaleString() : '—'
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

const OPERATIONS_PANELS = {
  sessions: { label: 'Recent Sessions', component: SessionsPanel },
}

// ---------------------------------------------------------------------------
// Section
// ---------------------------------------------------------------------------

export function OperationsSection({ runtimePanels, extensionPanels }) {
  const hasRuntimeContent = runtimePanels.length > 0
  const hasExtensionContent = extensionPanels.length > 0

  return (
    <SectionFrame
      title="Operations"
      description="Operational history, validation signals, incidents, and recent runtime activity."
    >
      {runtimePanels.map((panelConfig) => {
        const id = typeof panelConfig === 'string' ? panelConfig : panelConfig?.id
        const built = OPERATIONS_PANELS[id]
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
      {!hasRuntimeContent && !hasExtensionContent && (
        <EmptyState>No operations panels are configured yet.</EmptyState>
      )}
    </SectionFrame>
  )
}
