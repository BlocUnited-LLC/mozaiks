import { useState } from 'react'
import { PageHeader, StatusPill, SummaryStrip } from '@mozaiks/chat-ui/ui'
import { LinkButton } from '../../ui/components/StudioShared.jsx'

// ─── Formatters ───────────────────────────────────────────────────────────────

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

// ─── Image helpers ────────────────────────────────────────────────────────────

function fileToDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = (e) => resolve(e.target.result)
    reader.onerror = reject
    reader.readAsDataURL(file)
  })
}

function appBrandingKey(appId, field) {
  return `mozaiks_app_${field}_${appId || 'workspace'}`
}

// ─── App metadata helpers ─────────────────────────────────────────────────────

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
    app.description, app.product_summary, app.concept_overview, app.summary,
    identity.description, theme.description,
  )
}

function getAppTagline(summary) {
  const app = summary?.app || {}
  const identity = summary?.identity || {}
  const theme = summary?.theme || {}
  return firstText(
    app.tagline, app.value_proposition, identity.tagline, theme.tagline,
  )
}

function getSafeImageSrc(...values) {
  const src = values.find((v) => typeof v === 'string' && v.trim())
  if (!src) return null
  const trimmed = src.trim()
  return /^(https?:|data:image\/|blob:|\/)/i.test(trimmed) ? trimmed : null
}

function getAppLogoSrc(summary) {
  const app = summary?.app || {}
  const theme = summary?.theme || {}
  const brand = summary?.brand || {}
  const themeBranding = theme?.branding || {}
  const logo = app.logo || theme.logo || brand.logo || null
  return getSafeImageSrc(
    app.logo_url, app.logoUrl, app.icon_url,
    theme.logo_url, theme.logoUrl, theme.logo_src,
    themeBranding.logo_url, brand.logo_url,
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
    app.banner_url, app.bannerUrl, app.cover_url, app.coverUrl,
    app.image_url, app.imageUrl,
    theme.banner_url, theme.bannerUrl, theme.cover_url,
    themeBranding.banner_url, brand.banner_url, brand.cover_url,
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
  return (words[0] || 'APP').slice(0, 2).toUpperCase()
}

// ─── Icons ────────────────────────────────────────────────────────────────────

function CameraIcon({ className = '' }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={className} aria-hidden="true">
      <path d="M23 19a2 2 0 01-2 2H3a2 2 0 01-2-2V8a2 2 0 012-2h4l2-3h6l2 3h4a2 2 0 012 2z" />
      <circle cx="12" cy="13" r="4" />
    </svg>
  )
}

// ─── Identity mark ────────────────────────────────────────────────────────────

// size: 'sm' | 'md' | 'lg'
function AppIdentityMark({ summary, appId, size = 'md', logoOverride = null, className = '' }) {
  const sizeClasses = {
    sm: 'h-8 w-8 text-xs rounded-lg',
    md: 'h-11 w-11 text-sm rounded-xl',
    lg: 'h-[5rem] w-[5rem] text-xl rounded-2xl',
  }
  const sz = sizeClasses[size] || sizeClasses.md
  const appName = getAppName(summary, appId)
  const logoSrc = logoOverride || getAppLogoSrc(summary)

  if (logoSrc) {
    return (
      <span className={`flex shrink-0 items-center justify-center overflow-hidden ${sz} ${className}`}>
        <img src={logoSrc} alt={`${appName} logo`} className="h-full w-full object-cover" />
      </span>
    )
  }

  return (
    <span className={`flex shrink-0 items-center justify-center font-bold text-primary ${sz} ${className}`}>
      {getAppInitials(appName, appId)}
    </span>
  )
}

// ─── Dashboard banner hero ────────────────────────────────────────────────────

