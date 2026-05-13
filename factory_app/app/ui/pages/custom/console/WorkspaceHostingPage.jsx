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
import { formatCompactNumber } from './AppConsoleChrome.jsx'
import buildWorkspacePortfolio from './workspaceConsoleModel.js'
import { useWorkspaceApps } from './useWorkspaceApps.js'


function toneForStatus(status) {
  if (status === 'active') return 'success'
  if (status === 'needs_revision') return 'warning'
  if (status === 'archived') return 'default'
  return 'primary'
}


export default function WorkspaceHostingPage() {
  const { apps, loading, error } = useWorkspaceApps('Hosting could not be loaded.')
  const [searchValue, setSearchValue] = useState('')

  const portfolio = useMemo(() => buildWorkspacePortfolio(apps), [apps])
  const visibleRows = useMemo(() => {
    const search = searchValue.trim().toLowerCase()
    if (!search) return portfolio.rows
    return portfolio.rows.filter((row) => row.searchText.includes(search))
  }, [portfolio.rows, searchValue])
  const preparingReleaseCount = portfolio.rows.filter((row) => ['review', 'configuring', 'deploying'].includes(row.status)).length
  const summaryItems = [
    { id: 'live', label: 'Live Apps', value: formatCompactNumber(portfolio.activeCount, '0'), detail: 'Currently in production' },
    { id: 'preparing', label: 'Preparing Release', value: formatCompactNumber(preparingReleaseCount, '0'), detail: 'Approaching hosting handoff' },
    { id: 'build', label: 'Build Only', value: formatCompactNumber(portfolio.buildCount, '0'), detail: 'Not yet ready for hosting' },
    { id: 'blocked', label: 'Needs Input', value: formatCompactNumber(portfolio.blockingAlerts, '0'), detail: 'Require release decisions' },
  ]

  if (loading) return <ConsoleLoadingState label="Loading hosting…" />
  if (error) return <ConsoleErrorState title="Hosting Unavailable" message={error} />

  return (
    <AdminWorkspaceLayout>
      <div className="space-y-6">
        <PageHeader
          title="Hosting"
          subtitle="Keep managed hosting posture, release readiness, and production attention visible at the workspace level."
        />

        <SummaryStrip items={summaryItems} />

        <div className="grid gap-6 xl:grid-cols-[minmax(0,1.1fr)_minmax(0,0.9fr)]">
          <Panel eyebrow="Release queue" title="Managed hosting posture" subtitle="Search hosting posture and review which apps are blocked, provisioning, or already healthy.">
            <div className="mb-4">
              <input
                type="search"
                value={searchValue}
                onChange={(event) => setSearchValue(event.target.value)}
                placeholder="Search hosting..."
                className="w-full rounded-[var(--shell-control-radius,1rem)] border border-border bg-card px-4 py-3 text-sm text-foreground outline-none transition focus:border-primary/40 focus:ring-2 focus:ring-primary/20"
              />
            </div>

            {visibleRows.length > 0 ? (
              <div className="space-y-3">
                {visibleRows.map((row) => (
                  <div key={row.id} className="flex flex-wrap items-center justify-between gap-3 rounded-[1.5rem] border border-border/70 bg-card/60 px-4 py-4">
                    <div>
                      <div className="font-semibold text-foreground">{row.name}</div>
                      <div className="mt-1 text-sm text-muted-foreground">{row.stateLabel}</div>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      <StatusPill tone={toneForStatus(row.status)}>{row.snapshot.lifecycleLabel}</StatusPill>
                      <StatusPill tone={row.operationsState.tone}>{row.operationsState.label}</StatusPill>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <ConsoleInlineEmptyState
                title="No apps match this hosting search"
                description="Adjust the search term to bring hosting posture back into view."
              />
            )}
          </Panel>

          <div className="space-y-6">
            <Panel eyebrow="Domains" title="Domains" subtitle="Workspace domains appear after an app reaches a managed hosting posture.">
              <ConsoleInlineEmptyState
                title="No domains assigned yet"
                description="Managed domains will appear here once at least one app completes release setup and receives a mapped environment."
              />
            </Panel>

            <Panel eyebrow="Environments" title="Environment readiness" subtitle="Use environment posture to understand where hosting work is concentrating right now.">
              <div className="grid gap-3 sm:grid-cols-3">
                <div className="rounded-[1.5rem] border border-border/70 bg-card/60 px-4 py-4 text-sm">
                  <div className="text-xs uppercase tracking-[0.16em] text-muted-foreground">Build</div>
                  <div className="mt-2 text-2xl font-semibold text-foreground">{portfolio.buildCount}</div>
                </div>
                <div className="rounded-[1.5rem] border border-border/70 bg-card/60 px-4 py-4 text-sm">
                  <div className="text-xs uppercase tracking-[0.16em] text-muted-foreground">Provisioning</div>
                  <div className="mt-2 text-2xl font-semibold text-foreground">{visibleRows.filter((row) => row.status === 'deploying').length}</div>
                </div>
                <div className="rounded-[1.5rem] border border-border/70 bg-card/60 px-4 py-4 text-sm">
                  <div className="text-xs uppercase tracking-[0.16em] text-muted-foreground">Production</div>
                  <div className="mt-2 text-2xl font-semibold text-foreground">{portfolio.activeCount}</div>
                </div>
              </div>
            </Panel>
          </div>
        </div>
      </div>
    </AdminWorkspaceLayout>
  )
}