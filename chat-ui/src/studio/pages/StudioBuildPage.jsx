import { useEffect, useMemo, useState } from 'react'

import { useWorkflowStart } from '../../hooks/useWorkflowStart.js'
import { BuilderWorkspaceLayout } from '../components/BuilderWorkspaceNav.jsx'


const API_BASE =
  (typeof import.meta !== 'undefined' && import.meta.env?.VITE_API_URL) ||
  ''

const REQUEST_KIND_OPTIONS = [
  { value: 'existing_app', label: 'Existing App Build' },
  { value: 'new_app', label: 'New App Build' },
  { value: 'refinement', label: 'Refinement' },
]

const REFINEMENT_COPY = {
  patch: {
    title: 'Patch',
    description: 'Localized fixes or targeted corrections against the current app bundle.',
  },
  design: {
    title: 'Design',
    description: 'Brand, layout, navigation, or UI-schema changes that stay within the same concept.',
  },
  feature: {
    title: 'Feature',
    description: 'A new capability added inside the current product direction.',
  },
  core: {
    title: 'Core',
    description: 'A foundational reset that changes the value proposition or product identity.',
  },
}


function formatTimestamp(value) {
  if (!value) {
    return 'Not saved yet'
  }
  const parsed = new Date(value)
  if (Number.isNaN(parsed.getTime())) {
    return value
  }
  return parsed.toLocaleString()
}


function StatusPill({ children, tone = 'default' }) {
  const tones = {
    default: 'border-border bg-muted text-muted-foreground',
    primary: 'border-primary/30 bg-primary/10 text-primary',
    success: 'border-success/30 bg-success/10 text-success',
    warning: 'border-warning/30 bg-warning/10 text-warning',
  }

  return (
    <span className={`inline-flex items-center rounded-full border px-3 py-1 text-xs font-semibold uppercase tracking-[0.18em] ${tones[tone]}`}>
      {children}
    </span>
  )
}


function SurfaceCard({ title, eyebrow, children, accent = false, className = '' }) {
  return (
    <section className={`rounded-3xl border ${accent ? 'border-primary/30 bg-gradient-to-br from-primary/10 via-card to-secondary/10' : 'border-border bg-card'} p-6 shadow-sm ${className}`}>
      {eyebrow ? <div className="mb-2 text-xs font-semibold uppercase tracking-[0.22em] text-muted-foreground">{eyebrow}</div> : null}
      <h2 className="text-lg font-semibold text-foreground">{title}</h2>
      <div className="mt-4">{children}</div>
    </section>
  )
}


function LoadingState() {
  return (
    <div className="flex min-h-full flex-1 items-center justify-center bg-background px-6 py-10">
      <div className="flex items-center gap-3 rounded-2xl border border-border bg-card px-5 py-4 text-sm text-muted-foreground shadow-sm">
        <div className="h-4 w-4 animate-spin rounded-full border-2 border-primary border-t-transparent" />
        Loading Studio Build...
      </div>
    </div>
  )
}


function ErrorState({ message }) {
  return (
    <div className="flex min-h-full flex-1 items-center justify-center bg-background px-6 py-10">
      <div className="max-w-xl rounded-3xl border border-destructive/30 bg-destructive/10 p-6 shadow-sm">
        <div className="text-xs font-semibold uppercase tracking-[0.2em] text-destructive">Studio Build Unavailable</div>
        <p className="mt-3 text-sm leading-6 text-foreground">{message}</p>
      </div>
    </div>
  )
}


function Metric({ label, value, detail = null }) {
  return (
    <div className="rounded-2xl border border-border bg-background/70 p-4">
      <div className="text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">{label}</div>
      <div className="mt-2 text-2xl font-semibold text-foreground">{value}</div>
      {detail ? <div className="mt-1 text-sm text-muted-foreground">{detail}</div> : null}
    </div>
  )
}


function ActionButton({ children, onClick, disabled = false, variant = 'primary' }) {
  const variants = {
    primary: 'bg-primary text-primary-foreground hover:bg-primary/90 border-primary/30',
    secondary: 'bg-background text-foreground hover:bg-muted border-border',
  }

  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className={`inline-flex items-center justify-center rounded-2xl border px-4 py-2.5 text-sm font-semibold transition ${variants[variant]} disabled:cursor-not-allowed disabled:opacity-50`}
    >
      {children}
    </button>
  )
}


