import { Link } from 'react-router-dom'

import { PageHeader, SummaryStrip } from '@mozaiks/chat-ui/ui'
import { StatusPill, SurfaceCard } from '../../ui/components/ConsoleShared.jsx'
import {
  getAppDisplayDescription,
  getAppDisplayName,
  getAppLifecycleLabel,
  getAppStatusTone,
} from './appConsoleModel.js'

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

export function AppConsoleHero({
  appId,
  summary,
  dataMode = 'live',
  title,
  subtitle,
  currentSection,
  actions = null,
  summaryItems = [],
  children,
  accent = true,
}) {
  const app = summary?.app || {}
  const lifecycleState = app.lifecycle_state || 'draft'
  const lifecycleLabel = getAppLifecycleLabel(lifecycleState, app.lifecycle_label)
  const lifecycleTone = getAppStatusTone(lifecycleState)
  const appName = getAppDisplayName(app) || appId
  const appDescription = getAppDisplayDescription(app) || 'Open the details you need without leaving the app context.'

  return (
    <div className="space-y-4">
      <SurfaceCard
        title={appName}
        subtitle={appDescription}
        headerAction={actions}
        accent={accent}
      >
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <Link
            to="/apps"
            className="text-muted-foreground transition hover:text-foreground"
          >
            Apps
          </Link>
          <span className="text-muted-foreground/70">/</span>
          <span className="font-medium text-foreground/90">{appName}</span>
          <StatusPill tone={lifecycleTone}>{lifecycleLabel}</StatusPill>
          {dataMode === 'demo' ? <StatusPill tone="warning">Demo dataset</StatusPill> : null}
        </div>
      </SurfaceCard>

      <PageHeader title={title} subtitle={subtitle} className="px-1" />

      {summaryItems.length > 0 ? <SummaryStrip items={summaryItems} /> : null}
      {children ? <div>{children}</div> : null}
    </div>
  )
}

export function WorkspaceConsoleHero({
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

export default AppConsoleHero
