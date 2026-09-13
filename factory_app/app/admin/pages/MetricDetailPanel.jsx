import { useEffect, useState } from 'react'

import { MetricSummaryStrip } from '@mozaiks/chat-ui/ui'
import {
  StatusPill,
  StudioInlineEmptyState,
  StudioLoadingState,
  StudioSlideOver,
} from '../../ui/components/StudioShared.jsx'
import {
  buildDeltaView,
  buildTrendPoints,
  formatMetricValue,
} from './analyticsModel.js'
import { fetchMetricDetail } from './useStudioAnalytics.js'

function MiniTrend({ points, label }) {
  if (!points.length) return null
  const maxValue = Math.max(...points.map((point) => Math.abs(point.value)), 0)
  return (
    <div>
      <div className="text-[12px] font-medium text-muted-foreground/82">{label}</div>
      <div
        role="img"
        aria-label={`${label} trend, ${points.length} days`}
        className="mt-2 flex h-20 items-end gap-1"
      >
        {points.map((point) => (
          <div
            key={point.id}
            className="min-w-0 flex-1 rounded-t-sm bg-primary/55"
            style={{
              height: maxValue > 0 ? `${Math.max(4, (Math.abs(point.value) / maxValue) * 100)}%` : '0.25rem',
            }}
            title={`${point.detail}: ${point.extras?.[1]?.value ?? point.value}`}
          />
        ))}
      </div>
      <div className="mt-1 flex justify-between text-[11px] text-muted-foreground/70">
        <span>{points[0]?.label}</span>
        <span>{points[points.length - 1]?.label}</span>
      </div>
    </div>
  )
}

function ValueBlock({ definition, value, comparisonLabel }) {
  const delta = buildDeltaView(definition, value, comparisonLabel)
  return (
    <div>
      <div className="text-[12px] font-medium text-muted-foreground/82">Current value</div>
      <div className="mt-1.5 flex flex-wrap items-end gap-x-3 gap-y-1">
        <div className="text-3xl font-semibold leading-none tabular-nums text-foreground">
          {formatMetricValue(definition, value?.value, 'Pending')}
        </div>
        {delta ? (
          <div className="pb-0.5 text-sm font-medium tabular-nums text-muted-foreground">
            <span className={delta.tone === 'positive' ? 'text-success' : delta.tone === 'negative' ? 'text-destructive' : ''}>
              {delta.rising === false ? '↓' : delta.rising === true ? '↑' : '→'} {delta.label}
            </span>{' '}
            <span className="text-muted-foreground/70">{comparisonLabel}</span>
          </div>
        ) : null}
      </div>
      {value?.previous != null ? (
        <div className="mt-1.5 text-xs text-muted-foreground/72">
          Previous period: {formatMetricValue(definition, value.previous, '—')}
        </div>
      ) : null}
    </div>
  )
}

function RelatedStrip({ title, envelopes, registry, comparisonLabel, onNavigate }) {
  if (!envelopes?.length) return null
  const items = envelopes.map((envelope) => {
    const definition = registry?.[envelope.metric_id] || null
    return {
      id: envelope.metric_id,
      label: definition?.short_label || definition?.label || envelope.metric_id,
      value: formatMetricValue(definition, envelope.value, 'Pending'),
      delta: buildDeltaView(definition, envelope, comparisonLabel),
      pending: !envelope.available,
      onSelect: onNavigate ? () => onNavigate(envelope.metric_id) : undefined,
    }
  })
  return <MetricSummaryStrip eyebrow={title} items={items} />
}

/**
 * MetricDetailPanel — the universal metric drill-down drawer.
 *
 * Opens for any metric id on any owned app: definition, current value and
 * comparison, daily trend, drivers, related metrics, and the portfolio-median
 * benchmark. Selecting a driver or related metric re-targets the panel in
 * place, so a user can keep digging without losing context.
 */
export default function MetricDetailPanel({
  open,
  appId,
  appName = null,
  metricId,
  period,
  registry = null,
  overrideDetail = null,
  onClose,
}) {
  const [activeMetricId, setActiveMetricId] = useState(metricId)
  const [fetchedDetail, setFetchedDetail] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [dataMode, setDataMode] = useState('live')

  useEffect(() => {
    setActiveMetricId(metricId)
  }, [metricId, open])

  useEffect(() => {
    if (!open || overrideDetail || !appId || !activeMetricId) return undefined
    let cancelled = false
    setLoading(true)
    setError(null)
    fetchMetricDetail(appId, activeMetricId, period)
      .then(({ payload, dataMode: mode }) => {
        if (!cancelled) {
          setFetchedDetail(payload)
          setDataMode(mode)
        }
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Metric detail unavailable.')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [open, overrideDetail, appId, activeMetricId, period])

  const detail = overrideDetail || fetchedDetail

  const definition = detail?.definition || registry?.[activeMetricId] || null
  const comparisonLabel = detail?.period?.comparison_label || 'vs previous period'
  const points = buildTrendPoints(detail?.series || [], definition)

  return (
    <StudioSlideOver
      open={open}
      onClose={onClose}
      title={definition?.label || activeMetricId}
      description={definition?.description || null}
    >
      {loading ? (
        <StudioLoadingState label="Loading metric detail…" />
      ) : error ? (
        <StudioInlineEmptyState title="Metric detail unavailable" description={error} />
      ) : detail ? (
        <div className="space-y-6">
          <div className="flex flex-wrap items-center gap-2">
            {appName ? <StatusPill tone="default">{appName}</StatusPill> : null}
            <StatusPill tone="muted">{detail.period?.label || period}</StatusPill>
            {dataMode === 'demo' ? <StatusPill tone="warning">Demo dataset</StatusPill> : null}
          </div>

          {detail.value?.available === false ? (
            <StudioInlineEmptyState
              title="No data recorded yet"
              description={
                definition?.source === 'kpi_snapshot'
                  ? 'This metric appears once the app records KPI snapshots for it (for example from its billing integration).'
                  : definition?.source === 'usage_rollup'
                    ? 'This metric appears once the app has recorded user activity.'
                    : 'This metric appears once its underlying metrics have recorded data.'
              }
            />
          ) : (
            <ValueBlock definition={definition} value={detail.value} comparisonLabel={comparisonLabel} />
          )}

          {detail.benchmark && detail.benchmark.median != null ? (
            <div className="rounded-lg border border-border/45 bg-card/[0.18] px-4 py-3">
              <div className="flex items-baseline justify-between gap-3">
                <span className="text-sm text-muted-foreground">{detail.benchmark.label}</span>
                <span className="text-sm font-semibold tabular-nums text-foreground">
                  {formatMetricValue(definition, detail.benchmark.median, '—')}
                </span>
              </div>
              <div className="mt-1 text-[11px] text-muted-foreground/70">
                Benchmark across {detail.benchmark.sample_size} of your apps — context, not a target.
              </div>
            </div>
          ) : null}

          <MiniTrend points={points} label={`Daily ${definition?.short_label || definition?.label || 'trend'}`} />

          <RelatedStrip
            title="Drivers"
            envelopes={detail.drivers}
            registry={detail.registry || registry}
            comparisonLabel={comparisonLabel}
            onNavigate={overrideDetail ? null : setActiveMetricId}
          />
          <RelatedStrip
            title="Related metrics"
            envelopes={detail.related}
            registry={detail.registry || registry}
            comparisonLabel={comparisonLabel}
            onNavigate={overrideDetail ? null : setActiveMetricId}
          />
        </div>
      ) : (
        <StudioInlineEmptyState title="No metric selected" description="Choose a metric to inspect." />
      )}
    </StudioSlideOver>
  )
}
