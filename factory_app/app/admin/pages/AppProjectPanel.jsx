import { useEffect, useMemo, useState } from 'react'

import {
  LinkButton,
  StatusPill,
  StudioInlineEmptyState,
  StudioLoadingState,
  StudioSlideOver,
  SurfaceCard,
} from '../../ui/components/StudioShared.jsx'
import { formatDateTimeLabel } from './AppStudioChrome.jsx'
import ProjectBuildButton from './ProjectBuildButton.jsx'
import ReadableDocument from './ReadableDocument.jsx'
import { fetchProjectVersions } from './projectRoomApi.js'
import {
  buildSteps,
  documentContent,
  documentName,
  latestDocuments,
  projectAction,
  projectStatus,
  projectStatusSentence,
  projectVersions,
  versionDate,
  versionStatus,
} from './projectRoomModel.js'

function formatDay(value) {
  if (!value) return ''
  try {
    return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium' }).format(new Date(value))
  } catch {
    return ''
  }
}

function useProjectVersions(buildRegistryId, dataMode, demoVersions) {
  const [state, setState] = useState({ key: null, versions: [], incomplete: false, error: null })
  const key = JSON.stringify([buildRegistryId, dataMode])

  useEffect(() => {
    if (dataMode === 'demo' || !buildRegistryId) return undefined
    const controller = new AbortController()
    fetchProjectVersions(buildRegistryId, { signal: controller.signal })
      .then(({ versions, incomplete }) => setState({ key, versions, incomplete, error: null }))
      .catch((err) => {
        if (err?.name === 'AbortError') return
        setState({ key, versions: [], incomplete: false, error: err instanceof Error ? err.message : 'Saved documents unavailable.' })
      })
    return () => controller.abort()
  }, [buildRegistryId, dataMode, key])

  if (dataMode === 'demo') return { loading: false, versions: demoVersions, incomplete: false, error: null }
  if (!buildRegistryId) return { loading: false, versions: [], incomplete: false, error: null }
  return { loading: state.key !== key, ...state }
}

const STEP_MARKS = {
  done: { icon: '✓', ring: 'border-success/60 bg-success/10 text-success', text: 'text-foreground' },
  next: { icon: '→', ring: 'border-primary/70 bg-primary/10 text-primary', text: 'text-foreground' },
  not_started: { icon: '', ring: 'border-border bg-muted/40 text-muted-foreground', text: 'text-muted-foreground' },
}

/** A short step date: "Sep 15", with the year only when it is not this year. */
function formatStepDay(value) {
  if (!value) return ''
  try {
    const date = new Date(value)
    const sameYear = date.getFullYear() === new Date().getFullYear()
    return new Intl.DateTimeFormat(undefined, sameYear
      ? { month: 'short', day: 'numeric' }
      : { month: 'short', day: 'numeric', year: 'numeric' }).format(date)
  } catch {
    return ''
  }
}

function stepDetail(step) {
  if (step.state === 'done') return `Done${step.date ? ` · ${formatStepDay(step.date)}` : ''}`
  return step.state === 'next' ? 'Next' : 'Not started'
}

