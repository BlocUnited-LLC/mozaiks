import { Alert } from '@mozaiks/chat-ui/ui'
import { ChevronDown } from 'lucide-react'

import { StatusPill } from '../../ui/components/StudioShared.jsx'
import { formatCompactNumber, formatDateTimeLabel } from './AppStudioChrome.jsx'

function statusTone(status) {
  if (status === 'ready') return 'success'
  if (status === 'fallback_prices') return 'warning'
  if (status === 'unpriced_models') return 'destructive'
  return 'warning'
}

function statusLabel(status) {
  if (status === 'ready') return 'Ready'
  if (status === 'fallback_prices') return 'Fallback prices'
  if (status === 'unpriced_models') return 'Unpriced models'
  if (status === 'catalog_unavailable') return 'Catalog unavailable'
  return 'Not configured'
}

function formatPercent(value) {
  if (value == null || Number.isNaN(Number(value))) return 'Pending'
  return `${Number(value).toFixed(Number(value) % 1 === 0 ? 0 : 1)}%`
}

function latestCatalog(health) {
  const catalogs = Array.isArray(health?.catalogs) ? health.catalogs : []
  return catalogs.find((catalog) => catalog?.status === 'ready') || catalogs[0] || null
}

function summaryText(health, unpricedModels, fallbackModels) {
  if (health.status === 'ready') return 'Provider pricing is available for measured usage.'
  if (unpricedModels.includes('unknown')) return 'Some usage is missing model names, so cost estimates may be incomplete.'
  if (unpricedModels.length > 0) return `${unpricedModels.length} model${unpricedModels.length === 1 ? '' : 's'} need provider pricing.`
  if (fallbackModels.length > 0) return 'Some calls used fallback list prices.'
  return 'Provider pricing needs review.'
}

export default function PricingHealthPanel({ usage, showWhenReady = false }) {
  const health = usage?.pricing_health
  if (!health) return null

  const catalog = latestCatalog(health)
  const unpricedModels = Array.isArray(health.unpriced_models) ? health.unpriced_models : []
  const fallbackModels = Array.isArray(health.default_table_models) ? health.default_table_models : []
  const sourceCounts = health.cost_source_counts || {}
  const hasIssue = health.status && health.status !== 'ready'

  if (!showWhenReady && !hasIssue && fallbackModels.length === 0 && unpricedModels.length === 0) {
    return null
  }

  return (
    <details className="group rounded-[1.15rem] border border-border/42 bg-background/24 shadow-sm shadow-black/5">
      <summary className="flex cursor-pointer list-none items-center justify-between gap-4 px-4 py-3 marker:hidden sm:px-5">
        <div className="min-w-0">
          <div className="text-sm font-semibold text-foreground">Cost estimate status</div>
          <div className="mt-1 text-sm leading-6 text-muted-foreground/86">
            {summaryText(health, unpricedModels, fallbackModels)}
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-3">
          <StatusPill tone={statusTone(health.status)}>{statusLabel(health.status)}</StatusPill>
          <ChevronDown className="h-4 w-4 text-muted-foreground transition-transform group-open:rotate-180" aria-hidden="true" />
        </div>
      </summary>

      <div className="border-t border-border/32 px-4 pb-4 pt-4 sm:px-5">
      <div className="grid gap-3 sm:grid-cols-4">
        <div className="rounded-lg border border-border/42 bg-card/30 px-4 py-3">
          <div className="text-[12px] font-medium text-muted-foreground/82">Coverage</div>
          <div className="mt-2 text-xl font-semibold text-foreground">{formatPercent(health.coverage_percent)}</div>
        </div>
        <div className="rounded-lg border border-border/42 bg-card/30 px-4 py-3">
          <div className="text-[12px] font-medium text-muted-foreground/82">Unpriced models</div>
          <div className="mt-2 text-xl font-semibold text-foreground">{formatCompactNumber(health.unpriced_model_count, '0')}</div>
        </div>
        <div className="rounded-lg border border-border/42 bg-card/30 px-4 py-3">
          <div className="text-[12px] font-medium text-muted-foreground/82">Provider price entries</div>
          <div className="mt-2 text-xl font-semibold text-foreground">{formatCompactNumber(health.catalog_model_count, '0')}</div>
        </div>
        <div className="rounded-lg border border-border/42 bg-card/30 px-4 py-3">
          <div className="text-[12px] font-medium text-muted-foreground/82">Catalog updated</div>
          <div className="mt-2 text-sm font-semibold text-foreground">{formatDateTimeLabel(health.catalog_updated_at, 'Not available')}</div>
        </div>
      </div>

      {unpricedModels.length > 0 ? (
        <Alert
          variant="warning"
          className="mt-4"
          message={`Missing provider prices for ${unpricedModels.slice(0, 4).join(', ')}${unpricedModels.length > 4 ? ` and ${unpricedModels.length - 4} more` : ''}.`}
        />
      ) : null}

      {fallbackModels.length > 0 ? (
        <Alert
          variant="info"
          className="mt-4"
          message={`${health.default_table_event_count} call${Number(health.default_table_event_count || 0) === 1 ? '' : 's'} used the local fallback table.`}
        />
      ) : null}

      <div className="mt-4 flex flex-wrap gap-2 text-xs text-muted-foreground">
        {Object.entries(sourceCounts).map(([source, count]) => (
          <span key={source} className="rounded-md border border-border/42 bg-background/40 px-2 py-1">
            {source}: {formatCompactNumber(count, '0')}
          </span>
        ))}
        {catalog?.source_name ? (
          <span className="rounded-md border border-border/42 bg-background/40 px-2 py-1">
            Source: {catalog.source_name}
          </span>
        ) : null}
      </div>
      </div>
    </details>
  )
}
