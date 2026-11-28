import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { InlineEmptyState, ResourceList } from '@mozaiks/chat-ui/ui'
import { WorkspaceLayout } from '@mozaiks/chat-ui/workspace'
import { LinkButton, SegmentedControl, StatusPill, StudioErrorState, StudioLoadingState } from '../../ui/components/StudioShared.jsx'
import { WorkspaceStudioHero } from './AppStudioChrome.jsx'
import { ANALYTICS_PERIODS, DEFAULT_ANALYTICS_PERIOD, formatMetricValue, sortPortfolioRows } from './analyticsModel.js'
import { usePortfolioAnalytics } from './useStudioAnalytics.js'

const METRICS = ['mrr', 'arr', 'mrr_growth', 'nrr', 'arppu']

export default function WorkspaceRevenuePage() {
  const navigate = useNavigate()
  const [period, setPeriod] = useState(DEFAULT_ANALYTICS_PERIOD)
  const { data, loading, error, dataMode } = usePortfolioAnalytics(period)
  const registry = data?.registry || {}
  const rows = sortPortfolioRows(data?.apps || [])
  const label = id => registry[id]?.short_label || registry[id]?.label || id.toUpperCase()
  const value = (row, id) => row.metrics?.[id]?.available
    ? formatMetricValue(registry[id], row.metrics[id].value, 'Unavailable') : 'Unavailable'
  const open = row => row.dashboard_app_id && navigate('/apps/' + encodeURIComponent(row.dashboard_app_id) + '/revenue')
  const appLink = row => row.dashboard_app_id
    ? <LinkButton to={'/apps/' + encodeURIComponent(row.dashboard_app_id) + '/revenue'} variant="ghost" size="sm">{row.name}</LinkButton>
    : <span>{row.name}<span className="block text-xs text-muted-foreground">Dashboard unavailable</span></span>
  const columns = [{ id: 'app', header: 'App', render: appLink },
    ...METRICS.map(id => ({ id, header: label(id), render: row => value(row, id) }))]
  return (
    <WorkspaceLayout>
      <div className="space-y-6">
        <WorkspaceStudioHero title="Revenue" subtitle="Customer revenue by app. Personal wallet balances and hosted account billing are separate." />
        {dataMode === 'demo' && <StatusPill tone="warning">Demo data</StatusPill>}
        <SegmentedControl options={ANALYTICS_PERIODS} value={period} onChange={setPeriod} />
        {loading ? <StudioLoadingState label="Loading app revenue." /> : error ? <StudioErrorState title="Revenue unavailable" message={error} /> : rows.length === 0 ? (
          <InlineEmptyState title="No apps yet" description="Revenue appears for the apps you manage as they record customer billing activity." />
        ) : <ResourceList items={rows} columns={columns} getItemId={row => row.app_id} onRowClick={open}
          renderMobileItem={row => <article className="space-y-3 p-4"><div className="font-semibold">{appLink(row)}</div>
            <dl className="grid grid-cols-2 gap-3">{METRICS.map(id => <div key={id}><dt className="text-xs text-muted-foreground">{label(id)}</dt><dd>{value(row, id)}</dd></div>)}</dl>
          </article>} />}
      </div>
    </WorkspaceLayout>
  )
}
