/**
 * The app Project page, derived only from what the server records.
 *
 * Inputs are the app's saved build documents, exactly as
 * `GET /api/studio/build/history` returns them (`artifact_versions` rows),
 * and the app's registry entry from `GET /api/studio/apps`. Nothing here
 * invents state: a step is done only when a current version of its document
 * is saved, and the build button only targets an entry the server accepts
 * for this app (a recorded build conversation, the build review page, or a
 * build start bound to the app's `build_registry_id`).
 */

/** Build order of `extension_registry.json` `workflow_sequences[id=build]`. */
export const BUILD_STEPS = [
  { family: 'concept', label: 'Idea', next: 'describe the idea' },
  { family: 'theme_capture', label: 'Look & feel', next: 'choose the look and feel' },
  { family: 'design_docs', label: 'Design', next: 'design the app' },
  { family: 'subscription_contract', label: 'Plans & pricing', next: 'set plans and pricing' },
  { family: 'workflow_bundle', label: 'AI workflows', next: 'build the AI workflows' },
  { family: 'app_bundle', label: 'App', next: 'build the app' },
]

/**
 * Documents meant for people, in reading order. Internal families such as
 * `app_context_version`, `app_intelligence_snapshot`, `app_context_graph`,
 * `source_context_bundle` and `brownfield_registration` are left out.
 */
export const PROJECT_DOCUMENTS = [
  { family: 'concept', name: 'Idea & value' },
  { family: 'theme_capture', name: 'Look & feel' },
  { family: 'design_docs', name: 'Design' },
  { family: 'subscription_contract', name: 'Plans & pricing' },
  { family: 'workflow_bundle', name: 'AI workflows' },
  { family: 'app_bundle', name: 'App' },
  // Imported apps: what discovery found in the existing code.
  { family: 'application_inventory', name: 'What the app contains' },
  { family: 'integration_inventory', name: 'Connected services' },
  { family: 'ownership_boundary', name: 'What may be changed' },
  { family: 'risk_report', name: 'Risks' },
  { family: 'adoption_plan', name: 'Adoption plan' },
]

export const PROJECT_DOCUMENT_FAMILIES = PROJECT_DOCUMENTS.map((entry) => entry.family)

/** Rows of the first, unfiltered history page. */
export const HISTORY_PAGE_LIMIT = 100
/** Rows of a per-family follow-up page. */
export const FAMILY_PAGE_LIMIT = 25

/**
 * Families to read on their own after a first history page.
 *
 * A page shorter than its limit holds every record of the app, so nothing is
 * missing. A full page may have cut older records, including a family's
 * current version below newer draft or stale ones, so each wanted family
 * without a current version on the page gets a follow-up.
 */
export function familiesToFollowUp(rows, families, pageLimit = HISTORY_PAGE_LIMIT) {
  const list = Array.isArray(rows) ? rows : []
  if (list.length < pageLimit) return []
  const withCurrent = new Set(list.filter(isCurrent).map(familyOf))
  return families.filter((family) => !withCurrent.has(family))
}

const DOCUMENT_NAMES = Object.fromEntries(PROJECT_DOCUMENTS.map((entry) => [entry.family, entry.name]))

/** Lifecycle states in which the server resumes the app's recorded build conversation. */
const CONTINUE_STATES = new Set(['draft', 'building', 'review', 'configuring', 'needs_revision'])
/** Lifecycle states in which the server binds a review chat to the app's saved build. */
const REVIEW_STATES = new Set(['review', 'needs_revision', 'active'])

function text(value) {
  return typeof value === 'string' ? value.trim() : ''
}

function familyOf(row) {
  return text(row?.build_family) || text(row?.artifact_kind)
}

function versionNumber(row) {
  const number = Number(row?.version_number)
  return Number.isFinite(number) ? number : 0
}

function timeOf(row) {
  const value = Date.parse(row?.created_at || '')
  return Number.isFinite(value) ? value : 0
}

function newestFirst(left, right) {
  return versionNumber(right) - versionNumber(left) || timeOf(right) - timeOf(left)
}

export function documentName(family) {
  return DOCUMENT_NAMES[family] || null
}