/** The build steps in order, across the card: one row on wide screens, wrapping on narrow ones. */
function StepStrip({ steps }) {
  return (
    <ol aria-label="Build steps" className="grid grid-cols-2 gap-x-3 gap-y-4 sm:grid-cols-3 lg:grid-cols-6">
      {steps.map((step) => {
        const mark = STEP_MARKS[step.state] || STEP_MARKS.not_started
        return (
          <li key={step.family} className="flex min-w-0 items-start gap-2" aria-current={step.state === 'next' ? 'step' : undefined}>
            <span
              aria-hidden="true"
              className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-full border text-xs font-bold ${mark.ring}`}
            >
              {mark.icon}
            </span>
            <span className="min-w-0">
              <span className={`block text-sm font-semibold leading-5 ${mark.text}`}>{step.label}</span>
              <span className="block text-xs leading-5 text-muted-foreground">{stepDetail(step)}</span>
            </span>
          </li>
        )
      })}
    </ol>
  )
}

/** One top section: where the build stands, its one button, then the steps. */
function StatusSection({ status, action, steps }) {
  return (
    <SurfaceCard accent>
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <p className="text-lg font-semibold leading-7 text-foreground">{projectStatusSentence(status, formatDay)}</p>
        <div className="shrink-0">
          <ProjectBuildButton action={action} />
        </div>
      </div>
      <div className="mt-5 border-t border-border/50 pt-4">
        <StepStrip steps={steps} />
      </div>
    </SurfaceCard>
  )
}

function DocumentCard({ document, onOpen }) {
  const { version, status, name } = document
  return (
    <button
      type="button"
      onClick={() => onOpen(document)}
      className="block w-full min-w-0 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
      aria-label={`Open ${name}`}
    >
      <SurfaceCard className="h-full transition hover:border-primary/35">
        <div className="flex items-start justify-between gap-3">
          <h3 className="min-w-0 text-sm font-semibold text-foreground">{name}</h3>
          <StatusPill tone={status.tone}>{status.label}</StatusPill>
        </div>
        <p className="mt-2 text-xs text-muted-foreground">
          Version {version.version_number ?? '?'}{versionDate(version) ? ` · ${formatDay(versionDate(version))}` : ''}
        </p>
      </SurfaceCard>
    </button>
  )
}

function DocumentView({ document, appId }) {
  const { version, status, family } = document
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-2">
        <StatusPill tone={status.tone}>{status.label}</StatusPill>
        <span className="text-xs text-muted-foreground">
          Version {version.version_number ?? '?'} · {formatDateTimeLabel(versionDate(version))}
        </span>
      </div>
      {family === 'app_bundle' ? (
        <div className="space-y-3">
          <p className="text-sm leading-6 text-muted-foreground">
            The app is a set of files. Open the build review to try it, see its checks, and ask for changes.
          </p>
          <LinkButton to={`/apps/${encodeURIComponent(appId || '')}/activity`} size="sm">Open build review</LinkButton>
        </div>
      ) : (
        <ReadableDocument content={documentContent(version)} />
      )}
    </div>
  )
}

function VersionList({ versions }) {
  if (versions.length === 0) return null
  return (
    <details className="rounded-lg border border-border/55 bg-background/30 p-4">
      <summary className="cursor-pointer text-sm font-semibold text-foreground">All versions ({versions.length})</summary>
      <ul className="mt-3 divide-y divide-border/50">
        {versions.map((version) => {
          const status = versionStatus(version)
          return (
            <li key={version.id} className="flex flex-wrap items-center justify-between gap-2 py-2.5 text-sm">
              <span className="min-w-0 text-foreground">
                {documentName(version.build_family)} · Version {version.version_number ?? '?'}
              </span>
              <span className="flex flex-wrap items-center gap-2">
                <span className="text-xs text-muted-foreground">{formatDay(versionDate(version))}</span>
                <StatusPill tone={status.tone}>{status.label}</StatusPill>
              </span>
            </li>
          )
        })}
      </ul>
    </details>
  )
}

/**
 * The app's Project: build status with one button, the build steps, its
 * saved documents, and every version. Read-only.
 */
export default function AppProjectPanel({ appId, app, buildRegistryId, dataMode, demoVersions = [] }) {
  const { loading, versions: rows, incomplete, error } = useProjectVersions(buildRegistryId, dataMode, demoVersions)
  const [openFamily, setOpenFamily] = useState(null)
  const versions = useMemo(() => projectVersions(rows), [rows])
  const documents = useMemo(() => latestDocuments(versions), [versions])
  const steps = useMemo(() => buildSteps(versions), [versions])
  const status = useMemo(() => projectStatus({ versions, app }), [versions, app])
  const action = useMemo(() => projectAction({ status, app, appId }), [status, app, appId])
  const openDocument = documents.find((document) => document.family === openFamily) || null

  if (loading) return <StudioLoadingState label="Loading the project…" />

  return (
    <div className="space-y-5">
      {error ? (
        <StudioInlineEmptyState title="Saved documents could not be loaded" description={error} />
      ) : (
        <StatusSection status={status} action={action} steps={steps} />
      )}
      {incomplete ? (
        <p className="text-sm text-warning">Some documents could not be loaded. Refresh to try again.</p>
      ) : null}

      <section aria-labelledby="project-documents-heading" className="space-y-3">
        <h2 id="project-documents-heading" className="px-1 text-base font-semibold text-foreground">Documents</h2>
        {documents.length === 0 ? (
          <StudioInlineEmptyState
            title="No documents yet"
            description="Each build step saves a document here: the idea, the look and feel, the design, plans and pricing, the AI workflows, and the app."
          />
        ) : (
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {documents.map((document) => (
              <DocumentCard key={document.family} document={document} onOpen={(item) => setOpenFamily(item.family)} />
            ))}
          </div>
        )}
      </section>

      <VersionList versions={versions} />

      <StudioSlideOver
        open={Boolean(openDocument)}
        title={openDocument?.name || ''}
        onClose={() => setOpenFamily(null)}
        maxWidthClass="max-w-2xl"
      >
        {openDocument ? <DocumentView document={openDocument} appId={appId} /> : null}
      </StudioSlideOver>
    </div>
  )
}
