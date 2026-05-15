import { useMemo, useState } from 'react'

import { WorkspaceLayout } from '@mozaiks/chat-ui/workspace'
import {
  ConsoleErrorState,
  ConsoleInlineEmptyState,
  ConsoleLoadingState,
  Panel,
  StatusPill,
} from '../../ui/components/ConsoleShared.jsx'
import { WorkspaceConsoleHero, formatCompactNumber, formatCurrencyValue } from './AppConsoleChrome.jsx'
import buildWorkspacePortfolio from './workspaceConsoleModel.js'
import { useWorkspaceConsoleData } from './useWorkspaceConsoleData.js'

function resolveBillingState(row) {
  if (row.status === 'active') return { label: 'Ready', tone: 'success', detail: 'Billing can be connected from the app console.' }
  if (row.status === 'needs_revision' || row.status === 'review') return { label: 'Pending review', tone: 'warning', detail: 'Billing waits for build review and deployment readiness.' }
  return { label: 'Pending setup', tone: 'muted', detail: 'Billing becomes available after the app moves closer to deployment.' }
}

export default function WorkspaceBillingPage() {
  const { apps, loading, error } = useWorkspaceConsoleData('Workspace billing could not be loaded.')
  const [searchValue, setSearchValue] = useState('')
  const portfolio = useMemo(() => buildWorkspacePortfolio(apps), [apps])
  const rows = useMemo(() => {
    const search = searchValue.trim().toLowerCase()
    return portfolio.rows
      .map((row) => ({
        ...row,
        billing: resolveBillingState(row),
      }))
      .filter((row) => !search || `${row.name} ${row.description} ${row.billing.label}`.toLowerCase().includes(search))
  }, [portfolio.rows, searchValue])
  const activeRows = portfolio.rows.filter((row) => row.status === 'active')
  const pendingRows = portfolio.rows.filter((row) => row.status !== 'active')
  const summaryItems = [
    { id: 'revenue', label: 'Total Revenue', value: formatCurrencyValue(0, '$0'), detail: 'Reporting pending' },
    { id: 'live', label: 'Billing Ready', value: formatCompactNumber(activeRows.length, '0'), detail: 'Live apps' },
    { id: 'pending', label: 'Pending Setup', value: formatCompactNumber(pendingRows.length, '0'), detail: 'Build or review required' },
    { id: 'providers', label: 'Providers', value: '0', detail: 'No payment provider connected' },
  ]

  if (loading) return <ConsoleLoadingState label="Loading workspace billing..." />
  if (error) return <ConsoleErrorState title="Workspace Billing Unavailable" message={error} />

  return (
    <WorkspaceLayout>
      <div className="space-y-6">
        <WorkspaceConsoleHero
          title="Billing"
          subtitle="Track which apps are ready for monetization, payment setup, and revenue reporting."
          summaryItems={summaryItems}
        />

        <Panel title="Billing by app" subtitle="Billing reporting pending until payment providers and deployed apps are connected.">
          <div className="mb-4">
            <input
              type="search"
              value={searchValue}
              onChange={(event) => setSearchValue(event.target.value)}
              placeholder="Search billing..."
              className="w-full rounded-[var(--shell-control-radius,1rem)] border border-border bg-card px-4 py-3 text-sm text-foreground outline-none transition focus:border-primary/40 focus:ring-2 focus:ring-primary/20"
            />
          </div>

          {rows.length > 0 ? (
            <div className="divide-y divide-border overflow-hidden rounded-2xl border border-border bg-background/45">
              {rows.map((row) => (
                <div key={row.id} className="grid gap-3 px-4 py-4 md:grid-cols-[minmax(0,1fr)_9rem_minmax(0,1fr)] md:items-center">
                  <div className="min-w-0">
                    <div className="font-semibold text-foreground">{row.name}</div>
                    <div className="mt-1 text-sm text-muted-foreground">{row.description}</div>
                  </div>
                  <StatusPill tone={row.billing.tone}>{row.billing.label}</StatusPill>
                  <div className="text-sm text-muted-foreground">{row.billing.detail}</div>
                </div>
              ))}
            </div>
          ) : (
            <ConsoleInlineEmptyState
              title="Billing reporting pending"
              description="Apps will appear here once the workspace has app records that can be evaluated for billing readiness."
            />
          )}
        </Panel>
      </div>
    </WorkspaceLayout>
  )
}