/** Rows of known document families, one per id, newest version first per family. */
export function projectVersions(rows) {
  const seen = new Set()
  const versions = []
  for (const row of Array.isArray(rows) ? rows : []) {
    const family = familyOf(row)
    const id = text(row?.id) || text(row?._id)
    if (!DOCUMENT_NAMES[family] || !id || seen.has(id)) continue
    seen.add(id)
    versions.push({ ...row, id, build_family: family })
  }
  return versions.sort((left, right) => (
    PROJECT_DOCUMENT_FAMILIES.indexOf(left.build_family) - PROJECT_DOCUMENT_FAMILIES.indexOf(right.build_family)
    || newestFirst(left, right)
  ))
}

function versionsOf(versions, family) {
  return versions.filter((row) => row.build_family === family).sort(newestFirst)
}

function isCurrent(row) {
  return row?.lifecycle_status === 'current'
}

export function versionStatus(row) {
  if (row?.validation_status === 'failed') return { label: "Didn't pass checks", tone: 'destructive' }
  if (isCurrent(row)) return { label: 'Current', tone: 'success' }
  if (row?.lifecycle_status === 'draft') return { label: 'Draft', tone: 'warning' }
  if (row?.lifecycle_status === 'stale') return { label: 'Out of date', tone: 'warning' }
  return { label: 'Replaced', tone: 'default' }
}

export function versionDate(row) {
  return row?.updated_at || row?.created_at || null
}

/** The latest current version of each document, or its newest version when none is current. */
export function latestDocuments(rows) {
  const versions = projectVersions(rows)
  const documents = []
  for (const { family, name } of PROJECT_DOCUMENTS) {
    const family_versions = versionsOf(versions, family)
    if (family_versions.length === 0) continue
    const version = family_versions.find(isCurrent) || family_versions[0]
    documents.push({ family, name, version, versionCount: family_versions.length, status: versionStatus(version) })
  }
  return documents
}

export function buildSteps(rows) {
  const versions = projectVersions(rows)
  let nextAssigned = false
  return BUILD_STEPS.map((step) => {
    const current = versionsOf(versions, step.family).find(isCurrent) || null
    if (current) return { ...step, state: 'done', date: versionDate(current) }
    const state = nextAssigned ? 'not_started' : 'next'
    nextAssigned = true
    return { ...step, state, date: null }
  })
}

function lifecycleOf(app) {
  return text(app?.status) || text(app?.lifecycle_state) || 'draft'
}

function buildRun(app) {
  return app?.current_build_run && typeof app.current_build_run === 'object' ? app.current_build_run : {}
}

function activeChat(app) {
  const run = buildRun(app)
  const chatId = text(run.active_chat_id) || text(app?.active_chat_id)
  const workflowId = text(run.active_workflow_id) || text(app?.active_workflow_id)
  return chatId && workflowId ? { chatId, workflowId } : null
}

/** Lifecycle states in which a build conversation is producing a new version. */
const BUILDING_STATES = new Set(['draft', 'building', 'configuring'])

/**
 * Where the app's build stands.
 *
 * The app's lifecycle state (the one My Apps shows) wins when it says more
 * than the documents can: `needs_change` (`needs_revision`), `ready_for_review`
 * (`review`), `deploying`, `archived`, and a rebuild in progress (a building
 * state with a recorded build conversation, even when an earlier app version
 * passed or the newest one failed). Otherwise the saved documents decide:
 * `not_started` (nothing saved and no build conversation), `in_progress`,
 * `built` (the current app version passed its checks), `failed` (the newest
 * app version did not pass its checks and no build is running), or `live` (an
 * `active` app without a passing app version, such as an imported app).
 *
 * `unknownFamilies` names document families the caller could not read; a next
 * step in one of them is not guessed.
 */
