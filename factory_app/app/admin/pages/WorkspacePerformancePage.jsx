import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'

import {
  InsightList,
  MetricSummaryStrip,
  ResourceList,
  UsageTrendPanel,
} from '@mozaiks/chat-ui/ui'
import { WorkspaceLayout } from '@mozaiks/chat-ui/workspace'
import {
  ActionButton,
  SegmentedControl,
  StatusPill,
  StudioErrorState,
  StudioInlineEmptyState,
  StudioLoadingState,
  StudioSlideOver,
} from '../../ui/components/StudioShared.jsx'
import { WorkspaceStudioHero } from './AppStudioChrome.jsx'
import {
  ANALYTICS_PERIODS,
  DEFAULT_ANALYTICS_PERIOD,
  PORTFOLIO_OPTIONAL_COLUMNS,
  REVENUE_TREND_METRICS,
  USERS_TREND_METRICS,
  WORLD_REVENUE_HEADLINES,
  WORLD_USERS_HEADLINES,
  buildDeltaView,
  buildHeadlineItems,
  buildInsightItems,
  buildTrendPoints,
  formatMetricValue,
  resolvePortfolioColumns,
  sortPortfolioRows,
} from './analyticsModel.js'
import MetricDetailPanel from './MetricDetailPanel.jsx'
import { usePortfolioAnalytics } from './useStudioAnalytics.js'

const COLUMNS_STORAGE_KEY = 'mozaiks_performance_columns'

const TREND_DOMAINS = [
  { value: 'revenue', label: 'Revenue' },
  { value: 'users', label: 'Users' },
]

const REVENUE_SIDE_METRICS = ['arr', 'nrr', 'arppu']
const USERS_SIDE_METRICS = ['new_users', 'paid_conversion', 'retention']

function loadStoredColumns() {
  try {
    const raw = localStorage.getItem(COLUMNS_STORAGE_KEY)
    const parsed = raw ? JSON.parse(raw) : []
    return Array.isArray(parsed) ? parsed.filter((id) => PORTFOLIO_OPTIONAL_COLUMNS.includes(id)) : []
  } catch {
    return []
  }
}

function MetricCell({ definition, envelope, comparisonLabel }) {
  const delta = buildDeltaView(definition, envelope, comparisonLabel)
  return (
    <div className="tabular-nums">
      <span className={envelope?.available ? 'text-foreground' : 'text-muted-foreground/55'}>
        {envelope?.available ? formatMetricValue(definition, envelope.value, '—') : '—'}
      </span>
      {delta ? (
        <span
          className={`ml-2 text-[11px] font-medium ${
            delta.tone === 'positive' ? 'text-success' : delta.tone === 'negative' ? 'text-destructive' : 'text-muted-foreground/70'
          }`}
        >
          {delta.rising === false ? '↓' : delta.rising === true ? '↑' : ''}
          {delta.label}
          <span className="sr-only"> {delta.srLabel}</span>
        </span>
      ) : null}
    </div>
  )
}

function AppPerformanceMobileItem({ row, registry, comparisonLabel, onOpen }) {
  return (
    <article
      className="cursor-pointer rounded-[1.15rem] border border-border/45 bg-card/34 p-4 shadow-sm shadow-black/5 transition-colors hover:bg-card/50"
      onClick={() => onOpen(row)}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="truncate font-semibold text-foreground">{row.name}</div>
          {row.lifecycle_state ? (
            <div className="mt-1 text-xs capitalize text-muted-foreground">{String(row.lifecycle_state).replace(/_/g, ' ')}</div>
          ) : null}
        </div>
      </div>
      <div className="mt-4 grid grid-cols-2 gap-3 text-sm">
        {['mrr', 'mrr_growth', 'active_users', 'paid_conversion'].map((metricId) => (
          <div key={metricId}>
            <div className="text-[12px] text-muted-foreground">
              {registry?.[metricId]?.short_label || registry?.[metricId]?.label || metricId}
            </div>
            <div className="mt-1 font-medium">
              <MetricCell definition={registry?.[metricId]} envelope={row.metrics?.[metricId]} comparisonLabel={comparisonLabel} />
            </div>
          </div>
        ))}
      </div>
    </article>
  )
}

function ColumnPicker({ open, onClose, registry, selected, onChange }) {
  return (
    <StudioSlideOver
      open={open}
      onClose={onClose}
      title="Table columns"
      description="Choose which secondary metrics appear in the applications table. App, MRR, growth, users, and conversion always stay visible."
      maxWidthClass="max-w-md"
    >
      <div className="space-y-1">
        {PORTFOLIO_OPTIONAL_COLUMNS.map((metricId) => {
          const definition = registry?.[metricId]
          const checked = selected.includes(metricId)
          return (
            <label
              key={metricId}
              className="flex cursor-pointer items-center justify-between gap-3 rounded-lg px-3 py-2.5 transition-colors hover:bg-muted/40"
            >
              <span className="min-w-0">
                <span className="block text-sm font-medium text-foreground">{definition?.label || metricId}</span>
                {definition?.description ? (
                  <span className="mt-0.5 block truncate text-xs text-muted-foreground/75">{definition.description}</span>
                ) : null}
              </span>
              <input
                type="checkbox"
                checked={checked}
                onChange={() =>
                  onChange(checked ? selected.filter((id) => id !== metricId) : [...selected, metricId])
                }
                className="h-4 w-4 shrink-0 accent-primary"
              />
            </label>
          )
        })}
      </div>
    </StudioSlideOver>
  )
}

