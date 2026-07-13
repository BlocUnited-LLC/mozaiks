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

// size: 'sm' | 'md' | 'lg'
function AppIdentityMark({ summary, appId, size = 'md', className = '', imageClassName = '', initialsClassName = '' }) {
  const sizeClasses = {
    sm: 'h-8 w-8 text-xs rounded-lg',
    md: 'h-11 w-11 text-sm rounded-xl',
    lg: 'h-20 w-20 text-2xl rounded-2xl',
  }
  const sz = sizeClasses[size] || sizeClasses.md
  const appName = getAppName(summary, appId)
  const logoSrc = getAppLogoSrc(summary)

  if (logoSrc) {
    return (
      <span className={`flex shrink-0 items-center justify-center overflow-hidden border border-border/50 bg-card/45 ${sz} ${className}`}>
        <img src={logoSrc} alt={`${appName} logo`} className={`h-full w-full object-cover ${imageClassName}`} />
      </span>
    )
  }

  return (
    <span className={`flex shrink-0 items-center justify-center border border-primary/28 bg-primary/10 font-semibold text-primary ${sz} ${className} ${initialsClassName}`}>
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
        <AppIdentityMark summary={summary} appId={appId} size="md" />
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
    <div className="shrink-0 rounded-xl border border-border/30 bg-background/65 p-4 backdrop-blur-md sm:min-w-[15rem] sm:max-w-xs">
      <div className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground/60">
        Next step
      </div>
      {nextStep ? (
        <p className="mt-1.5 text-sm leading-relaxed text-foreground">{nextStep}</p>
      ) : (
        <p className="mt-1.5 text-sm leading-relaxed text-muted-foreground">
          No operator action is needed right now.
        </p>
      )}
      {action?.href && action?.label ? (
        <LinkButton
          to={action.href}
          variant="secondary"
          size="sm"
          className="mt-3 w-full justify-center font-semibold"
        >
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

  return (
    <section className="relative min-h-[17rem] overflow-hidden rounded-2xl border border-border/40 bg-card/50 shadow-md shadow-black/8">
      {/* Banner image — full bleed */}
      {bannerSrc ? (
        <img
          src={bannerSrc}
          alt=""
          aria-hidden="true"
          className="absolute inset-0 h-full w-full object-cover"
        />
      ) : (
        /* Subtle ambient gradient when no image */
        <div
          className="absolute inset-0 bg-gradient-to-br from-primary/8 via-transparent to-secondary/8"
          aria-hidden="true"
        />
      )}

      {/* Gradient scrim — strong at bottom, fades to transparent at top */}
      <div
        className="absolute inset-0 bg-gradient-to-t from-background/96 via-background/55 to-background/8"
        aria-hidden="true"
      />

      {/* Status pills — top-right overlay */}
      <div className="absolute right-4 top-4 flex items-center gap-2">
        {lifecycleLabel ? (
          <StatusPill tone="default" className="bg-background/65 backdrop-blur-sm">
            {String(lifecycleLabel).replace(/_/g, ' ')}
          </StatusPill>
        ) : null}
        {dataMode === 'demo' ? (
          <StatusPill tone="warning" className="backdrop-blur-sm">Demo data</StatusPill>
        ) : null}
      </div>

      {/* Content — pinned to bottom */}
      <div className="relative flex min-h-[17rem] flex-col justify-end gap-5 p-6 sm:p-8">
        <div className="flex flex-col gap-5 sm:flex-row sm:items-end sm:justify-between">

          {/* Identity block */}
          <div className="flex min-w-0 items-end gap-5">
            <AppIdentityMark
              summary={summary}
              appId={appId}
              size="lg"
              className="border-border/50 bg-background/80 shadow-lg shadow-black/15 backdrop-blur-sm"
            />
            <div className="min-w-0 pb-1">
              <h2 className="truncate text-3xl font-bold leading-tight tracking-tight text-foreground sm:text-4xl">
                {appName}
              </h2>
              {tagline ? (
                <p className="mt-1 text-sm font-medium text-foreground/75">{tagline}</p>
              ) : null}
              {description ? (
                <p className="mt-1.5 max-w-2xl text-sm leading-relaxed text-muted-foreground/85">
                  {description}
                </p>
              ) : (
                <p className="mt-1.5 text-sm text-muted-foreground/50">
                  App description will appear after the concept brief is captured.
                </p>
              )}
            </div>
          </div>

          {/* Next step frosted card */}
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
      ) : null}
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
