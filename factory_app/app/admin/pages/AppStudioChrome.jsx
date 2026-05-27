import { PageHeader, SummaryStrip } from '@mozaiks/chat-ui/ui'

export function formatCompactNumber(value, fallback = 'Pending') {
  if (value == null || Number.isNaN(Number(value))) return fallback
  return new Intl.NumberFormat(undefined, {
    notation: Math.abs(Number(value)) >= 1000 ? 'compact' : 'standard',
    maximumFractionDigits: 1,
  }).format(Number(value))
}

export function formatCurrencyValue(value, fallback = 'Pending') {
  if (value == null || Number.isNaN(Number(value))) return fallback
  return new Intl.NumberFormat(undefined, {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: Math.abs(Number(value)) >= 1000 ? 0 : 2,
  }).format(Number(value))
}

export function formatDateTimeLabel(value, fallback = 'Not available') {
  if (!value) return fallback
  try {
    return new Intl.DateTimeFormat(undefined, {
      dateStyle: 'medium',
      timeStyle: 'short',
    }).format(new Date(value))
  } catch {
    return fallback
  }
}

export function AppStudioHero({
  title,
  subtitle,
  actions = null,
  onAction = null,
  summaryItems = [],
  children,
}) {
  return (
    <div className="space-y-4">
      <PageHeader title={title} subtitle={subtitle} actions={actions} onAction={onAction} className="px-1" />

      {summaryItems.length > 0 ? <SummaryStrip items={summaryItems} /> : null}
      {children ? <div>{children}</div> : null}
    </div>
  )
}

export function WorkspaceStudioHero({
  title,
  subtitle,
  actions = null,
  onAction = null,
  summaryItems = [],
  children,
}) {
  return (
    <div className="space-y-4">
      <PageHeader
        title={title}
        subtitle={subtitle}
        actions={actions}
        onAction={onAction}
        className="px-1"
      />

      {summaryItems.length > 0 ? <SummaryStrip items={summaryItems} /> : null}
      {children ? <div>{children}</div> : null}
    </div>
  )
}

export default AppStudioHero
