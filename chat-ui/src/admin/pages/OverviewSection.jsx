import { StatCard, Badge, useAdminFetch } from '../components/AdminPrimitives.jsx'

export function OverviewSection({ runtimePanels, extensionPanels }) {
  const { data, loading } = useAdminFetch('/api/admin/stats', 15000)
  const totalTokens = data
    ? (data.total_prompt_tokens ?? 0) + (data.total_completion_tokens ?? 0)
    : null

  return (
    <section className="scroll-mt-28">
      <div className="mb-4 flex flex-col justify-between gap-3 md:flex-row md:items-end">
        <h1 className="text-2xl font-bold text-foreground">Overview</h1>
        <Badge variant="primary">admin</Badge>
      </div>

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard
          label="Active Chats"
          value={loading ? '…' : data?.active_chats ?? 0}
          sub="Current workflow conversations"
          accent
        />
        <StatCard
          label="Tracked Runs"
          value={loading ? '…' : data?.tracked_chats ?? 0}
          sub="Persisted runtime operations"
        />
        <StatCard
          label="Admin Extensions"
          value={extensionPanels.length}
          sub="Extra app controls from installed features"
        />
        <StatCard
          label="Token Usage"
          value={loading || totalTokens == null ? '…' : totalTokens.toLocaleString()}
          sub="Prompt + completion tokens"
        />
      </div>

      <div className="mt-3 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard
          label="Runtime Panels"
          value={runtimePanels.length}
          sub="Enabled observability surfaces"
        />
        <StatCard
          label="Errors"
          value={loading ? '…' : data?.total_errors ?? 0}
          sub="Runtime error count"
        />
        <StatCard
          label="Agent Turns"
          value={loading ? '…' : data?.total_agent_turns ?? 0}
          sub="Total orchestrated turns"
        />
        <StatCard
          label="Estimated Cost"
          value={loading ? '…' : `$${(data?.total_cost ?? 0).toFixed(4)}`}
          sub="Runtime usage estimate"
        />
      </div>
    </section>
  )
}
