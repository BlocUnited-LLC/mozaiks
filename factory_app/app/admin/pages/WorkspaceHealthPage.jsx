import { useMemo, useState } from 'react'

import { WorkspaceLayout } from '@mozaiks/chat-ui/workspace'
import {
  StudioErrorState,
  StudioInlineEmptyState,
  StudioLoadingState,
  Panel,
  StatusPill,
} from '../../ui/components/StudioShared.jsx'
import { WorkspaceStudioHero, formatCompactNumber } from './AppStudioChrome.jsx'
import { buildHealthState, formatPercentValue } from './studioHealthModel.js'
import { getStudioDemoDeploymentRecord, getStudioDemoUsageRecord } from './studioDemoData.js'
import buildWorkspacePortfolio from './workspaceStudioModel.js'
import { useWorkspaceStudioData } from './useWorkspaceStudioData.js'

export default function WorkspaceHealthPage() {
  const { apps, loading, error, dataMode } = useWorkspaceStudioData('Workspace health could not be loaded.')
  const [searchValue, setSearchValue] = useState('')

  const portfolio = useMemo(() => buildWorkspacePortfolio(apps), [apps])
  const healthRows = useMemo(() => {
    return portfolio.rows
      .map((row) => {
        const appId = row.app?.app_id || row.app?.id || row.id
        const deploymentRecord = dataMode === 'demo' ? getStudioDemoDeploymentRecord(appId) : null
        const usageRecord = dataMode === 'demo' ? getStudioDemoUsageRecord(appId) : null
        const health = buildHealthState({
          status: row.status,
          totalErrors: Number(usageRecord?.errors || 0),
          runtimeReadiness: row.status === 'active' || row.status === 'deploying' ? 'entry_point_configured' : 'no_workflows',
          uptimePercent: deploymentRecord?.uptime_percent ?? null,
          hasDeploymentFailure: Boolean(deploymentRecord?.failed),
        })

        return {
          ...row,
          deploymentRecord,
          usageRecord,
          health,
          searchText: `${row.searchText} ${health.label} ${(deploymentRecord?.domains || []).join(' ')}`.toLowerCase(),
        }
      })
      .sort((left, right) => left.health.score - right.health.score || left.name.localeCompare(right.name))
  }, [dataMode, portfolio.rows])
  const visibleRows = useMemo(() => {
    const search = searchValue.trim().toLowerCase()
    if (!search) return healthRows
    return healthRows.filter((row) => row.searchText.includes(search))
  }, [healthRows, searchValue])
  const averageHealth = healthRows.length > 0
    ? Math.round(healthRows.reduce((total, row) => total + row.health.score, 0) / healthRows.length)
    : 0
  const summaryItems = [
    { id: 'healthy', label: 'Healthy Apps', value: formatCompactNumber(healthRows.filter((row) => row.health.score >= 85).length, '0') },
    { id: 'stable', label: 'Stable Apps', value: formatCompactNumber(healthRows.filter((row) => row.health.score >= 70 && row.health.score < 85).length, '0') },
    { id: 'risk', label: 'At Risk', value: formatCompactNumber(healthRows.filter((row) => row.health.score < 55).length, '0') },
    { id: 'average', label: 'Average Health', value: `${averageHealth}/100`, detail: 'Portfolio health score' },
  ]

  if (loading) return <StudioLoadingState label="Loading workspace health…" />
  if (error) return <StudioErrorState title="Workspace Health Unavailable" message={error} />

  return (
    <WorkspaceLayout>
      <div className="space-y-6">
        <WorkspaceStudioHero
          title="Health"
          subtitle="See which apps are healthy, which ones are drifting, and where the next app-level intervention should happen."
          summaryItems={summaryItems}
        />

        <Panel eyebrow="Portfolio health" title="Health by app" subtitle="Search the current portfolio and keep the overall health of each app visible without opening each app.">
          <div className="mb-4">
            <input
              type="search"
              value={searchValue}
              onChange={(event) => setSearchValue(event.target.value)}
              placeholder="Search health..."
              className="w-full rounded-[var(--shell-control-radius,1rem)] border border-border bg-card px-4 py-3 text-sm text-foreground outline-none transition focus:border-primary/40 focus:ring-2 focus:ring-primary/20"
            />
          </div>

          {visibleRows.length > 0 ? (
            <div className="space-y-4">
              {visibleRows.map((row) => (
                <div key={row.id} className="rounded-2xl border border-border bg-background px-5 py-5 shadow-sm">
                  <div className="flex flex-wrap items-start justify-between gap-3">
                    <div>
                      <div className="font-semibold text-foreground">{row.name}</div>
                      <div className="mt-0.5 text-sm text-muted-foreground">{row.description}</div>
                    </div>
                    <div className="flex flex-wrap gap-2">
                      <StatusPill tone={row.health.tone}>{row.health.label}</StatusPill>
                      <StatusPill tone={row.snapshot.lifecycleTone}>{row.snapshot.lifecycleLabel}</StatusPill>
                    </div>
                  </div>

                  <div className="mt-4 grid gap-3 text-sm sm:grid-cols-4">
                    <div className="rounded-xl border border-border bg-card px-4 py-3">
                      <div className="text-xs uppercase tracking-[0.16em] text-muted-foreground">Health score</div>
                      <div className="mt-2 text-2xl font-semibold text-foreground">{row.health.score}/100</div>
                    </div>
                    <div className="rounded-xl border border-border bg-card px-4 py-3">
                      <div className="text-xs uppercase tracking-[0.16em] text-muted-foreground">State</div>
                      <div className="mt-2 font-semibold text-foreground">{row.stateLabel}</div>
                    </div>
                    <div className="rounded-xl border border-border bg-card px-4 py-3">
                      <div className="text-xs uppercase tracking-[0.16em] text-muted-foreground">Uptime</div>
                      <div className="mt-2 font-semibold text-foreground">{formatPercentValue(row.deploymentRecord?.uptime_percent)}</div>
                    </div>
                    <div className="rounded-xl border border-border bg-card px-4 py-3">
                      <div className="text-xs uppercase tracking-[0.16em] text-muted-foreground">Errors</div>
                      <div className="mt-2 font-semibold text-foreground">{formatCompactNumber(row.usageRecord?.errors, '0')}</div>
                    </div>
                  </div>

                  <div className="mt-3 space-y-2 text-sm text-muted-foreground">
                    {row.health.issues.length > 0 ? row.health.issues.map((issue) => (
                      <div key={issue} className="rounded-xl border border-border bg-card px-4 py-3">{issue}</div>
                    )) : (
                      <div className="rounded-xl border border-border bg-card px-4 py-3">No workflows configured</div>
                    )}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <StudioInlineEmptyState
              title="No apps match this health search"
              description="Adjust the search term to bring portfolio health back into view."
            />
          )}
        </Panel>
      </div>
    </WorkspaceLayout>
  )
}
