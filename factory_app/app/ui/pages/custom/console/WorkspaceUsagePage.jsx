import { useMemo, useState } from 'react'

import { PageHeader, SummaryStrip } from '@mozaiks/chat-ui/ui'
import { AdminWorkspaceLayout } from '@mozaiks/chat-ui/admin/components/AdminWorkspaceLayout.jsx'
import {
  ConsoleErrorState,
  ConsoleLoadingState,
  Panel,
  StatusPill,
} from '../../../components/ConsoleShared.jsx'
import { formatCompactNumber, formatCurrencyValue } from './AppConsoleChrome.jsx'
import buildWorkspacePortfolio from './workspaceConsoleModel.js'
import { useWorkspaceApps } from './useWorkspaceApps.js'


function exportUsageCsv(rows) {
  const headers = ['app', 'metering', 'operations', 'updated']
  const lines = [
    headers.join(','),
    ...rows.map((row) => [
      JSON.stringify(row.name),
      JSON.stringify(row.usageState.label),
      JSON.stringify(row.operationsState.label),
      JSON.stringify(row.updatedLabel),
    ].join(',')),
  ]
  const blob = new Blob([lines.join('\n')], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = 'workspace-usage.csv'
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
  URL.revokeObjectURL(url)
}

export default function WorkspaceUsagePage() {
  const { apps, metrics, loading, error, dataMode } = useWorkspaceApps('Workspace usage could not be loaded.')
  const [searchValue, setSearchValue] = useState('')

  const portfolio = useMemo(() => buildWorkspacePortfolio(apps), [apps])
  const visibleRows = useMemo(() => {
    const search = searchValue.trim().toLowerCase()
    if (!search) return portfolio.rows
    return portfolio.rows.filter((row) => row.searchText.includes(search))
  }, [portfolio.rows, searchValue])
  const summaryItems = [
    { id: 'tokens', label: 'Tokens Used', value: formatCompactNumber(metrics.total_tokens ?? metrics.tokens_used, 'Pending'), detail: 'Workspace metering' },
    { id: 'cost', label: 'LLM Cost', value: formatCurrencyValue(metrics.total_cost ?? metrics.llm_cost_usd, 'Pending'), detail: 'Observed model spend' },
    { id: 'active', label: 'Active Apps', value: formatCompactNumber(portfolio.activeCount, '0'), detail: 'Live metering' },
    { id: 'build', label: 'Build Queue', value: formatCompactNumber(portfolio.buildCount, '0'), detail: 'Pre-deploy activity' },
  ]

  if (loading) return <ConsoleLoadingState label="Loading workspace usage…" />
  if (error) return <ConsoleErrorState title="Workspace Usage Unavailable" message={error} />

  return (
    <AdminWorkspaceLayout>
      <div className="space-y-6">
        <PageHeader
          title="Usage"
          subtitle="Track usage at the workspace level without losing the app boundary: metering, spend, and which apps are driving the current load."
          actions={[
            { id: 'export', label: 'Export CSV', variant: 'outline' },
          ]}
          onAction={() => exportUsageCsv(visibleRows)}
        />

        <SummaryStrip items={summaryItems} />

        <Panel
          eyebrow="Workspace usage"
          title="Usage by app"
          subtitle="Search the app portfolio and keep the app-level metering posture readable from one surface."
        >
          <div className="mb-4 flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
            <input
              type="search"
              value={searchValue}
              onChange={(event) => setSearchValue(event.target.value)}
              placeholder="Search apps..."
              className="w-full rounded-[var(--shell-control-radius,1rem)] border border-border bg-card px-4 py-3 text-sm text-foreground outline-none transition focus:border-primary/40 focus:ring-2 focus:ring-primary/20 md:max-w-md"
            />
            <div className="flex flex-wrap items-center gap-2">
              {dataMode === 'demo' ? <StatusPill tone="warning">Demo dataset</StatusPill> : null}
              <StatusPill tone="primary">{portfolio.totalApps} tracked apps</StatusPill>
            </div>
          </div>

          <div className="space-y-3">
            {visibleRows.map((row) => (
              <div key={row.id} className="rounded-[1.5rem] border border-border/70 bg-card/60 px-4 py-4">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <div className="font-semibold text-foreground">{row.name}</div>
                    <div className="mt-1 text-sm text-muted-foreground">{row.description}</div>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <StatusPill tone={row.usageState.tone}>{row.usageState.label}</StatusPill>
                    <StatusPill tone={row.operationsState.tone}>{row.operationsState.label}</StatusPill>
                  </div>
                </div>
                <div className="mt-3 grid gap-3 text-sm text-muted-foreground sm:grid-cols-3">
                  <div>
                    <div className="text-xs uppercase tracking-[0.16em] text-muted-foreground/80">Lifecycle</div>
                    <div className="mt-1 text-foreground">{row.snapshot.lifecycleLabel}</div>
                  </div>
                  <div>
                    <div className="text-xs uppercase tracking-[0.16em] text-muted-foreground/80">State</div>
                    <div className="mt-1 text-foreground">{row.stateLabel}</div>
                  </div>
                  <div>
                    <div className="text-xs uppercase tracking-[0.16em] text-muted-foreground/80">Updated</div>
                    <div className="mt-1 text-foreground">{row.updatedLabel}</div>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </Panel>
      </div>
    </AdminWorkspaceLayout>
  )
}
