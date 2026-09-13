import { useMemo, useState } from 'react'
import { useParams } from 'react-router-dom'

import { MetricBridge, MetricSummaryStrip, UsageTrendPanel } from '@mozaiks/chat-ui/ui'
import { WorkspaceLayout } from '@mozaiks/chat-ui/workspace'
import {
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
  REVENUE_PAGE_HEADLINES,
  REVENUE_TREND_METRICS,
  buildBridgeModel,
  buildDeltaView,
  buildHeadlineItems,
  buildTrendPoints,
  formatMetricValue,
} from './analyticsModel.js'
import MetricDetailPanel from './MetricDetailPanel.jsx'
import { useAppAnalytics } from './useStudioAnalytics.js'

export default function AppRevenuePage() {
  const { appId = 'workspace-app' } = useParams()
  const [period, setPeriod] = useState(DEFAULT_ANALYTICS_PERIOD)
  const { data, loading, error, dataMode } = useAppAnalytics(appId, period)
  const [trendMetric, setTrendMetric] = useState('mrr')
  const [detailMetricId, setDetailMetricId] = useState(null)

  const registry = data?.registry || {}
  const comparisonLabel = data?.period?.comparison_label || 'vs previous period'
  const appName = data?.app?.name || appId

  const bridge = useMemo(
    () =>
      buildBridgeModel(data?.movement, registry, {
        onSelect: (metricId) => setDetailMetricId(metricId),
      }),
    [data, registry],
  )

  if (loading) return <StudioLoadingState label="Loading revenue analytics…" />
  if (error) return <StudioErrorState title="Revenue Analytics Unavailable" message={error} />

  const revenueAvailability = data?.availability?.revenue || 'none'
  const headlineItems = buildHeadlineItems(registry, data?.metrics, REVENUE_PAGE_HEADLINES, {
    comparisonLabel,
    onSelect: (metricId) => setDetailMetricId(metricId),
  })

  const trendDefinition = registry?.[trendMetric] || null
  const trendEnvelope = data?.metrics?.[trendMetric] || null
  const trendPoints = buildTrendPoints(data?.series?.[trendMetric], trendDefinition)
  const trendDelta = buildDeltaView(trendDefinition, trendEnvelope, comparisonLabel)

  return (
    <WorkspaceLayout>
      <div className="space-y-6">
        <AppStudioHero
          appId={appId}
          summary={{ app: data?.app }}
          dataMode={dataMode}
          title="Revenue"
          subtitle="What this app earns, how it is trending, and what is driving the change."
        >
          <div className="flex flex-wrap items-center justify-between gap-3 px-1">
            <SegmentedControl options={ANALYTICS_PERIODS} value={period} onChange={setPeriod} />
            {dataMode === 'demo' ? <StatusPill tone="warning">Demo dataset</StatusPill> : null}
          </div>
        </AppStudioHero>

        {revenueAvailability === 'none' ? (
          <StudioInlineEmptyState
            title="No revenue data recorded yet"
            description="Revenue metrics appear once this app records KPI snapshots — typically written by its billing integration (for example when subscriptions activate, upgrade, or cancel). Nothing is estimated: this page only shows recorded values."
          />
        ) : (
          <>
            <MetricSummaryStrip items={headlineItems} />

            <UsageTrendPanel
              title="Revenue trend"
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
              emptyLabel="Trend appears once daily revenue snapshots are recorded"
              formatPointValue={(value) => formatMetricValue(trendDefinition, value, '—')}
              action={
                <SegmentedControl
                  options={REVENUE_TREND_METRICS}
                  value={trendMetric}
                  onChange={setTrendMetric}
                  className="border-b-0"
                />
              }
            />

            <Panel
              title="Why MRR changed"
              subtitle={`Movement over ${String(data?.period?.label || period).toLowerCase()} — click a driver to inspect it.`}
            >
              {bridge ? (
                <MetricBridge {...bridge} />
              ) : (
                <StudioInlineEmptyState
                  title="Movement not available yet"
                  description="The MRR bridge appears once this app records new, expansion, contraction, and churned MRR for the period."
                />
              )}
            </Panel>
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
