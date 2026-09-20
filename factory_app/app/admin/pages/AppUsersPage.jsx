import { useState } from 'react'
import { useParams } from 'react-router-dom'

import { MetricSummaryStrip, StageFunnel, UsageTrendPanel } from '@mozaiks/chat-ui/ui'
import { WorkspaceLayout } from '@mozaiks/chat-ui/workspace'
import {
  LinkButton,
  Panel,
  SegmentedControl,
  StatusPill,
  StudioErrorState,
  StudioInlineEmptyState,
  StudioLoadingState,
} from '../../ui/components/StudioShared.jsx'
import AppStudioHero from './AppStudioChrome.jsx'
import {
  ANALYTICS_PERIODS,
  DEFAULT_ANALYTICS_PERIOD,
  USERS_PAGE_HEADLINES,
  USERS_TREND_METRICS,
  buildDeltaView,
  buildFunnelSteps,
  buildHeadlineItems,
  buildTrendPoints,
  formatMetricValue,
} from './analyticsModel.js'
import MetricDetailPanel from './MetricDetailPanel.jsx'
import { useAppAnalytics } from './useStudioAnalytics.js'

export default function AppUsersPage() {
  const { appId = 'workspace-app' } = useParams()
  const [period, setPeriod] = useState(DEFAULT_ANALYTICS_PERIOD)
  const { data, loading, error, dataMode } = useAppAnalytics(appId, period)
  const [trendMetric, setTrendMetric] = useState('active_users')
  const [detailMetricId, setDetailMetricId] = useState(null)

  const registry = data?.registry || {}
  const comparisonLabel = data?.period?.comparison_label || 'vs previous period'
  const appName = data?.app?.name || appId

  if (loading) return <StudioLoadingState label="Loading user analytics…" />
  if (error) return <StudioErrorState title="User Analytics Unavailable" message={error} />

  const usersAvailability = data?.availability?.users || 'none'
  const headlineItems = buildHeadlineItems(registry, data?.metrics, USERS_PAGE_HEADLINES, {
    comparisonLabel,
    onSelect: (metricId) => setDetailMetricId(metricId),
  })

  const trendDefinition = registry?.[trendMetric] || null
  const trendEnvelope = data?.metrics?.[trendMetric] || null
  const trendPoints = buildTrendPoints(data?.series?.[trendMetric], trendDefinition)
  const trendDelta = buildDeltaView(trendDefinition, trendEnvelope, comparisonLabel)

  const funnel = data?.funnel || { configured: false }
  const funnelSteps = buildFunnelSteps(funnel)

  return (
    <WorkspaceLayout>
      <div className="space-y-6">
        <AppStudioHero
          appId={appId}
          summary={{ app: data?.app }}
          dataMode={dataMode}
          title="Users"
          subtitle="Who uses this app, whether they convert, and where they drop out."
        >
          <div className="flex flex-wrap items-center justify-between gap-3 px-1">
            <SegmentedControl options={ANALYTICS_PERIODS} value={period} onChange={setPeriod} />
            {dataMode === 'demo' ? <StatusPill tone="warning">Demo dataset</StatusPill> : null}
          </div>
        </AppStudioHero>

        {usersAvailability === 'none' ? (
          <StudioInlineEmptyState
            title="No user data recorded yet"
            description="Active users appear automatically once the app serves pages and actions. Totals, paying users, and conversion appear once the app records those KPI snapshots. Nothing is estimated: this page only shows recorded values."
          />
        ) : (
          <>
            <MetricSummaryStrip items={headlineItems} />

            <UsageTrendPanel
              title="User trend"
              metricLabel={trendDefinition?.label || trendMetric}
              metricValue={
                trendEnvelope?.available
                  ? formatMetricValue(trendDefinition, trendEnvelope.value, '—')
                  : 'Pending'
              }
              metricDetail={
                trendDelta
                  ? `${trendDelta.rising === false ? '↓' : '↑'} ${trendDelta.label} ${comparisonLabel}`
                  : null
              }
              data={trendPoints}
              emptyLabel="Trend appears once daily activity is recorded"
              formatPointValue={(value) => formatMetricValue(trendDefinition, value, '—')}
              baseline="auto"
              action={
                <SegmentedControl
                  options={USERS_TREND_METRICS}
                  value={trendMetric}
                  onChange={setTrendMetric}
                  className="border-b-0"
                />
              }
            />

            <Panel
              title={funnel.configured && funnel.label ? funnel.label : 'Lifecycle funnel'}
              subtitle={
                funnel.configured
                  ? 'Distinct users reaching each stage this period, with stage-to-stage conversion.'
                  : null
              }
            >
              {funnel.configured && funnelSteps.length > 0 && funnel.available !== false ? (
                <StageFunnel steps={funnelSteps} />
              ) : funnel.configured ? (
                <StudioInlineEmptyState
                  title="No funnel activity yet"
                  description="This app declares a funnel, but its stage events have not been recorded in this period."
                />
              ) : (
                <StudioInlineEmptyState
                  title="No funnel configured"
                  description="This app has not declared lifecycle stages. Add funnels to the app's config/metrics.yaml — stages are app-specific, not a fixed SaaS template."
                />
              )}
            </Panel>

            <div className="px-1">
              <LinkButton to={`/apps/${encodeURIComponent(appId)}/access`} variant="ghost" size="sm">
                Manage accounts and access →
              </LinkButton>
            </div>
          </>
        )}
      </div>

      <MetricDetailPanel
        open={Boolean(detailMetricId)}
        appId={appId}
        appName={appName}
        metricId={detailMetricId}
        period={period}
        registry={registry}
        onClose={() => setDetailMetricId(null)}
      />
    </WorkspaceLayout>
  )
}
