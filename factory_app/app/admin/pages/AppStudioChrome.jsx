import { PageHeader, StatusPill, SummaryStrip } from '@mozaiks/chat-ui/ui'
import { LinkButton } from '../../ui/components/StudioShared.jsx'

export function formatCompactNumber(value, fallback = 'Pending') {
  if (value == null || Number.isNaN(Number(value))) return fallback
  return new Intl.NumberFormat(undefined, {
    notation: Math.abs(Number(value)) >= 1000 ? 'compact' : 'standard',
    maximumFractionDigits: 1,
  }).format(Number(value))
}

export function formatCurrencyValue(value, fallback = 'Pending') {
  if (value == null || Number.isNaN(Number(value))) return fallback
  const num = Number(value)
  const abs = Math.abs(num)
  // Use 4 decimal places for very small amounts (fractions of a cent), 2 otherwise
  const fractionDigits = abs > 0 && abs < 0.01 ? 4 : 2
  return new Intl.NumberFormat(undefined, {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: fractionDigits,
    maximumFractionDigits: fractionDigits,
  }).format(num)
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

function getAppName(summary, appId) {
  const app = summary?.app || {}
  return app.name || app.app_name || app.app_id || app.id || appId || 'App'
}

function firstText(...values) {
  for (const value of values) {
    if (typeof value !== 'string') continue
    const text = value.trim()
    if (text) return text
  }
  return null
}

function getAppDescription(summary) {
  const app = summary?.app || {}
  const identity = summary?.identity || {}
  const theme = summary?.theme || {}
  return firstText(
    app.description,
    app.product_summary,
    app.concept_overview,
    app.summary,
    identity.description,
    theme.description,
  )
}

function getAppTagline(summary) {
  const app = summary?.app || {}
  const identity = summary?.identity || {}
  const theme = summary?.theme || {}
  return firstText(
    app.tagline,
    app.value_proposition,
    identity.tagline,
    theme.tagline,
  )
}

function getSafeImageSrc(...values) {
  const src = values.find((value) => typeof value === 'string' && value.trim())
  if (!src) return null
  const trimmed = src.trim()
  if (/^(https?:|data:image\/|blob:|\/)/i.test(trimmed)) return trimmed
  return null
}

function getAppLogoSrc(summary) {
  const app = summary?.app || {}
  const theme = summary?.theme || {}
  const brand = summary?.brand || {}
  const themeBranding = theme?.branding || {}
  const logo = app.logo || theme.logo || brand.logo || null

  return getSafeImageSrc(
    app.logo_url,
    app.logoUrl,
    app.icon_url,
    theme.logo_url,
    theme.logoUrl,
    theme.logo_src,
    themeBranding.logo_url,
    brand.logo_url,
    typeof logo === 'string' ? logo : logo?.src,
  )
}

function getAppBannerSrc(summary) {
  const app = summary?.app || {}
  const theme = summary?.theme || {}
  const brand = summary?.brand || {}
  const themeBranding = theme?.branding || {}
  const banner = app.banner || app.cover || theme.banner || brand.banner || null

  return getSafeImageSrc(
    app.banner_url,
    app.bannerUrl,
    app.cover_url,
    app.coverUrl,
    app.image_url,
    app.imageUrl,
    theme.banner_url,
    theme.bannerUrl,
    theme.cover_url,
    themeBranding.banner_url,
    brand.banner_url,
    brand.cover_url,
    typeof banner === 'string' ? banner : banner?.src,
  )
}

function getAppInitials(name, appId) {
  const words = String(name || appId || 'App')
    .replace(/[_-]+/g, ' ')
    .split(/\s+/)
    .map((part) => part.replace(/[^a-z0-9]/gi, ''))
    .filter(Boolean)

  if (words.length >= 2) return `${words[0][0]}${words[1][0]}`.toUpperCase()
  const compact = words[0] || 'APP'
  return compact.slice(0, 2).toUpperCase()
}

function AppIdentityMark({ summary, appId, className = '', imageClassName = '', initialsClassName = '' }) {
  const appName = getAppName(summary, appId)
  const logoSrc = getAppLogoSrc(summary)

  if (logoSrc) {
    return (
      <span className={`flex h-11 w-11 shrink-0 items-center justify-center overflow-hidden rounded-xl border border-border/50 bg-card/45 ${className}`}>
        <img src={logoSrc} alt={`${appName} logo`} className={`h-full w-full object-cover ${imageClassName}`} />
      </span>
    )
  }

  return (
    <span className={`flex h-11 w-11 shrink-0 items-center justify-center rounded-xl border border-primary/28 bg-primary/10 text-sm font-semibold text-primary ${className} ${initialsClassName}`}>
      {getAppInitials(appName, appId)}
    </span>
  )
}

function AppIdentity({ appId, summary, dataMode }) {
  const app = summary?.app || {}
  if (!summary && !appId) return null

  const appName = getAppName(summary, appId)
  const shortId = app.app_id || app.id || appId
  const lifecycleLabel = app.lifecycle_label || app.lifecycle_state || app.status || null

  return (
    <div className="flex flex-col gap-3 px-1 sm:flex-row sm:items-center sm:justify-between">
      <div className="flex min-w-0 items-center gap-3">
        <AppIdentityMark summary={summary} appId={appId} />
        <div className="min-w-0">
          <div className="truncate text-sm font-semibold text-foreground">{appName}</div>
          <div className="mt-0.5 truncate text-xs text-muted-foreground">{shortId || 'App workspace'}</div>
        </div>
        {lifecycleLabel ? (
          <StatusPill tone="default" className="hidden shrink-0 sm:inline-flex">
            {String(lifecycleLabel).replace(/_/g, ' ')}
          </StatusPill>
        ) : null}
      </div>

      {dataMode === 'demo' ? (
        <StatusPill tone="warning" className="w-fit shrink-0">Demo data</StatusPill>
      ) : null}
    </div>
  )
}

function AppNextStep({ nextStep = null, action = null }) {
  if (!nextStep && !action) return null

  return (
    <div className="flex min-w-0 flex-col gap-3 border-t border-border/35 pt-4 sm:min-w-[17rem] sm:max-w-xs sm:border-l sm:border-t-0 sm:pl-5 sm:pt-0">
      <div>
        <div className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground/70">Next step</div>
        {nextStep ? (
          <p className="mt-1 text-sm leading-6 text-foreground">{nextStep}</p>
        ) : (
          <p className="mt-1 text-sm leading-6 text-muted-foreground">No operator action is needed right now.</p>
        )}
      </div>
      {action?.href && action?.label ? (
        <LinkButton to={action.href} variant="secondary" size="sm" className="w-fit font-semibold">
          {action.label}
        </LinkButton>
      ) : null}
    </div>
  )
}

function AppDashboardBanner({ appId, summary, dataMode, nextStep = null, nextStepAction = null }) {
  const app = summary?.app || {}
  const appName = getAppName(summary, appId)
  const description = getAppDescription(summary)
  const tagline = getAppTagline(summary)
  const bannerSrc = getAppBannerSrc(summary)
  const lifecycleLabel = app.lifecycle_label || app.lifecycle_state || app.status || null
  const identityPending = !description

  return (
    <section className="relative min-h-[11rem] overflow-hidden rounded-lg border border-border/45 bg-card/32 shadow-sm shadow-black/5">
      {bannerSrc ? (
        <img
          src={bannerSrc}
          alt={`${appName} banner`}
          className="absolute inset-0 h-full w-full object-cover"
        />
      ) : null}
      {bannerSrc ? <div className="absolute inset-0 bg-background/62" aria-hidden="true" /> : null}

      <div className="relative flex min-h-[11rem] flex-col justify-end gap-4 p-5 sm:p-6">
        <div className="flex flex-col gap-5 sm:flex-row sm:items-end sm:justify-between">
          <div className="flex min-w-0 items-end gap-4">
            <AppIdentityMark
              summary={summary}
              appId={appId}
              className="h-16 w-16 rounded-2xl border-border/60 bg-background/72 text-xl shadow-sm shadow-black/10"
            />
            <div className="min-w-0 pb-0.5">
              <div className="truncate text-2xl font-semibold leading-none text-foreground sm:text-3xl">
                {appName}
              </div>
              {tagline ? (
                <div className="mt-1.5 text-sm font-medium leading-6 text-foreground/86">{tagline}</div>
              ) : null}
              <p className={`mt-1 max-w-2xl text-sm leading-6 ${identityPending ? 'text-muted-foreground/72' : 'text-muted-foreground/90'}`}>
                {description || 'App description will appear after the concept brief is captured.'}
              </p>
              <div className="mt-2 flex flex-wrap items-center gap-2">
                {lifecycleLabel ? (
                  <StatusPill tone="default" className="bg-background/70">
                    {String(lifecycleLabel).replace(/_/g, ' ')}
                  </StatusPill>
                ) : null}
                {dataMode === 'demo' ? <StatusPill tone="warning">Demo data</StatusPill> : null}
              </div>
            </div>
          </div>
          <AppNextStep nextStep={nextStep} action={nextStepAction} />
        </div>
      </div>
    </section>
  )
}

export function AppStudioHero({
  appId = null,
  summary = null,
  dataMode = null,
  showBanner = false,
  nextStep = null,
  nextStepAction = null,
  title,
  subtitle,
  actions = null,
  onAction = null,
  summaryItems = [],
  children,
}) {
  return (
    <div className="space-y-4">
      {showBanner ? (
        <AppDashboardBanner
          appId={appId}
          summary={summary}
          dataMode={dataMode}
          nextStep={nextStep}
          nextStepAction={nextStepAction}
        />
      ) : (
        <AppIdentity appId={appId} summary={summary} dataMode={dataMode} />
      )}
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
