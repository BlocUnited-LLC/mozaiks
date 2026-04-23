import { useEffect, useMemo, useState } from 'react'

import { useWorkflowStart } from '../../hooks/useWorkflowStart.js'
import { AdminWorkspaceLayout } from '../../admin/components/AdminWorkspaceLayout.jsx'
import {
  API_BASE,
  StatusPill,
  SurfaceCard,
  Metric,
  ActionButton,
  StudioLoadingState,
  StudioErrorState,
} from '../StudioPrimitives.jsx'


const REQUEST_KIND_OPTIONS = [
  { value: 'existing_app', label: 'Existing App Build' },
  { value: 'new_app',      label: 'New App Build' },
  { value: 'refinement',   label: 'Refinement' },
]

const REFINEMENT_COPY = {
  patch:   { title: 'Patch',   description: 'Localized fixes or targeted corrections against the current app bundle.' },
  design:  { title: 'Design',  description: 'Brand, layout, navigation, or UI-schema changes within the same concept.' },
  feature: { title: 'Feature', description: 'A new capability added inside the current product direction.' },
  core:    { title: 'Core',    description: 'A foundational reset that changes the value proposition or product identity.' },
}

function formatTimestamp(value) {
  if (!value) return 'Not saved yet'
  const d = new Date(value)
  return Number.isNaN(d.getTime()) ? value : d.toLocaleString()
}


// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function BuildRequestForm({ draft, setDraft, requestKind, setRequestKind, examples, busy, saving, starting, lastSavedAt, draftHandoffNote, combinedError, onSave, onInitialBuild, supportsInitialCompile }) {
  return (
    <div className="rounded-3xl border border-border bg-background/70 p-4">
      <div className="grid gap-4 md:grid-cols-[220px_minmax(0,1fr)]">
        <div>
          <label className="block text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground" htmlFor="studio-build-kind">
            Build Mode
          </label>
          <select
            id="studio-build-kind"
            value={requestKind}
            onChange={(e) => setRequestKind(e.target.value)}
            className="mt-3 w-full rounded-2xl border border-border bg-card px-4 py-3 text-sm text-foreground outline-none transition focus:border-primary/40 focus:ring-2 focus:ring-primary/20"
          >
            {REQUEST_KIND_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>{opt.label}</option>
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
            onChange={(e) => setDraft(e.target.value)}
            placeholder="Describe what you want Mozaiks to build or refine…"
            className="mt-3 min-h-40 w-full rounded-2xl border border-border bg-card px-4 py-3 text-sm leading-7 text-foreground outline-none transition focus:border-primary/40 focus:ring-2 focus:ring-primary/20"
          />
          {/* Inline example hints when draft is empty */}
          {!draft.trim() && examples.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-2">
              {examples.slice(0, 3).map((ex) => (
                <button
                  key={ex}
                  type="button"
                  onClick={() => setDraft(ex)}
                  className="rounded-xl border border-border bg-muted/40 px-3 py-1.5 text-xs text-muted-foreground hover:bg-muted hover:text-foreground transition"
                >
                  {ex}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-3">
        <ActionButton
          onClick={onSave}
          disabled={busy || (!draft.trim())}
          variant="secondary"
        >
          {saving ? 'Saving…' : 'Save Build Draft'}
        </ActionButton>
        <ActionButton
          onClick={onInitialBuild}
          disabled={busy || !supportsInitialCompile}
        >
          {starting ? 'Starting…' : 'Start Build Conversation'}
        </ActionButton>
      </div>

      <div className="mt-3 text-sm text-muted-foreground">
        <span className="font-semibold text-foreground">Last saved:</span> {lastSavedAt}
      </div>

      {draftHandoffNote && (
        <p className="mt-3 text-sm leading-6 text-muted-foreground">{draftHandoffNote}</p>
      )}

      {combinedError && (
        <div className="mt-3 rounded-2xl border border-destructive/30 bg-destructive/10 px-4 py-3 text-sm text-destructive">
          {combinedError}
        </div>
      )}
    </div>
  )
}


function BuildStatePanel({ build, ai, workspace }) {
  const generatorWorkflows = Object.entries(build.generator_workflows || {})

  return (
    <div className="rounded-3xl border border-border bg-background/75 p-5">
      <div className="text-xs font-semibold uppercase tracking-[0.2em] text-muted-foreground">Current Build State</div>
      <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-1 xl:grid-cols-2">
        <Metric label="Plan"      value={build.plan_state || 'none'}     detail="Current persisted build plan" />
        <Metric label="Approval"  value={build.approval_state || 'none'} detail="Local until plan review exists" />
        <Metric label="Provider"  value={ai.provider || 'Not set'}       detail={ai.model || 'Choose a model'} />
        <Metric label="Workflows" value={workspace.workflow_count ?? 0}  detail={`${(build.available_workflows || []).length} declared`} />
      </div>

      <dl className="mt-4 space-y-2 text-sm text-muted-foreground">
        <div><span className="font-semibold text-foreground">Request kind:</span> {build.current_request?.request_kind || '—'}</div>
        <div><span className="font-semibold text-foreground">Change class:</span>  {build.current_request?.change_class  || '—'}</div>
        <div><span className="font-semibold text-foreground">State file:</span>    {build.state_file || 'platform/config/build.json'}</div>
      </dl>

      {/* Generator workflow availability */}
      {generatorWorkflows.length > 0 && (
        <div className="mt-4">
          <div className="text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground mb-2">Generator Workflows</div>
          <div className="space-y-1.5">
            {generatorWorkflows.map(([id, available]) => (
              <div key={id} className="flex items-center justify-between rounded-xl border border-border bg-background/70 px-3 py-2">
                <span className="text-xs text-muted-foreground font-mono">{id}</span>
                <span className={`text-xs font-semibold ${available ? 'text-success' : 'text-warning'}`}>
                  {available ? 'installed' : 'missing'}
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}


function RefinementCard({ mode, busy, starting, onRefinement }) {
  return (
    <div className="rounded-3xl border border-border bg-background/70 p-5">
      <div className="flex items-center justify-between gap-3">
        <h3 className="text-base font-semibold text-foreground">{mode.title}</h3>
        <StatusPill tone={mode.available ? 'success' : 'warning'}>
          {mode.available ? 'Available' : 'Unavailable'}
        </StatusPill>
      </div>
      <p className="mt-3 text-sm leading-6 text-muted-foreground">{mode.description}</p>
      <div className="mt-3 text-sm text-muted-foreground">
        Workflow: <span className="font-semibold text-foreground">{mode.workflowId || 'not installed'}</span>
      </div>
      <div className="mt-4">
        <ActionButton
          onClick={() => onRefinement(mode.changeClass, mode.available)}
          disabled={busy || !mode.available}
          variant="secondary"
        >
          {starting ? 'Starting…' : `Route ${mode.title}`}
        </ActionButton>
      </div>
    </div>
  )
}


function BuildHistory({ currentPlan, recentRequests }) {
  const hasPlan = currentPlan.summary || currentPlan.owned_paths?.length || currentPlan.acceptance_criteria?.length
  const hasRequests = recentRequests.length > 0

  return (
    <SurfaceCard title="Build History" eyebrow="Context">
      <div className="space-y-5">

        {/* Current plan */}
        {hasPlan && (
          <div>
            <div className="text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground mb-2">Current Plan</div>
            <div className="rounded-2xl border border-border bg-background/70 p-4 text-sm leading-7 text-muted-foreground">
              {currentPlan.summary || '—'}
            </div>
            {currentPlan.owned_paths?.length > 0 && (
              <div className="mt-2 flex flex-wrap gap-2">
                {currentPlan.owned_paths.map((p) => (
                  <span key={p} className="rounded-lg border border-border bg-muted/40 px-2 py-1 font-mono text-xs text-muted-foreground">{p}</span>
                ))}
              </div>
            )}
          </div>
        )}

        {/* Recent requests */}
        <div>
          <div className="text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground mb-2">Recent Requests</div>
          {hasRequests ? (
            <div className="space-y-2">
              {recentRequests.map((entry) => (
                <div key={`${entry.saved_at}:${entry.text}`} className="rounded-2xl border border-border bg-background/70 px-4 py-3 text-sm">
                  <div className="font-semibold text-foreground">{entry.request_kind || 'untyped'}</div>
                  <div className="mt-1 text-muted-foreground">{entry.text}</div>
                  <div className="mt-2 text-xs uppercase tracking-[0.14em] text-muted-foreground">
                    {entry.change_class || 'no class'} · {formatTimestamp(entry.saved_at)}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="rounded-2xl border border-border bg-background/70 px-4 py-3 text-sm text-muted-foreground">
              No request history yet.
            </div>
          )}
        </div>

      </div>
    </SurfaceCard>
  )
}


// ---------------------------------------------------------------------------
// Root
// ---------------------------------------------------------------------------

export default function StudioBuildPage() {
  const [summary, setSummary]       = useState(null)
  const [loading, setLoading]       = useState(true)
  const [error, setError]           = useState(null)
  const [draft, setDraft]           = useState('')
  const [requestKind, setRequestKind] = useState('existing_app')
  const [localError, setLocalError] = useState(null)
  const [saving, setSaving]         = useState(false)
  const { startWorkflow, starting, error: workflowError } = useWorkflowStart()

  const syncFromSummary = (payload) => {
    setSummary(payload)
    const req = payload?.build?.current_request || {}
    setDraft(req.text || '')
    setRequestKind(req.request_kind || payload?.app?.journey || 'existing_app')
  }

  useEffect(() => {
    let cancelled = false
    async function load() {
      try {
        const res = await fetch(`${API_BASE}/api/studio/build`)
        if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
        const payload = await res.json()
        if (!cancelled) { syncFromSummary(payload); setError(null) }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Studio Build could not be loaded.')
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    load()
    return () => { cancelled = true }
  }, [])

  const build     = summary?.build     || {}
  const app       = summary?.app       || {}
  const ai        = summary?.ai        || {}
  const studio    = summary?.studio    || {}
  const workspace = summary?.workspace || {}
  const currentPlan     = build.current_plan     || {}
  const recentRequests  = build.recent_requests  || []
  const examples        = build.request_examples || []
  const lastSavedAt     = formatTimestamp(build.last_saved_at)
  const busy            = saving || starting
  const combinedError   = localError || workflowError || null

  const refinementModes = useMemo(() => {
    const support = build.refinement_support || {}
    return Object.entries(REFINEMENT_COPY).map(([changeClass, copy]) => ({
      changeClass,
      ...copy,
      available:  support[changeClass]?.available  === true,
      workflowId: support[changeClass]?.workflow_id || null,
    }))
  }, [build.refinement_support])

  const persistBuildRequest = async ({ nextRequestKind, changeClass = null }) => {
    setLocalError(null)
    setSaving(true)
    try {
      const res = await fetch(`${API_BASE}/api/studio/build`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          request_text: draft,
          request_kind: nextRequestKind,
          ...(changeClass ? { change_class: changeClass } : {}),
        }),
      })
      if (!res.ok) {
        const body = await res.json().catch(() => ({ detail: res.statusText }))
        throw new Error(body.detail || 'Build draft could not be saved.')
      }
      const payload = await res.json()
      syncFromSummary(payload)
      return payload
    } catch (err) {
      setLocalError(err instanceof Error ? err.message : 'Build draft could not be saved.')
      return null
    } finally {
      setSaving(false)
    }
  }

  const handleSaveDraft = () => persistBuildRequest({ nextRequestKind: requestKind })

  const handleInitialBuild = async () => {
    setLocalError(null)
    if (requestKind === 'refinement') {
      setLocalError('Switch Build Mode to New App or Existing App for an initial build conversation.')
      return
    }
    if (!draft.trim()) {
      setLocalError('Enter a build request before starting.')
      return
    }
    if (!build.supports_initial_compile || !build.initial_compile_workflow) {
      setLocalError('Initial build workflows are not installed in this workspace yet.')
      return
    }
    const persisted = await persistBuildRequest({ nextRequestKind: requestKind })
    if (persisted) await startWorkflow(build.initial_compile_workflow, {}, { trigger_source: 'chat' })
  }

  const handleRefinement = async (changeClass, available) => {
    setLocalError(null)
    if (!draft.trim()) { setLocalError('Enter a request before launching a refinement run.'); return }
    if (!available)    { setLocalError("This refinement path's workflow is not installed."); return }
    const persisted = await persistBuildRequest({ nextRequestKind: 'refinement', changeClass })
    if (persisted) {
      await startWorkflow(null, {}, {
        trigger_source: 'refinement',
        change_class: changeClass,
        artifact_kind: 'app_bundle',
        raw_user_request: draft.trim(),
      })
    }
  }

  if (loading) return <StudioLoadingState label="Loading Studio Build…" />
  if (error || !summary) return <StudioErrorState title="Studio Build Unavailable" message={error || 'No summary returned.'} />

  return (
    <AdminWorkspaceLayout>
      <div className="flex flex-col gap-6">

        {/* ── Main build card ────────────────────────────────────────────── */}
        <SurfaceCard title="Build" eyebrow="Studio Control Plane" accent>
          <div className="grid gap-6 lg:grid-cols-[minmax(0,1.45fr)_minmax(320px,0.9fr)]">

            <div>
              <div className="flex flex-wrap items-center gap-2">
                <StatusPill tone="primary">
                  {app.journey === 'existing_app' ? 'Existing App' : app.journey === 'new_app' ? 'New App' : 'Not Configured'}
                </StatusPill>
                <StatusPill tone="success">{studio.local_only ? 'Local Only' : 'Shared Surface'}</StatusPill>
                <StatusPill tone={build.supports_initial_compile ? 'success' : 'warning'}>
                  {build.supports_initial_compile ? 'Compile Ready' : 'Compile Workflows Missing'}
                </StatusPill>
              </div>
              <p className="mt-4 max-w-3xl text-sm leading-7 text-muted-foreground">
                Shape your next build request and launch the correct workflow path directly from here.
              </p>

              <div className="mt-5">
                <BuildRequestForm
                  draft={draft}
                  setDraft={setDraft}
                  requestKind={requestKind}
                  setRequestKind={setRequestKind}
                  examples={examples}
                  busy={busy}
                  saving={saving}
                  starting={starting}
                  lastSavedAt={lastSavedAt}
                  draftHandoffNote={build.draft_handoff_note}
                  combinedError={combinedError}
                  onSave={handleSaveDraft}
                  onInitialBuild={handleInitialBuild}
                  supportsInitialCompile={build.supports_initial_compile}
                />
              </div>
            </div>

            <BuildStatePanel build={build} ai={ai} workspace={workspace} />
          </div>
        </SurfaceCard>

        {/* ── Refinement + History ───────────────────────────────────────── */}
        <div className="grid gap-6 xl:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)]">

          <SurfaceCard title="Refinement Paths" eyebrow="Routed Re-Entry">
            <div className="grid gap-4 md:grid-cols-2">
              {refinementModes.map((mode) => (
                <RefinementCard
                  key={mode.changeClass}
                  mode={mode}
                  busy={busy}
                  starting={starting}
                  onRefinement={handleRefinement}
                />
              ))}
            </div>
          </SurfaceCard>

          <BuildHistory currentPlan={currentPlan} recentRequests={recentRequests} />

        </div>
      </div>
    </AdminWorkspaceLayout>
  )
}