export function projectStatus({ versions: rows = [], app = null, unknownFamilies = [] } = {}) {
  const lifecycle = lifecycleOf(app)
  if (lifecycle === 'needs_revision') return { kind: 'needs_change', nextStep: null, date: null }
  if (lifecycle === 'review') return { kind: 'ready_for_review', nextStep: null, date: null }
  if (lifecycle === 'deploying') return { kind: 'deploying', nextStep: null, date: null }
  if (lifecycle === 'archived') return { kind: 'archived', nextStep: null, date: null }
  const versions = projectVersions(rows)
  const appVersions = versionsOf(versions, 'app_bundle')
  const newestApp = appVersions[0] || null
  const currentApp = appVersions.find(isCurrent) || null
  // A build conversation running now outranks the result of an earlier
  // attempt: after a failed version the owner retries, and the server moves
  // the app back to a building state with the retry's chat recorded.
  const buildRunning = BUILDING_STATES.has(lifecycle) && Boolean(activeChat(app))
  if (newestApp?.validation_status === 'failed' && !buildRunning) {
    return { kind: 'failed', nextStep: null, date: versionDate(newestApp) }
  }
  if (buildRunning && currentApp) return { kind: 'in_progress', firstBuild: false, nextStep: null, date: null }
  if (currentApp?.validation_status === 'passed') {
    return { kind: 'built', nextStep: null, date: versionDate(currentApp) }
  }
  if (lifecycle === 'active') return { kind: 'live', nextStep: null, date: null }
  if (versions.length === 0 && !activeChat(app) && unknownFamilies.length === 0) {
    return { kind: 'not_started', nextStep: BUILD_STEPS[0].next, date: null }
  }
  if (currentApp) return { kind: 'in_progress', firstBuild: false, nextStep: null, date: null }
  const nextStep = buildSteps(versions).find((step) => step.state === 'next')
  const reviewReady = newestApp?.lifecycle_status === 'draft' && newestApp.validation_status === 'passed'
  const unknown = new Set(unknownFamilies)
  return {
    kind: 'in_progress',
    firstBuild: true,
    nextStep: reviewReady || !nextStep ? 'review the app' : unknown.has(nextStep.family) ? null : nextStep.next,
    date: null,
  }
}

export function projectStatusSentence(status, formatDate = (value) => String(value || '')) {
  switch (status?.kind) {
    case 'not_started':
      return "This app's build hasn't started."
    case 'built':
      return status.date ? `Built. Last updated ${formatDate(status.date)}.` : 'Built.'
    case 'failed':
      return "The last build didn't pass its checks."
    case 'needs_change':
      return 'Needs a change before the build can continue.'
    case 'ready_for_review':
      return 'Ready for your review.'
    case 'live':
      return 'This app is live.'
    case 'deploying':
      return 'Being deployed.'
    case 'archived':
      return 'Archived.'
    default:
      if (status?.firstBuild === false) return 'A new build is in progress.'
      return status?.nextStep ? `First build in progress. Next step: ${status.nextStep}.` : 'First build in progress.'
  }
}

function resumeHref(app) {
  const chat = activeChat(app)
  if (!chat || !CONTINUE_STATES.has(lifecycleOf(app))) return null
  const params = new URLSearchParams({ workflow: chat.workflowId, mode: 'workflow', chat_id: chat.chatId })
  // The chat's execution app (chat_app_id) is the scope the server stored the session under.
  const scope = text(app?.chat_app_id) || text(app?.app_id)
  if (scope) params.set('app_id', scope)
  return `/chat?${params.toString()}`
}

function reviewHref(appId) {
  return `/apps/${encodeURIComponent(appId || '')}/activity`
}

/**
 * The single button for the app's build.
 *
 * type `link` navigates; type `start` asks the server to start `workflowId`
 * bound to `buildRegistryId` (`POST /api/workflows/trigger`, as the build
 * review page does); type `none` means the server records no entry this page
 * can open, and `reason` says so.
 */
