import { StatCard, ErrorBox, useAdminFetch } from '../components/AdminPrimitives.jsx'
import { USAGE_ENDPOINT, formatCost, formatCount, summarizeUsage } from '../adminUsage.js'

/**
 * Overview — the app's headline numbers.
 *
 * Conversation counts come from /api/admin/stats; token and cost totals come
 * from /api/admin/usage. Nothing here is derived on the client beyond the
 * token and cost wording.
 */
export function OverviewSection() {
  const stats = useAdminFetch('/api/admin/stats', 15000)
  // The usage body carries up to 1,000 events; these three totals do not
  // need it more often than this.
  const usage = useAdminFetch(USAGE_ENDPOINT, 300000)
  const counts = stats.data
  const summary = summarizeUsage(usage.data)
  const failed = counts?.failed_chats ?? 0

  // '…' while the first response is on its way, '—' when it carried no value.
  const count = (value) => (counts ? formatCount(value) : '…')
  const usageValue = (read) => (summary ? read(summary) : usage.data ? '—' : '…')
  const usageNote = (read) => (summary ? read(summary) : undefined)

  return (
    <section className="space-y-3">
      {stats.error && !counts ? (
        <ErrorBox message={`Conversation counts unavailable: ${stats.error}`} />
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
          <StatCard
            label="Running now"
            value={count(counts?.active_chats)}
            sub="Conversations in progress"
            accent
          />
          <StatCard label="Conversations" value={count(counts?.tracked_chats)} sub="All recorded" />
          <StatCard label="Completed" value={count(counts?.completed_chats)} sub="Finished normally" />
          <StatCard
            label="Failed"
            value={count(counts?.failed_chats)}
            sub={failed > 0 ? 'Ended with an error' : 'None'}
          />
        </div>
      )}

      {usage.error && !usage.data ? (
        <ErrorBox message={`AI usage unavailable: ${usage.error}`} />
      ) : (
        <div className="grid gap-3 sm:grid-cols-3">
          <StatCard
            label="Tokens used"
            value={usageValue((s) => formatCount(s.totalTokens))}
            sub={usageNote((s) => s.windowNote)}
          />
          <StatCard
            label="Model calls"
            value={usageValue((s) => formatCount(s.modelCalls))}
            sub={usageNote((s) => s.windowNote)}
          />
          <StatCard
            label="Estimated cost"
            value={usageValue((s) => formatCost(s.cost))}
            sub={usageNote((s) => s.costNote)}
          />
        </div>
      )}
    </section>
  )
}