function AppDashboardBanner({ appId, summary, dataMode }) {
  const [bannerPreview, setBannerPreview] = useState(() => {
    try { return localStorage.getItem(appBrandingKey(appId, 'banner')) || null } catch { return null }
  })
  const [logoPreview, setLogoPreview] = useState(() => {
    try { return localStorage.getItem(appBrandingKey(appId, 'logo')) || null } catch { return null }
  })

  const app = summary?.app || {}
  const appName = getAppName(summary, appId)
  const description = getAppDescription(summary)
  const tagline = getAppTagline(summary)
  const bannerSrc = bannerPreview || getAppBannerSrc(summary)
  const lifecycleLabel = app.lifecycle_label || app.lifecycle_state || app.status || null

  function handleBannerChange(e) {
    const file = e.target.files?.[0]
    if (!file) return
    fileToDataUrl(file).then((dataUrl) => {
      setBannerPreview(dataUrl)
      try { localStorage.setItem(appBrandingKey(appId, 'banner'), dataUrl) } catch (_) {}
    })
    e.target.value = ''
  }

  function handleLogoChange(e) {
    const file = e.target.files?.[0]
    if (!file) return
    fileToDataUrl(file).then((dataUrl) => {
      setLogoPreview(dataUrl)
      try { localStorage.setItem(appBrandingKey(appId, 'logo'), dataUrl) } catch (_) {}
    })
    e.target.value = ''
  }

  return (
    <div className="overflow-hidden rounded-2xl border border-border/50 bg-card shadow-md shadow-black/8">

      {/* ── Banner strip ──────────────────────────────────────────────────── */}
      <div className="relative h-44 sm:h-52">

        {bannerSrc ? (
          <img
            src={bannerSrc}
            alt=""
            aria-hidden="true"
            className="h-full w-full object-cover"
          />
        ) : (
          /* No image: bold gradient so the strip looks intentional */
          <>
            <div className="absolute inset-0 bg-gradient-to-br from-primary/70 via-primary/30 to-secondary/60" />
            <div className="absolute inset-0 bg-[radial-gradient(ellipse_80%_80%_at_0%_0%,rgba(255,255,255,0.12),transparent)]" />
            <div className="absolute inset-0 bg-[radial-gradient(ellipse_50%_50%_at_100%_100%,rgba(255,255,255,0.06),transparent)]" />
          </>
        )}

        {/* Change cover — label wraps input, always visible bottom-right */}
        <label className="absolute bottom-3 right-3 flex cursor-pointer items-center gap-1.5 rounded-lg border border-white/25 bg-black/45 px-3 py-1.5 text-xs font-medium text-white backdrop-blur-sm transition-colors hover:bg-black/65 hover:text-white">
          <CameraIcon className="h-3.5 w-3.5 shrink-0" />
          {bannerSrc ? 'Change cover' : 'Add cover'}
          <input
            type="file"
            accept="image/*"
            className="sr-only"
            onChange={handleBannerChange}
          />
        </label>
      </div>

      {/* ── Identity strip ────────────────────────────────────────────────── */}
      <div className="px-5 pb-5 sm:px-6 sm:pb-6">

        {/* Logo row — negative margin pulls it up over the banner edge */}
        <div className="-mt-7 mb-4 flex items-end justify-between">

          {/* Logo with inline camera button */}
          <div className="relative shrink-0">
            <AppIdentityMark
              summary={summary}
              appId={appId}
              size="lg"
              logoOverride={logoPreview}
              className="border-4 border-card bg-card shadow-lg shadow-black/20"
            />
            {/* Small camera badge — bottom-right of logo, always visible */}
            <label
              className="absolute -bottom-1 -right-1 flex h-7 w-7 cursor-pointer items-center justify-center rounded-full border-2 border-card bg-muted shadow-sm transition-colors hover:bg-accent"
              title="Change logo"
            >
              <CameraIcon className="h-3.5 w-3.5 text-foreground/70" />
              <input
                type="file"
                accept="image/*"
                className="sr-only"
                onChange={handleLogoChange}
              />
            </label>
          </div>

          {/* Status pills — right side of logo row */}
          <div className="flex items-center gap-2 pb-0.5">
            {lifecycleLabel ? (
              <StatusPill tone="default">
                {String(lifecycleLabel).replace(/_/g, ' ')}
              </StatusPill>
            ) : null}
            {dataMode === 'demo' ? (
              <StatusPill tone="warning">Demo data</StatusPill>
            ) : null}
          </div>
        </div>

        {/* App name + tagline + description */}
        <div className="space-y-1">
          <h2 className="text-2xl font-bold leading-tight tracking-tight text-foreground">
            {appName}
          </h2>
          {tagline ? (
            <p className="text-sm font-medium text-foreground/65">{tagline}</p>
          ) : null}
          {description ? (
            <p className="max-w-2xl text-sm leading-relaxed text-muted-foreground/80">
              {description}
            </p>
          ) : (
            <p className="text-sm italic text-muted-foreground/40">
              App description appears after the concept brief is captured.
            </p>
          )}
        </div>
      </div>
    </div>
  )
}

// ─── AppStudioHero ────────────────────────────────────────────────────────────

export function AppStudioHero({
  appId = null,
  summary = null,
  dataMode = null,
  showBanner = false,
  title,
  subtitle,
  actions = null,
  onAction = null,
  summaryItems = [],
  // compat — handled at page level now
  nextStep: _nextStep,
  nextStepAction: _nextStepAction,
  children,
}) {
  return (
    <div className="space-y-4">
      {showBanner ? (
        <AppDashboardBanner appId={appId} summary={summary} dataMode={dataMode} />
      ) : null}
      <PageHeader title={title} subtitle={subtitle} actions={actions} onAction={onAction} className="px-1" />
      {summaryItems.length > 0 ? <SummaryStrip items={summaryItems} /> : null}
      {children ? <div>{children}</div> : null}
    </div>
  )
}

// ─── WorkspaceStudioHero ──────────────────────────────────────────────────────

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
      <PageHeader title={title} subtitle={subtitle} actions={actions} onAction={onAction} className="px-1" />
      {summaryItems.length > 0 ? <SummaryStrip items={summaryItems} /> : null}
      {children ? <div>{children}</div> : null}
    </div>
  )
}

export default AppStudioHero