export function projectAction({ status, app = null, appId = null } = {}) {
  const lifecycle = lifecycleOf(app)
  const buildRegistryId = text(app?.build_registry_id) || null
  const resume = resumeHref(app)
  const run = buildRun(app)
  const routeAppId = appId || text(app?.app_id)

  if (status?.kind === 'failed') {
    return { type: 'link', label: 'See what failed', href: reviewHref(routeAppId) }
  }
  if (status?.kind === 'live') {
    return { type: 'link', label: 'Open overview', href: `/apps/${encodeURIComponent(routeAppId || '')}/overview` }
  }
  if (status?.kind === 'deploying' || status?.kind === 'archived') {
    // Nothing to build from here; the status says what is happening.
    return { type: 'none', label: null, reason: null }
  }
  if (status?.kind === 'ready_for_review') {
    // A saved build waiting for review is reviewed on the build review page,
    // as My Apps routes it, even when its generation chat is still recorded.
    if (text(run.artifact_version_id)) return { type: 'link', label: 'Review the app', href: reviewHref(routeAppId) }
    if (resume) return { type: 'link', label: 'Review the app', href: resume }
    return { type: 'none', label: 'Review the app', reason: 'This app has no saved build to review yet.' }
  }
  if (status?.kind === 'needs_change') {
    if (resume) return { type: 'link', label: 'Continue build', href: resume }
    if (text(run.artifact_version_id)) return { type: 'link', label: 'Continue build', href: reviewHref(routeAppId) }
    return { type: 'none', label: 'Continue build', reason: 'This app has no build conversation to reopen.' }
  }
  if (status?.kind === 'built') {
    if (buildRegistryId && (activeChat(app) || (REVIEW_STATES.has(lifecycle) && text(run.artifact_version_id)))) {
      return { type: 'start', label: 'Make a change', workflowId: 'AppReview', buildRegistryId }
    }
    return { type: 'link', label: 'Make a change', href: resume || reviewHref(routeAppId) }
  }

  const label = status?.kind === 'not_started' ? 'Start build' : 'Continue build'
  if (resume) return { type: 'link', label, href: resume }
  if (lifecycle === 'draft' && buildRegistryId) {
    return { type: 'start', label, workflowId: 'ValueEngine', buildRegistryId }
  }
  return { type: 'none', label, reason: 'This app has no build conversation to reopen.' }
}

/** What the readable view shows for a saved document version. */
export function documentContent(version) {
  const metadata = version?.commit_metadata?.metadata
  if (!metadata || typeof metadata !== 'object') return null
  if (metadata.summary_payload !== undefined && metadata.summary_payload !== null) return metadata.summary_payload
  if (version.build_family === 'workflow_bundle') {
    const content = {
      workflow_name: metadata.workflow_name,
      workflows: metadata.workflow_integration_metadata?.workflows,
      plan_review: metadata.workflow_plan_review
        ? { status: metadata.workflow_plan_review.status, rationale: metadata.workflow_plan_review.rationale }
        : undefined,
    }
    return Object.values(content).some((value) => value !== undefined && value !== null) ? content : null
  }
  return null
}

const ACRONYMS = new Set(['ai', 'api', 'id', 'ui', 'url', 'mvp', 'llm', 'faq', 'kpi', 'sdk'])

export function humanizeKey(key) {
  const words = String(key || '')
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
    .replace(/[_-]+/g, ' ')
    .trim()
    .split(/\s+/)
    .filter(Boolean)
    .map((word) => (ACRONYMS.has(word.toLowerCase()) ? word.toUpperCase() : word.toLowerCase()))
  if (words.length === 0) return ''
  const [first, ...rest] = words
  return [first.charAt(0).toUpperCase() + first.slice(1), ...rest].join(' ')
}

/** Identifier fields carry no meaning for a reader. */
export function isHiddenKey(key) {
  const name = String(key || '').toLowerCase()
  return name === 'id' || name === '_id' || name.endsWith('_id') || name.endsWith('_ids')
}

export function isHexColor(value) {
  return typeof value === 'string' && /^#(?:[0-9a-f]{3}|[0-9a-f]{4}|[0-9a-f]{6}|[0-9a-f]{8})$/i.test(value.trim())
}

export function isEmptyValue(value) {
  if (value === null || value === undefined) return true
  if (typeof value === 'string') return value.trim() === ''
  if (Array.isArray(value)) return value.every(isEmptyValue)
  if (typeof value === 'object') {
    return Object.entries(value).every(([key, item]) => isHiddenKey(key) || isEmptyValue(item))
  }
  return false
}

/** A readable title for one object in a list. */
export function itemTitle(item, index) {
  for (const key of ['name', 'title', 'label', 'route', 'path', 'key']) {
    if (typeof item?.[key] === 'string' && item[key].trim()) return item[key].trim()
  }
  return `Item ${index + 1}`
}

/** Apps on My Apps whose build is not finished, most recently updated first. */
export function buildsInProgressCandidates(apps, limit = 5) {
  const rows = (Array.isArray(apps) ? apps : []).filter((app) => (
    text(app?.build_registry_id) && text(app?.app_id) && CONTINUE_STATES.has(lifecycleOf(app))
  ))
  rows.sort((left, right) => (
    (Date.parse(right.updated_at || '') || 0) - (Date.parse(left.updated_at || '') || 0)
  ))
  return { apps: rows.slice(0, limit), more: Math.max(0, rows.length - limit) }
}
