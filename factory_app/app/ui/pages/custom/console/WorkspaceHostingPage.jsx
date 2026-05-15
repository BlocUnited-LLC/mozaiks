import { useMemo, useState } from 'react'

import {
  CollectionToolbar,
  InlineEmptyState,
  PageHeader,
  ResourceList,
  SummaryStrip,
} from '@mozaiks/chat-ui/ui'
import { WorkspaceLayout } from '@mozaiks/chat-ui/workspace'
import {
  ConsoleErrorState,
  ConsoleInlineEmptyState,
  ConsoleLoadingState,
  Panel,
  StatusPill,
} from '../../../components/ConsoleShared.jsx'
import { formatCompactNumber } from './AppConsoleChrome.jsx'
import { getConsoleDemoDeploymentRecord } from './consoleDemoData.js'
import { buildHostingSections, formatResourceValue } from './hostingConsoleModel.js'
import buildWorkspacePortfolio from './workspaceConsoleModel.js'
import { useWorkspaceApps } from './useWorkspaceApps.js'


function toneForStatus(status) {
  if (status === 'active') return 'success'
  if (status === 'needs_revision') return 'warning'
  if (status === 'archived') return 'default'
  return 'primary'
}

export default function WorkspaceHostingPage() {
  const { apps, loading, error, dataMode } = useWorkspaceApps('Hosting could not be loaded.')
  const [searchValue, setSearchValue] = useState('')

  const portfolio = useMemo(() => buildWorkspacePortfolio(apps), [apps])
  const visibleRows = useMemo(() => {
    const search = searchValue.trim().toLowerCase()
    if (!search) return portfolio.rows
    return portfolio.rows.filter((row) => row.searchText.includes(search))
  }, [portfolio.rows, searchValue])
  const hostingRows = useMemo(() => visibleRows.map((row) => {
    const appId = row.app?.app_id || row.app?.id || row.id
    const deploymentRecord = dataMode === 'demo' ? getConsoleDemoDeploymentRecord(appId) : null
    const hosting = buildHostingSections({
      appId,
      appName: row.name,
      status: row.status,
      deploymentRecord,
    })

    return {
      ...row,
      deploymentRecord,
      hosting,
    }
  }), [dataMode, visibleRows])
  const preparingReleaseCount = portfolio.rows.filter((row) => ['review', 'configuring', 'deploying'].includes(row.status)).length
  const domainCount = hostingRows.reduce((total, row) => total + row.hosting.domainItems.length, 0)
  const bandwidthTotal = hostingRows.reduce((total, row) => total + Number(row.hosting.bandwidthGb || 0), 0)
  const summaryItems = [
    { id: 'live', label: 'Live Apps', value: formatCompactNumber(portfolio.activeCount, '0'), detail: 'Production' },
    { id: 'failed', label: 'Needs Input', value: formatCompactNumber(portfolio.blockingAlerts, '0'), detail: 'Release blockers' },
    { id: 'domains', label: 'Domains', value: formatCompactNumber(domainCount, '0'), detail: 'Assigned' },
    { id: 'bandwidth', label: 'Bandwidth', value: formatResourceValue(bandwidthTotal, ' GB'), detail: 'Tracked usage' },
  ]
  const columns = [
    {
      id: 'app',
      header: 'App',
      width: '36%',
      render: (row) => (
        <div>
          <div className="font-semibold text-foreground">{row.name}</div>
          <div className="mt-1 max-w-xl text-sm leading-6 text-muted-foreground/88">{row.hosting.planLabel} · {row.hosting.environmentLabel}</div>
        </div>
      ),
    },
    {
      id: 'status',
      header: 'Status',
      width: '16%',
      render: (row) => <StatusPill tone={toneForStatus(row.status)}>{row.snapshot.lifecycleLabel}</StatusPill>,
    },
    {
      id: 'resources',
      header: 'Resources',
      width: '28%',
      cellClassName: 'text-muted-foreground',
      render: (row) => `${row.hosting.domainItems.length} domains · ${formatResourceValue(row.hosting.bandwidthGb, ' GB')}`,
    },
    {
      id: 'updated',
      header: 'Updated',
      width: '20%',
      cellClassName: 'text-muted-foreground',
      render: (row) => row.updatedLabel,
    },
  ]

  if (loading) return <ConsoleLoadingState label="Loading hosting…" />
  if (error) return <ConsoleErrorState title="Hosting Unavailable" message={error} />

  return (
    <WorkspaceLayout>
      <div className="space-y-6">
        <PageHeader
          title="Hosting"
          subtitle="Monitor deployments, domains, environments, and hosting readiness."
        />

        <SummaryStrip items={summaryItems} />

        <section className="space-y-4">
          <CollectionToolbar
            searchValue={searchValue}
            onSearchChange={setSearchValue}
            searchPlaceholder="Search hosting..."
            actions={dataMode === 'demo' ? <StatusPill tone="warning">Demo dataset</StatusPill> : null}
          />

          {hostingRows.length > 0 ? (
            <ResourceList items={hostingRows} columns={columns} getItemId={(row) => row.id} />
          ) : (
            <InlineEmptyState
              title="No apps match this hosting search"
              description="Adjust the search term to bring hosting posture back into view."
            />
          )}
        </section>

        <div className="grid gap-6 xl:grid-cols-2">
          <Panel title="Domains" subtitle="Managed domains appear here after an app receives a mapped environment.">
            {domainCount > 0 ? (
              <div className="space-y-3">
                {hostingRows.flatMap((row) => row.hosting.domainItems.map((item) => (
                  <div key={`${row.id}-${item.label}`} className="flex items-center justify-between gap-3 rounded-2xl border border-border/42 bg-card/30 px-4 py-3 text-sm">
                    <div>
                      <div className="font-semibold text-foreground">{item.label}</div>
                      <div className="mt-1 text-muted-foreground/86">{row.name}</div>
                    </div>
                    <StatusPill tone="success">{item.status}</StatusPill>
                  </div>
                )))}
              </div>
            ) : (
              <ConsoleInlineEmptyState
                title="No domains assigned yet"
                description="Domains appear once an app is mapped to managed hosting."
              />
            )}
          </Panel>

          <Panel title="Environment readiness" subtitle="A compact view of where hosting work is concentrated.">
            <div className="grid gap-3 sm:grid-cols-3">
              <div className="rounded-2xl border border-border/42 bg-card/30 px-4 py-4 text-sm">
                <div className="text-[12px] font-medium text-muted-foreground/82">Build</div>
                <div className="mt-2 text-2xl font-semibold tracking-[-0.025em] text-foreground">{portfolio.buildCount}</div>
              </div>
              <div className="rounded-2xl border border-border/42 bg-card/30 px-4 py-4 text-sm">
                <div className="text-[12px] font-medium text-muted-foreground/82">Provisioning</div>
                <div className="mt-2 text-2xl font-semibold tracking-[-0.025em] text-foreground">{preparingReleaseCount}</div>
              </div>
              <div className="rounded-2xl border border-border/42 bg-card/30 px-4 py-4 text-sm">
                <div className="text-[12px] font-medium text-muted-foreground/82">Production</div>
                <div className="mt-2 text-2xl font-semibold tracking-[-0.025em] text-foreground">{portfolio.activeCount}</div>
              </div>
            </div>
          </Panel>
        </div>
      </div>
    </WorkspaceLayout>
  )
}
