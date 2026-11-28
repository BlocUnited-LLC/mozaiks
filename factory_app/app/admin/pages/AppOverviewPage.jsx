import { Link, useParams } from 'react-router-dom'
import { PageHeader, SummaryStrip } from '@mozaiks/chat-ui/ui'
import { WorkspaceLayout } from '@mozaiks/chat-ui/workspace'
import { StatusPill, StudioErrorState, StudioLoadingState } from '../../ui/components/StudioShared.jsx'
import { formatCompactNumber, formatCurrencyValue, formatDateTimeLabel } from './AppStudioChrome.jsx'
import { getAppLifecycleLabel } from './appStudioModel.js'
import { DEFAULT_ANALYTICS_PERIOD, formatMetricValue } from './analyticsModel.js'
import { useAppStudioData } from './useAppStudioData.js'
import { useAppAnalytics } from './useStudioAnalytics.js'

function metricValue(analytics, id) {
  const metric = analytics?.metrics?.[id]
  return metric?.available ? formatMetricValue(analytics?.registry?.[id], metric.value, 'Unavailable') : 'Unavailable'
}

export default function AppOverviewPage() {
  const { appId } = useParams()
  const { data, loading, error, dataMode, refresh } = useAppStudioData(appId)
  const { data: analytics } = useAppAnalytics(appId, DEFAULT_ANALYTICS_PERIOD)
  if (loading) return <StudioLoadingState label="Loading App Overview." />
  if (error || !data?.summary) return <StudioErrorState title="App Overview Unavailable" message={error || 'No summary returned.'} />
  const app = data.summary.app || {}
  const run = app.current_build_run
  const phase = run?.phase
  const phaseLabel = phase === 'genesis' ? 'Genesis Build' : phase === 'refinement' ? 'Refinement Run' : phase
  const stepLabel = typeof run?.active_workflow_id === 'string'
    ? run.active_workflow_id.replace(/([a-z0-9])([A-Z])/g, '$1 $2').replaceAll('_', ' ') : null
  const usage = data.usage
  const unpriced = usage?.pricing_health?.status === 'unpriced_models'
    || (usage?.cost_source === 'not_configured' && Number(usage?.totals?.llm_calls || 0) > 0)
  const items = [
    { id: 'revenue', label: 'MRR', value: metricValue(analytics, 'mrr'), detail: 'App customer revenue' },
    { id: 'users', label: 'Active users', value: metricValue(analytics, 'active_users') },
    { id: 'cost', label: 'Runtime cost', value: formatCurrencyValue(unpriced ? null : usage?.totals?.estimated_cost_usd, 'Unavailable') },
    { id: 'tokens', label: 'Tokens', value: formatCompactNumber(usage?.totals?.total_tokens, 'Unavailable') },
    { id: 'chats', label: 'Chats', value: formatCompactNumber(data.runs?.total, 'Unavailable') },
  ]
  return (
    <WorkspaceLayout>
      <div className="space-y-6">
        <PageHeader title={app.name || app.app_name || appId} subtitle={app.description || 'App overview'}
          actions={[{ id: 'refresh', label: 'Refresh', variant: 'outline' }]} onAction={() => refresh()} />
        <div className="flex flex-wrap items-center gap-2">
          {app.lifecycle_state && <StatusPill tone="default">{getAppLifecycleLabel(app.lifecycle_state)}</StatusPill>}
          {phaseLabel && <Link to={'/apps/' + encodeURIComponent(appId) + '/building'} className="inline-flex rounded-full focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary"
            aria-label={'Open Project: ' + phaseLabel + (stepLabel ? ' · ' + stepLabel : '')}>
            <StatusPill tone="primary">{phaseLabel}{stepLabel ? ' · ' + stepLabel : ''}</StatusPill>
          </Link>}
          {dataMode === 'demo' && <StatusPill tone="warning">Demo data</StatusPill>}
        </div>
        <SummaryStrip items={items} />
        {unpriced && <p className="text-sm text-muted-foreground">Runtime cost is unavailable because some recorded models have no price.</p>}
        {run?.updated_at && <p className="text-sm text-muted-foreground">Build updated {formatDateTimeLabel(run.updated_at)}.</p>}
      </div>
    </WorkspaceLayout>
  )
}
