import { useMemo, useState } from 'react'

import { PageHeader, SummaryStrip } from '@mozaiks/chat-ui/ui'
import { AdminWorkspaceLayout } from '@mozaiks/chat-ui/admin/components/AdminWorkspaceLayout.jsx'
import {
  ConsoleErrorState,
  ConsoleInlineEmptyState,
  ConsoleLoadingState,
  Panel,
  StatusPill,
} from '../../../components/ConsoleShared.jsx'
import { formatCompactNumber, formatCurrencyValue } from './AppConsoleChrome.jsx'
import buildWorkspacePortfolio from './workspaceConsoleModel.js'
import { useWorkspaceApps } from './useWorkspaceApps.js'


export default function WorkspaceBillingPage() {
  const { apps, metrics, loading, error } = useWorkspaceApps('Billing could not be loaded.')
  const [searchValue, setSearchValue] = useState('')

  const portfolio = useMemo(() => buildWorkspacePortfolio(apps), [apps])
  const visibleRows = useMemo(() => {
    const search = searchValue.trim().toLowerCase()
    if (!search) return portfolio.rows
    return portfolio.rows.filter((row) => row.searchText.includes(search))
  }, [portfolio.rows, searchValue])
  const totalRevenue = metrics.total_revenue_usd ?? metrics.revenue_usd ?? null
  const preRevenueCount = Math.max(portfolio.totalApps - portfolio.activeCount, 0)
  const summaryItems = [
    { id: 'revenue', label: 'Total Revenue', value: formatCurrencyValue(totalRevenue, 'Pending'), detail: 'Workspace billing' },
    { id: 'active', label: 'Active Apps', value: formatCompactNumber(portfolio.activeCount, '0'), detail: 'Commercially active' },
    { id: 'pre-revenue', label: 'Pre-Revenue', value: formatCompactNumber(preRevenueCount, '0'), detail: 'Billing still maturing' },
    { id: 'review', label: 'Review Required', value: formatCompactNumber(portfolio.blockingAlerts, '0'), detail: 'Need finance follow-up' },
  ]

  if (loading) return <ConsoleLoadingState label="Loading billing…" />
  if (error) return <ConsoleErrorState title="Billing Unavailable" message={error} />

  return (
    <AdminWorkspaceLayout>
      <div className="space-y-6">
        <PageHeader
          title="Billing"
          subtitle="Track workspace billing posture, revenue readiness, and the apps that still need commercial follow-up."
        />

        <SummaryStrip items={summaryItems} />

        <div className="grid gap-6 xl:grid-cols-[minmax(0,1.1fr)_minmax(0,0.9fr)]">
          <Panel eyebrow="Revenue" title="Revenue summary" subtitle="Search billing posture by app and keep the core finance signals visible without exposing unfinished finance tooling.">
            <div className="mb-4">
              <input
                type="search"
                value={searchValue}
                onChange={(event) => setSearchValue(event.target.value)}
                placeholder="Search billing..."
                className="w-full rounded-[var(--shell-control-radius,1rem)] border border-border bg-card px-4 py-3 text-sm text-foreground outline-none transition focus:border-primary/40 focus:ring-2 focus:ring-primary/20"
              />
            </div>

            <div className="rounded-[1.5rem] border border-warning/25 bg-warning/10 px-4 py-3 text-sm text-foreground">
              Billing reporting pending
            </div>

            <div className="mt-4 grid gap-3 sm:grid-cols-2">
              <div className="rounded-[1.5rem] border border-border/70 bg-card/60 px-4 py-4 text-sm">
                <div className="text-xs uppercase tracking-[0.16em] text-muted-foreground">Revenue Snapshot</div>
                <div className="mt-2 text-2xl font-semibold text-foreground">{formatCurrencyValue(totalRevenue, 'Pending')}</div>
              </div>
              <div className="rounded-[1.5rem] border border-border/70 bg-card/60 px-4 py-4 text-sm">
                <div className="text-xs uppercase tracking-[0.16em] text-muted-foreground">Active Apps</div>
                <div className="mt-2 text-2xl font-semibold text-foreground">{portfolio.activeCount}</div>
              </div>
            </div>
          </Panel>

          <Panel eyebrow="Portfolio" title="Commercial review queue" subtitle="Keep customer value, billing readiness, and the next finance follow-up visible for each app.">
            {visibleRows.length > 0 ? (
              <div className="space-y-3">
                {visibleRows.map((row) => (
                  <div key={row.id} className="rounded-[1.5rem] border border-border/70 bg-card/60 px-4 py-4">
                    <div className="flex items-center justify-between gap-3">
                      <div className="font-semibold text-foreground">{row.name}</div>
                      <StatusPill tone={row.snapshot.lifecycleTone}>{row.snapshot.lifecycleLabel}</StatusPill>
                    </div>
                    <div className="mt-2 text-sm text-muted-foreground">{row.description || row.snapshot.guidance}</div>
                    <div className="mt-3 text-xs text-muted-foreground">Updated {row.updatedLabel}</div>
                  </div>
                ))}
              </div>
            ) : (
              <ConsoleInlineEmptyState
                title="No apps match this billing search"
                description="Adjust the search term to bring billing posture back into view."
              />
            )}
          </Panel>
        </div>
      </div>
    </AdminWorkspaceLayout>
  )
}