export default function WorkspacePerformancePage() {
  const navigate = useNavigate()
  const [period, setPeriod] = useState(DEFAULT_ANALYTICS_PERIOD)
  const { data, loading, error, dataMode } = usePortfolioAnalytics(period)
  const [trendDomain, setTrendDomain] = useState('revenue')
  const [trendMetric, setTrendMetric] = useState('mrr')
  const [detailTarget, setDetailTarget] = useState(null)
  const [portfolioDetail, setPortfolioDetail] = useState(null)
  const [columnPickerOpen, setColumnPickerOpen] = useState(false)
  const [optionalColumns, setOptionalColumns] = useState(loadStoredColumns)

  useEffect(() => {
    try {
      localStorage.setItem(COLUMNS_STORAGE_KEY, JSON.stringify(optionalColumns))
    } catch {
      /* storage unavailable — column choice just won't persist */
    }
  }, [optionalColumns])

  useEffect(() => {
    setTrendMetric(trendDomain === 'revenue' ? 'mrr' : 'active_users')
  }, [trendDomain])

  const registry = data?.registry || {}
  const comparisonLabel = data?.period?.comparison_label || 'vs previous period'
  const appRows = useMemo(() => sortPortfolioRows(data?.apps || []), [data])

  const openPortfolioMetric = (metricId) => {
    setPortfolioDetail({
      period: data?.period,
      definition: registry?.[metricId] || null,
      app: { app_id: null, name: 'Portfolio' },
      value: data?.portfolio?.[metricId] || null,
      series: data?.series?.[metricId] || [],
      drivers: (registry?.[metricId]?.related || [])
        .filter((id) => ['new_mrr', 'expansion_mrr', 'contraction_mrr', 'churned_mrr'].includes(id))
        .map((id) => data?.portfolio?.[id])
        .filter(Boolean),
      related: (registry?.[metricId]?.related || [])
        .map((id) => data?.portfolio?.[id])
        .filter(Boolean),
      benchmark: null,
      error: false,
    })
  }

  if (loading) return <StudioLoadingState label="Loading portfolio performance…" />
  if (error) return <StudioErrorState title="Performance Unavailable" message={error} />

  const trendMetricOptions = trendDomain === 'revenue' ? REVENUE_TREND_METRICS : USERS_TREND_METRICS
  const trendDefinition = registry?.[trendMetric] || null
  const trendEnvelope = data?.portfolio?.[trendMetric] || null
  const trendPoints = buildTrendPoints(data?.series?.[trendMetric], trendDefinition)
  const trendDelta = buildDeltaView(trendDefinition, trendEnvelope, comparisonLabel)
  const sideMetricIds = trendDomain === 'revenue' ? REVENUE_SIDE_METRICS : USERS_SIDE_METRICS
  const sideItems = sideMetricIds.map((metricId) => {
    const definition = registry?.[metricId]
    const envelope = data?.portfolio?.[metricId]
    return {
      id: metricId,
      label: definition?.label || metricId,
      value: envelope?.available ? formatMetricValue(definition, envelope.value, '—') : 'Pending',
      detail: envelope?.available ? null : 'No data yet',
    }
  })

  const revenueItems = buildHeadlineItems(registry, data?.portfolio, WORLD_REVENUE_HEADLINES, {
    comparisonLabel,
    onSelect: openPortfolioMetric,
  })
  const usersItems = buildHeadlineItems(registry, data?.portfolio, WORLD_USERS_HEADLINES, {
    comparisonLabel,
    onSelect: openPortfolioMetric,
  })

  const insightItems = buildInsightItems(data?.insights, {
    onSelect: (insight) => {
      if (!insight.app_id) return
      setDetailTarget({
        appId: insight.app_id,
        appName: insight.app_name,
        metricId: insight.metric_id || 'mrr',
      })
    },
  })

  const metricColumns = resolvePortfolioColumns(optionalColumns)
  const columns = [
    {
      id: 'app',
      header: 'App',
      width: '22%',
      render: (row) => (
        <div className="min-w-0">
          <div className="truncate font-semibold text-foreground">{row.name}</div>
          {row.lifecycle_state ? (
            <div className="mt-1 text-xs capitalize text-muted-foreground">
              {String(row.lifecycle_state).replace(/_/g, ' ')}
            </div>
          ) : null}
        </div>
      ),
    },
    ...metricColumns.map((metricId) => ({
      id: metricId,
      header: registry?.[metricId]?.short_label || registry?.[metricId]?.label || metricId,
      render: (row) => (
        <MetricCell
          definition={registry?.[metricId]}
          envelope={row.metrics?.[metricId]}
          comparisonLabel={comparisonLabel}
        />
      ),
    })),
  ]

  const openApp = (row) => {
    if (!row?.app_id) return
    navigate(`/apps/${encodeURIComponent(row.app_id)}/overview`)
  }

  const noApps = appRows.length === 0
  const nothingRecorded =
    !noApps && data?.availability?.revenue === 'none' && data?.availability?.users === 'none'

  return (
    <WorkspaceLayout>
      <div className="space-y-6">
        <WorkspaceStudioHero
          title="Performance"
          subtitle="Revenue and user performance across your portfolio, with the apps that need attention."
          actions={null}
          onAction={null}
        >
          <div className="flex flex-wrap items-center justify-between gap-3 px-1">
            <SegmentedControl options={ANALYTICS_PERIODS} value={period} onChange={setPeriod} />
            {dataMode === 'demo' ? <StatusPill tone="warning">Demo dataset</StatusPill> : null}
          </div>
        </WorkspaceStudioHero>

        {noApps ? (
          <StudioInlineEmptyState
            title="No apps to analyze yet"
            description="Create your first app to start tracking revenue and user performance here."
            action={{ label: 'Create app', to: '/apps/new' }}
          />
        ) : (
          <>
            {nothingRecorded ? (
              <StudioInlineEmptyState
                title="No performance data recorded yet"
                description="Metrics appear as your apps record activity and KPI snapshots (for example from a billing integration). Everything below fills in automatically — nothing to configure here."
              />
            ) : null}

            <div className="grid gap-4 xl:grid-cols-2">
              <MetricSummaryStrip eyebrow="Revenue" items={revenueItems} />
              <MetricSummaryStrip eyebrow="Users" items={usersItems} />
            </div>

            <UsageTrendPanel
              title="Portfolio trend"
              subtitle="One trend at a time — switch the domain and metric instead of scanning many charts."
              metricLabel={trendDefinition?.label || trendMetric}
              metricValue={
                trendEnvelope?.available
                  ? formatMetricValue(trendDefinition, trendEnvelope.value, '—')
                  : 'Pending'
              }
              metricDetail={trendDelta ? `${trendDelta.rising === false ? '↓' : '↑'} ${trendDelta.label} ${comparisonLabel}` : null}
              data={trendPoints}
              sideItems={sideItems}
              emptyLabel="Trend appears once daily data is recorded"
              formatPointValue={(value) => formatMetricValue(trendDefinition, value, '—')}
              action={
                <div className="flex flex-col items-end gap-2">
                  <SegmentedControl options={TREND_DOMAINS} value={trendDomain} onChange={setTrendDomain} />
                  <SegmentedControl options={trendMetricOptions} value={trendMetric} onChange={setTrendMetric} className="border-b-0" />
                </div>
              }
            />

            <section className="space-y-3">
              <h2 className="px-1 text-lg font-semibold text-foreground">Needs attention</h2>
              <InsightList
                items={insightItems}
                emptyLabel="Nothing needs attention right now — no significant movements this period."
              />
            </section>

            <section className="space-y-3">
              <div className="flex items-center justify-between gap-3 px-1">
                <h2 className="text-lg font-semibold text-foreground">Applications</h2>
                <ActionButton variant="outline" size="sm" onClick={() => setColumnPickerOpen(true)}>
                  Columns
                </ActionButton>
              </div>
              <ResourceList
                items={appRows}
                columns={columns}
                getItemId={(row) => row.app_id}
                onRowClick={openApp}
                renderMobileItem={(row) => (
                  <AppPerformanceMobileItem
                    row={row}
                    registry={registry}
                    comparisonLabel={comparisonLabel}
                    onOpen={openApp}
                  />
                )}
              />
            </section>
          </>
        )}
      </div>

      <ColumnPicker
        open={columnPickerOpen}
        onClose={() => setColumnPickerOpen(false)}
        registry={registry}
        selected={optionalColumns}
        onChange={setOptionalColumns}
      />

      <MetricDetailPanel
        open={Boolean(detailTarget)}
        appId={detailTarget?.appId}
        appName={detailTarget?.appName}
        metricId={detailTarget?.metricId}
        period={period}
        registry={registry}
        onClose={() => setDetailTarget(null)}
      />

      <MetricDetailPanel
        open={Boolean(portfolioDetail)}
        appId={null}
        appName="Portfolio"
        metricId={portfolioDetail?.definition?.metric_id}
        period={period}
        registry={registry}
        overrideDetail={portfolioDetail}
        onClose={() => setPortfolioDetail(null)}
      />
    </WorkspaceLayout>
  )
}