export default function StudioBuildPage() {
  const [summary, setSummary] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [draft, setDraft] = useState('')
  const [requestKind, setRequestKind] = useState('existing_app')
  const [localError, setLocalError] = useState(null)
  const [saving, setSaving] = useState(false)
  const { startWorkflow, starting, error: workflowError } = useWorkflowStart()

  const syncFromSummary = (payload) => {
    setSummary(payload)
    const currentRequest = payload?.build?.current_request || {}
    setDraft(currentRequest.text || '')
    setRequestKind(currentRequest.request_kind || payload?.app?.journey || 'existing_app')
  }

  useEffect(() => {
    let cancelled = false

    async function loadStudioBuild() {
      try {
        const response = await fetch(`${API_BASE}/api/studio/build`)
        if (!response.ok) {
          throw new Error(`Studio Build request failed: ${response.status} ${response.statusText}`)
        }

        const payload = await response.json()
        if (!cancelled) {
          syncFromSummary(payload)
          setError(null)
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : 'Studio Build could not be loaded.')
        }
      } finally {
        if (!cancelled) {
          setLoading(false)
        }
      }
    }

    loadStudioBuild()
    return () => {
      cancelled = true
    }
  }, [])

  const build = summary?.build || {}
  const app = summary?.app || {}
  const ai = summary?.ai || {}
  const studio = summary?.studio || {}
  const workspace = summary?.workspace || {}
  const currentRequest = build.current_request || {}
  const currentPlan = build.current_plan || {}
  const recentRequests = build.recent_requests || []
  const lastSavedAt = formatTimestamp(build.last_saved_at)
  const busy = saving || starting

  const refinementModes = useMemo(() => {
    const support = build.refinement_support || {}
    return Object.entries(REFINEMENT_COPY).map(([changeClass, copy]) => ({
      changeClass,
      ...copy,
      available: support[changeClass]?.available === true,
      workflowId: support[changeClass]?.workflow_id || null,
    }))
  }, [build.refinement_support])

  const combinedError = localError || workflowError || null

  const persistBuildRequest = async ({ nextRequestKind, changeClass = null }) => {
    setLocalError(null)
    setSaving(true)
    try {
      const response = await fetch(`${API_BASE}/api/studio/build`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          request_text: draft,
          request_kind: nextRequestKind,
          ...(changeClass ? { change_class: changeClass } : {}),
        }),
      })

      if (!response.ok) {
        const payload = await response.json().catch(() => ({ detail: response.statusText }))
        throw new Error(payload.detail || 'Studio Build draft could not be saved.')
      }

      const payload = await response.json()
      syncFromSummary(payload)
      return payload
    } catch (err) {
      setLocalError(err instanceof Error ? err.message : 'Studio Build draft could not be saved.')
      return null
    } finally {
      setSaving(false)
    }
  }

  const handleSaveDraft = async () => {
    await persistBuildRequest({ nextRequestKind: requestKind })
  }

  const handleInitialBuild = async () => {
    setLocalError(null)
    if (requestKind === 'refinement') {
      setLocalError('Switch Build Mode to New App or Existing App before opening an initial build conversation.')
      return
    }
    if (!draft.trim()) {
      setLocalError('Enter a build request before opening an initial build conversation.')
      return
    }
    if (!build.supports_initial_compile || !build.initial_compile_workflow) {
      setLocalError('Initial build workflows are not installed in this workspace yet.')
      return
    }

    const persisted = await persistBuildRequest({ nextRequestKind: requestKind })
    if (!persisted) {
      return
    }

    await startWorkflow(build.initial_compile_workflow, {}, { trigger_source: 'chat' })
  }

  const handleRefinement = async (changeClass, available) => {
    setLocalError(null)
    if (!draft.trim()) {
      setLocalError('Enter a build or refinement request before launching a routed refinement run.')
      return
    }
    if (!available) {
      setLocalError('This refinement path is unavailable because the owning workflow is not installed in this workspace.')
      return
    }

    const persisted = await persistBuildRequest({ nextRequestKind: 'refinement', changeClass })
    if (!persisted) {
      return
    }

    await startWorkflow(null, {}, {
      trigger_source: 'refinement',
      change_class: changeClass,
      artifact_kind: 'app_bundle',
      raw_user_request: draft.trim(),
    })
  }

  if (loading) {
    return <LoadingState />
  }

  if (error || !summary) {
    return <ErrorState message={error || 'Studio Build returned no summary.'} />
  }

  return (
    <BuilderWorkspaceLayout>
      <div className="flex flex-col gap-6">
        <SurfaceCard title="Build" eyebrow="Studio Control Plane" accent>
          <div className="grid gap-6 lg:grid-cols-[minmax(0,1.45fr)_minmax(320px,0.9fr)]">
            <div>
              <div className="flex flex-wrap items-center gap-2">
                <StatusPill tone="primary">{app.journey === 'existing_app' ? 'Existing App' : app.journey === 'new_app' ? 'New App' : 'Not Configured'}</StatusPill>
                <StatusPill tone="success">{studio.local_only ? 'Local Only' : 'Shared Surface'}</StatusPill>
                <StatusPill tone={build.supports_initial_compile ? 'success' : 'warning'}>
                  {build.supports_initial_compile ? 'Initial Compile Ready' : 'Compile Workflows Missing'}
                </StatusPill>
              </div>
              <p className="mt-4 max-w-3xl text-sm leading-7 text-muted-foreground">
                Shape the next build request here, then launch the correct workflow path instead of dropping back into terminal-only commands.
              </p>
              <div className="mt-5 rounded-3xl border border-border bg-background/70 p-4">
                <div className="grid gap-4 md:grid-cols-[220px_minmax(0,1fr)]">
                  <div>
                    <label className="block text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground" htmlFor="studio-build-kind">
                      Build Mode
                    </label>
                    <select
                      id="studio-build-kind"
                      value={requestKind}
                      onChange={(event) => setRequestKind(event.target.value)}
                      className="mt-3 w-full rounded-2xl border border-border bg-card px-4 py-3 text-sm text-foreground outline-none transition focus:border-primary/40 focus:ring-2 focus:ring-primary/20"
                    >
                      {REQUEST_KIND_OPTIONS.map((option) => (
                        <option key={option.value} value={option.value}>
                          {option.label}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div>
                    <label className="block text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground" htmlFor="studio-build-draft">
                      Build Request
                    </label>
                    <textarea
                      id="studio-build-draft"
                      value={draft}
                      onChange={(event) => setDraft(event.target.value)}
                      placeholder="Describe the next thing you want Mozaiks to build or refine."
                      className="mt-3 min-h-40 w-full rounded-2xl border border-border bg-card px-4 py-3 text-sm leading-7 text-foreground outline-none transition focus:border-primary/40 focus:ring-2 focus:ring-primary/20"
                    />
                  </div>
                </div>
                <div className="mt-3 flex flex-wrap items-center gap-3">
                  <ActionButton onClick={handleSaveDraft} disabled={busy || (!draft.trim() && !currentRequest.text)} variant="secondary">
                    {saving ? 'Saving...' : 'Save Build Draft'}
                  </ActionButton>
                  <ActionButton onClick={handleInitialBuild} disabled={busy || !build.supports_initial_compile}>
                    {starting ? 'Starting...' : 'Open Initial Build Conversation'}
                  </ActionButton>
                </div>
                <div className="mt-3 text-sm text-muted-foreground">
                  <span className="font-semibold text-foreground">Last saved:</span> {lastSavedAt}
                </div>
                <p className="mt-3 text-sm leading-6 text-muted-foreground">{build.draft_handoff_note}</p>
                {combinedError ? <div className="mt-3 rounded-2xl border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">{combinedError}</div> : null}
              </div>
            </div>

            <div className="rounded-3xl border border-border bg-background/75 p-5">
              <div className="text-xs font-semibold uppercase tracking-[0.2em] text-muted-foreground">Current Build State</div>
              <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-1 xl:grid-cols-2">
                <Metric label="Plan" value={build.plan_state || 'unknown'} detail="No persisted build plan yet" />
                <Metric label="Approval" value={build.approval_state || 'unknown'} detail="Approval stays local until plan review exists" />
                <Metric label="Provider" value={ai.provider || 'Not configured'} detail={ai.model || 'Choose a model'} />
                <Metric label="Workflows" value={workspace.workflow_count ?? 0} detail={`${(build.available_workflows || []).length} declared in active workspace`} />
              </div>
              <div className="mt-4 space-y-3 text-sm text-muted-foreground">
                <div>
                  <span className="font-semibold text-foreground">State file:</span> {build.state_file || 'platform/config/build.json'}
                </div>
                <div>
                  <span className="font-semibold text-foreground">Current request kind:</span> {currentRequest.request_kind || requestKind}
                </div>
                <div>
                  <span className="font-semibold text-foreground">Current change class:</span> {currentRequest.change_class || 'none'}
                </div>
              </div>
            </div>
          </div>
        </SurfaceCard>

        <div className="grid gap-6 xl:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)]">
          <SurfaceCard title="Refinement Paths" eyebrow="Routed Re-Entry">
            <div className="grid gap-4 md:grid-cols-2">
              {refinementModes.map((mode) => (
                <div key={mode.changeClass} className="rounded-3xl border border-border bg-background/70 p-5">
                  <div className="flex items-center justify-between gap-3">
                    <h3 className="text-base font-semibold text-foreground">{mode.title}</h3>
                    <StatusPill tone={mode.available ? 'success' : 'warning'}>{mode.available ? 'Available' : 'Unavailable'}</StatusPill>
                  </div>
                  <p className="mt-3 text-sm leading-6 text-muted-foreground">{mode.description}</p>
                  <div className="mt-4 text-sm text-muted-foreground">
                    Owner workflow: <span className="font-semibold text-foreground">{mode.workflowId || 'not installed'}</span>
                  </div>
                  <div className="mt-4">
                    <ActionButton onClick={() => handleRefinement(mode.changeClass, mode.available)} disabled={busy || !mode.available} variant="secondary">
                      {starting ? 'Starting...' : `Route ${mode.title}`}
                    </ActionButton>
                  </div>
                </div>
              ))}
            </div>
          </SurfaceCard>

          <SurfaceCard title="Workspace Availability" eyebrow="Guardrails">
            <div className="space-y-4">
              <div>
                <div className="text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">Current Plan</div>
                <div className="mt-3 rounded-2xl border border-border bg-background/70 p-4 text-sm text-muted-foreground">
                  {currentPlan.summary || currentRequest.text || 'No persisted build draft or plan yet.'}
                </div>
              </div>
              <div className="grid gap-4 md:grid-cols-2">
                <div>
                  <div className="text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">Owned Paths</div>
                  <div className="mt-3 space-y-2 text-sm text-muted-foreground">
                    {(currentPlan.owned_paths || []).length > 0 ? (
                      currentPlan.owned_paths.map((path) => (
                        <div key={path} className="rounded-2xl border border-border bg-background/70 px-4 py-3">
                          {path}
                        </div>
                      ))
                    ) : (
                      <div className="rounded-2xl border border-border bg-background/70 px-4 py-3">No owned paths persisted yet.</div>
                    )}
                  </div>
                </div>
                <div>
                  <div className="text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">Acceptance Criteria</div>
                  <div className="mt-3 space-y-2 text-sm text-muted-foreground">
                    {(currentPlan.acceptance_criteria || []).length > 0 ? (
                      currentPlan.acceptance_criteria.map((criterion) => (
                        <div key={criterion} className="rounded-2xl border border-border bg-background/70 px-4 py-3">
                          {criterion}
                        </div>
                      ))
                    ) : (
                      <div className="rounded-2xl border border-border bg-background/70 px-4 py-3">No acceptance criteria persisted yet.</div>
                    )}
                  </div>
                </div>
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                {Object.entries(build.generator_workflows || {}).map(([workflowId, available]) => (
                  <div key={workflowId} className="rounded-2xl border border-border bg-background/70 px-4 py-3">
                    <div className="text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">{workflowId}</div>
                    <div className="mt-2 text-sm font-semibold text-foreground">{available ? 'Installed' : 'Missing'}</div>
                  </div>
                ))}
              </div>
              <div>
                <div className="text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">Recent Requests</div>
                <div className="mt-3 space-y-2 text-sm leading-6 text-muted-foreground">
                  {recentRequests.length > 0 ? (
                    recentRequests.map((entry) => (
                      <div key={`${entry.saved_at || 'unsaved'}:${entry.text}`} className="rounded-2xl border border-border bg-background/70 px-4 py-3">
                        <div className="font-semibold text-foreground">{entry.request_kind || 'untyped request'}</div>
                        <div className="mt-1">{entry.text}</div>
                        <div className="mt-2 text-xs uppercase tracking-[0.18em] text-muted-foreground">
                          {entry.change_class || 'no change class'} · {formatTimestamp(entry.saved_at)}
                        </div>
                      </div>
                    ))
                  ) : (
                    <div className="rounded-2xl border border-border bg-background/70 px-4 py-3">No persisted request history yet.</div>
                  )}
                </div>
              </div>
              <div>
                <div className="text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">Request Examples</div>
                <ul className="mt-3 space-y-2 text-sm leading-6 text-muted-foreground">
                  {(build.request_examples || []).map((example) => (
                    <li key={example} className="rounded-2xl border border-border bg-background/70 px-4 py-3">
                      {example}
                    </li>
                  ))}
                </ul>
              </div>
            </div>
          </SurfaceCard>
        </div>
      </div>
    </BuilderWorkspaceLayout>
  )
}
