import { useEffect, useMemo, useState } from 'react'

import { useWorkflowStart } from '@mozaiks/chat-ui/hooks/useWorkflowStart.js'
import { AdminWorkspaceLayout } from '@mozaiks/chat-ui/admin/components/AdminWorkspaceLayout.jsx'
import {
  API_BASE,
  StatusPill,
  SurfaceCard,
  Metric,
  ActionButton,
  StudioSlideOver,
  StudioLoadingState,
  StudioErrorState,
} from './studio/StudioPrimitives.jsx'
import { RefinementControls } from './studio/RefinementControls.jsx'
import { buildRefinementTriggerPayload } from './studio/refinement.js'


const REQUEST_KIND_OPTIONS = [
  { value: 'greenfield_app', label: 'Greenfield App' },
  { value: 'brownfield_app', label: 'Brownfield App' },
  { value: 'refinement', label: 'Refinement' },
]

const REFINEMENT_COPY = {
  patch: { title: 'Patch', description: 'Localized fixes or targeted corrections against the current app bundle.' },
  design: { title: 'Design', description: 'Brand, layout, navigation, or UI-schema changes within the same concept.' },
  feature: { title: 'Feature', description: 'A new capability added inside the current product direction.' },
  core: { title: 'Core', description: 'A foundational reset that changes the value proposition or product identity.' },
}

function formatTimestamp(value) {
  if (!value) return 'Not saved yet'
  const d = new Date(value)
  return Number.isNaN(d.getTime()) ? value : d.toLocaleString()
}

function CreateRequestForm({
  draft,
  setDraft,
  requestKind,
  setRequestKind,
  validationStrategy,
  setValidationStrategy,
  validationOptions,
  validationDefaultReason,
  examples,
  busy,
  saving,
  starting,
  lastSavedAt,
  draftHandoffNote,
  combinedError,
  onSave,
  onInitialCreate,
  supportsInitialCompile,
}) {
  const selectedValidationOption = validationOptions.find((opt) => opt.value === validationStrategy) || null
  return (
    <div className="rounded-3xl border border-border bg-background/70 p-4">
      <div className="grid gap-4 md:grid-cols-[220px_minmax(0,1fr)]">
        <div>
          <label className="block text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground" htmlFor="studio-create-kind">
            Request Mode
          </label>
          <select
            id="studio-create-kind"
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
          <label className="block text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground" htmlFor="studio-create-draft">
            Request Brief
          </label>
          <textarea
            id="studio-create-draft"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="Describe what you want Mozaiks to create or refine..."
            className="mt-3 min-h-40 w-full rounded-2xl border border-border bg-card px-4 py-3 text-sm leading-7 text-foreground outline-none transition focus:border-primary/40 focus:ring-2 focus:ring-primary/20"
          />
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

      <div className="mt-4 grid gap-4 md:grid-cols-[220px_minmax(0,1fr)]">
        <div>
          <label className="block text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground" htmlFor="studio-create-validation">
            App Validation
          </label>
          <select
            id="studio-create-validation"
            value={validationStrategy || ''}
            onChange={(e) => setValidationStrategy(e.target.value)}
            className="mt-3 w-full rounded-2xl border border-border bg-card px-4 py-3 text-sm text-foreground outline-none transition focus:border-primary/40 focus:ring-2 focus:ring-primary/20"
          >
            {validationOptions.map((opt) => (
              <option key={opt.value} value={opt.value}>{opt.label}</option>
            ))}
          </select>
        </div>
        <div className="flex flex-col justify-end">
          <div className="rounded-2xl border border-border bg-background/60 px-4 py-3 text-sm text-muted-foreground">
            {selectedValidationOption?.description || validationDefaultReason || 'Select how generated app validation should run for this request.'}
          </div>
        </div>
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-3">
        <ActionButton onClick={onSave} disabled={busy || !draft.trim()} variant="secondary">
          {saving ? 'Saving...' : 'Save Draft'}
        </ActionButton>
        <ActionButton onClick={onInitialCreate} disabled={busy || !supportsInitialCompile}>
          {starting ? 'Starting...' : 'Start Create Conversation'}
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

function CreateStatePanel({ createData, ai, workspace }) {
  const generatorWorkflows = Object.entries(createData.generator_workflows || {})

  return (
    <div className="rounded-3xl border border-border bg-background/75 p-5">
      <div className="text-xs font-semibold uppercase tracking-[0.2em] text-muted-foreground">Current Create State</div>
      <div className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-1 xl:grid-cols-2">
        <Metric label="Plan" value={createData.plan_state || 'none'} detail="Current persisted request plan" />
        <Metric label="Approval" value={createData.approval_state || 'none'} detail="Local until plan review exists" />
        <Metric label="Provider" value={ai.provider || 'Not set'} detail={ai.model || 'Choose a model'} />
        <Metric label="Workflows" value={workspace.workflow_count ?? 0} detail={`${(createData.available_workflows || []).length} declared`} />
      </div>

      <dl className="mt-4 space-y-2 text-sm text-muted-foreground">
        <div><span className="font-semibold text-foreground">Request kind:</span> {createData.current_request?.request_kind || '-'} </div>
        <div><span className="font-semibold text-foreground">Change class:</span> {createData.current_request?.change_class || '-'} </div>
      </dl>

      {generatorWorkflows.length > 0 && (
        <div className="mt-4">
          <div className="mb-2 text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">Generator Workflows</div>
          <div className="space-y-1.5">
            {generatorWorkflows.map(([id, available]) => (
              <div key={id} className="flex items-center justify-between rounded-xl border border-border bg-background/70 px-3 py-2">
                <span className="font-mono text-xs text-muted-foreground">{id}</span>
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

function RefinementSummaryCard({ selectedMode, readyCount, busy, onOpen }) {
  return (
    <SurfaceCard title="Refinement Paths" eyebrow="Routed Re-Entry">
      <div className="space-y-4">
        <p className="text-sm leading-7 text-muted-foreground">
          Keep the main create surface focused on the request brief. Mozaiks classifies refinement requests automatically and reopens the right workflow; optional route hints stay available when you want to bias the result.
        </p>
        <div className="flex flex-wrap gap-2">
          <StatusPill tone={selectedMode ? (selectedMode.isDestructive ? 'destructive' : 'primary') : 'default'}>
            {selectedMode ? `${selectedMode.label || selectedMode.title} selected` : 'No path selected'}
          </StatusPill>
          <StatusPill tone={readyCount > 0 ? 'success' : 'warning'}>{readyCount} ready paths</StatusPill>
        </div>
        <div className="rounded-2xl border border-border bg-background/70 p-4">
          <div className="text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">Current Selection</div>
          <p className="mt-2 text-sm font-semibold text-foreground">
            {selectedMode ? (selectedMode.label || selectedMode.title) : 'Automatic classification'}
          </p>
          <p className="mt-2 text-sm leading-6 text-muted-foreground">
            {selectedMode
              ? selectedMode.description
              : 'Launch refinement without selecting a route hint and the backend classifier will determine whether this is a patch, design change, feature request, or core reset.'}
          </p>
        </div>
        <div className="flex flex-wrap gap-3">
          <ActionButton onClick={onOpen} variant="secondary">
            {selectedMode ? 'Edit route hint' : 'Open refinement controls'}
          </ActionButton>
          <div className="self-center text-sm text-muted-foreground">
            Uses the current Request Brief from the Create panel above.
          </div>
        </div>
        {busy && (
          <div className="text-sm text-muted-foreground">A workflow action is already in progress.</div>
        )}
      </div>
    </SurfaceCard>
  )
}

function ArtifactVersionRow({ version, onRevert, reverting }) {
  const isCurrent = version.lifecycle_status === 'current'
  const hasPath = Boolean(version.commit_metadata?.metadata?.artifact_path)

  return (
    <div className={`rounded-2xl border px-4 py-3 text-sm ${isCurrent ? 'border-primary/30 bg-primary/5' : 'border-border bg-background/70'}`}>
      <div className="flex items-center justify-between gap-2">
        <span className="font-semibold text-foreground">{version.artifact_key}</span>
        {isCurrent && (
          <span className="rounded-lg border border-primary/30 bg-primary/10 px-2 py-0.5 text-xs font-semibold text-primary">Active</span>
        )}
      </div>
      <div className="mt-1 text-xs text-muted-foreground">
        v{version.version_number} · {formatTimestamp(version.created_at)}
      </div>
      {!isCurrent && (
        <div className="mt-3">
          <ActionButton onClick={() => onRevert(version.id)} disabled={reverting || !hasPath} variant="secondary">
            {reverting ? 'Reverting...' : hasPath ? 'Revert to this' : 'No file stored'}
          </ActionButton>
        </div>
      )}
    </div>
  )
}

function CreateHistory({ currentPlan, recentRequests, history, onRevert, reverting, revertResult }) {
  const hasPlan = currentPlan.summary || currentPlan.owned_paths?.length || currentPlan.acceptance_criteria?.length
  const hasRequests = recentRequests.length > 0
  const versions = history?.artifact_versions || []

  return (
    <SurfaceCard title="Create History" eyebrow="Saved State">
      <div className="space-y-5">
        {revertResult && (
          <div className={`rounded-2xl border px-4 py-3 text-sm ${revertResult.error ? 'border-destructive/30 bg-destructive/10 text-destructive' : 'border-success/30 bg-success/10 text-success'}`}>
            {revertResult.error
              ? `Revert failed: ${revertResult.error}`
              : `Reverted to ${revertResult.artifact_key} v${revertResult.artifact_version_id?.slice(-6)}. Restart the server to apply.`
            }
          </div>
        )}

        <div>
          <div className="mb-2 text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">
            Generated Versions
            {versions.length > 0 && <span className="ml-2 font-mono text-primary">{versions.length}</span>}
          </div>
          {versions.length > 0 ? (
            <div className="space-y-2">
              {versions.map((v) => (
                <ArtifactVersionRow
                  key={v.id}
                  version={v}
                  onRevert={onRevert}
                  reverting={reverting === v.id}
                />
              ))}
            </div>
          ) : (
            <div className="rounded-2xl border border-border bg-background/70 px-4 py-3 text-sm text-muted-foreground">
              No generated versions yet. Run a create flow to create the first restorable state.
            </div>
          )}
        </div>

        {hasPlan && (
          <div>
            <div className="mb-2 text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">Current Plan</div>
            <div className="rounded-2xl border border-border bg-background/70 p-4 text-sm leading-7 text-muted-foreground">
              {currentPlan.summary || '-'}
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

        <div>
          <div className="mb-2 text-xs font-semibold uppercase tracking-[0.18em] text-muted-foreground">Recent Draft Requests</div>
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
              No draft request history yet.
            </div>
          )}
        </div>
      </div>
    </SurfaceCard>
  )
}

export default function StudioCreatePage() {
  const [summary, setSummary] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [draft, setDraft] = useState('')
  const [requestKind, setRequestKind] = useState('greenfield_app')
  const [localError, setLocalError] = useState(null)
  const [saving, setSaving] = useState(false)
  const [history, setHistory] = useState(null)
  const [reverting, setReverting] = useState(null)
  const [revertResult, setRevertResult] = useState(null)
  const [selectedRefinementClass, setSelectedRefinementClass] = useState(null)
  const [selectedValidationStrategy, setSelectedValidationStrategy] = useState(null)
  const [refinementOverlayOpen, setRefinementOverlayOpen] = useState(false)
  const { startWorkflow, starting, error: workflowError } = useWorkflowStart()

  const syncFromSummary = (payload) => {
    setSummary(payload)
    const req = payload?.create?.current_request || {}
    setDraft(req.text || '')
    setRequestKind(req.request_kind || payload?.app?.journey || 'greenfield_app')
    setSelectedRefinementClass(req.change_class || null)
  }

  useEffect(() => {
    let cancelled = false
    async function load() {
      try {
        const res = await fetch(`${API_BASE}/api/studio/create`)
        if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
        const payload = await res.json()
        if (!cancelled) { syncFromSummary(payload); setError(null) }
      } catch (err) {
        if (!cancelled) setError(err instanceof Error ? err.message : 'Studio Create could not be loaded.')
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    load()
    return () => { cancelled = true }
  }, [])

  useEffect(() => {
    let cancelled = false
    async function loadHistory() {
      try {
        const res = await fetch(`${API_BASE}/api/studio/history?limit=10`)
        if (res.ok) {
          const payload = await res.json()
          if (!cancelled) setHistory(payload)
        }
      } catch {
      }
    }
    loadHistory()
    return () => { cancelled = true }
  }, [])

  const createData = summary?.create || {}
  const app = summary?.app || {}
  const ai = summary?.ai || {}
  const studio = summary?.studio || {}
  const workspace = summary?.workspace || {}
  const currentPlan = createData.current_plan || {}
  const recentRequests = createData.recent_requests || []
  const examples = createData.request_examples || []
  const validationOptions = createData.app_validation?.options || []
  const validationDefaultStrategy = createData.app_validation?.default_value || null
  const validationDefaultReason = createData.app_validation?.default_reason || null
  const lastSavedAt = formatTimestamp(createData.last_saved_at)
  const busy = saving || starting
  const combinedError = localError || workflowError || null

  useEffect(() => {
    if (!validationOptions.length) return
    setSelectedValidationStrategy((prev) => {
      if (prev && validationOptions.some((opt) => opt.value === prev)) return prev
      return validationDefaultStrategy || validationOptions[0]?.value || null
    })
  }, [validationDefaultStrategy, validationOptions])

  const refinementModes = useMemo(() => {
    const support = createData.refinement_support || {}
    return Object.entries(REFINEMENT_COPY).map(([changeClass, copy]) => ({
      changeClass,
      ...copy,
      available: support[changeClass]?.available === true,
      workflowId: support[changeClass]?.workflow_id || null,
    }))
  }, [createData.refinement_support])
  const selectedRefinementMode = refinementModes.find((entry) => entry.changeClass === selectedRefinementClass) || null
  const readyRefinementCount = refinementModes.filter((entry) => entry.available).length

  const persistCreateRequest = async ({ nextRequestKind, changeClass = null }) => {
    setLocalError(null)
    setSaving(true)
    try {
      const res = await fetch(`${API_BASE}/api/studio/create`, {
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
        throw new Error(body.detail || 'Create draft could not be saved.')
      }
      const payload = await res.json()
      syncFromSummary(payload)
      return payload
    } catch (err) {
      setLocalError(err instanceof Error ? err.message : 'Create draft could not be saved.')
      return null
    } finally {
      setSaving(false)
    }
  }

  const handleSaveDraft = () => persistCreateRequest({ nextRequestKind: requestKind })

  const handleInitialCreate = async () => {
    setLocalError(null)
    if (requestKind === 'refinement') {
      setLocalError('Switch Request Mode to Greenfield App or Brownfield App for a create conversation.')
      return
    }
    if (!draft.trim()) {
      setLocalError('Enter a create request before starting.')
      return
    }
    if (!createData.supports_initial_compile || !createData.initial_compile_workflow) {
      setLocalError('Create workflows are not installed in this workspace yet.')
      return
    }
    const persisted = await persistCreateRequest({ nextRequestKind: requestKind })
    if (persisted) {
      await startWorkflow(
        createData.initial_compile_workflow,
        selectedValidationStrategy ? { app_validation_strategy: selectedValidationStrategy } : {},
        { trigger_source: 'action', action_id: 'studio_create' }
      )
    }
  }

  const handleRevert = async (artifactVersionId) => {
    setReverting(artifactVersionId)
    setRevertResult(null)
    try {
      const res = await fetch(`${API_BASE}/api/studio/revert`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ artifact_version_id: artifactVersionId }),
      })
      const body = await res.json().catch(() => ({ detail: res.statusText }))
      if (!res.ok) throw new Error(body.detail || 'Revert failed.')
      setRevertResult({ artifact_version_id: artifactVersionId, artifact_key: body.artifact_key })
    } catch (err) {
      setRevertResult({ error: err instanceof Error ? err.message : 'Revert failed.' })
    } finally {
      setReverting(null)
    }
  }

  const handleRefinement = async (changeClass = null) => {
    setLocalError(null)
    if (!draft.trim()) { setLocalError('Enter a request before launching a refinement run.'); return false }
    const mode = refinementModes.find((entry) => entry.changeClass === changeClass) || null
    if (mode && !mode.available) { setLocalError("This refinement path's workflow is not installed."); return false }
    const persisted = await persistCreateRequest({ nextRequestKind: 'refinement', changeClass })
    if (persisted) {
      try {
        await startWorkflow(null, selectedValidationStrategy ? { app_validation_strategy: selectedValidationStrategy } : {}, {
          trigger_source: 'refinement',
          trigger_payload: buildRefinementTriggerPayload({
            changeClass,
            artifactKind: 'app_bundle',
            artifactKey: 'app_bundle',
            rawUserRequest: draft,
            sourceSurface: 'studio_create',
          }),
        })
        return true
      } catch (err) {
        setLocalError(err instanceof Error ? err.message : 'Refinement could not be started.')
      }
    }
    return false
  }

  const handleRefinementFromOverlay = async (changeClass) => {
    const started = await handleRefinement(changeClass)
    if (started) setRefinementOverlayOpen(false)
  }

  if (loading) return <StudioLoadingState label="Loading Studio Create..." />
  if (error || !summary) return <StudioErrorState title="Studio Create Unavailable" message={error || 'No summary returned.'} />

  return (
    <AdminWorkspaceLayout>
      <div className="flex flex-col gap-6">
        <SurfaceCard title="Create" eyebrow="Studio Control Plane" accent>
          <div className="grid gap-6 lg:grid-cols-[minmax(0,1.45fr)_minmax(320px,0.9fr)]">
            <div>
              <div className="flex flex-wrap items-center gap-2">
                <StatusPill tone="primary">
                  {app.journey === 'brownfield_app' ? 'Brownfield App' : app.journey === 'greenfield_app' ? 'Greenfield App' : 'Not Configured'}
                </StatusPill>
                <StatusPill tone="success">{studio.local_only ? 'Local Only' : 'Shared Surface'}</StatusPill>
                <StatusPill tone={createData.supports_initial_compile ? 'success' : 'warning'}>
                  {createData.supports_initial_compile ? 'Create Ready' : 'Create Workflows Missing'}
                </StatusPill>
              </div>
              <p className="mt-4 max-w-3xl text-sm leading-7 text-muted-foreground">
                Shape the next request and route it into the right create or refinement workflow path from here.
              </p>

              <div className="mt-5">
                <CreateRequestForm
                  draft={draft}
                  setDraft={setDraft}
                  requestKind={requestKind}
                  setRequestKind={setRequestKind}
                  validationStrategy={selectedValidationStrategy}
                  setValidationStrategy={setSelectedValidationStrategy}
                  validationOptions={validationOptions}
                  validationDefaultReason={validationDefaultReason}
                  examples={examples}
                  busy={busy}
                  saving={saving}
                  starting={starting}
                  lastSavedAt={lastSavedAt}
                  draftHandoffNote={createData.draft_handoff_note}
                  combinedError={combinedError}
                  onSave={handleSaveDraft}
                  onInitialCreate={handleInitialCreate}
                  supportsInitialCompile={createData.supports_initial_compile}
                />
              </div>
            </div>

            <CreateStatePanel createData={createData} ai={ai} workspace={workspace} />
          </div>
        </SurfaceCard>

        <div className="grid gap-6 xl:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)]">
          <RefinementSummaryCard
            selectedMode={selectedRefinementMode}
            readyCount={readyRefinementCount}
            busy={busy}
            onOpen={() => setRefinementOverlayOpen(true)}
          />

          <CreateHistory
            currentPlan={currentPlan}
            recentRequests={recentRequests}
            history={history}
            onRevert={handleRevert}
            reverting={reverting}
            revertResult={revertResult}
          />
        </div>

        <StudioSlideOver
          open={refinementOverlayOpen}
          title="Refinement Paths"
          description="Apply refinement using the current Request Brief from the Create panel. Mozaiks classifies the request automatically; route hints below are optional."
          onClose={() => setRefinementOverlayOpen(false)}
        >
          <RefinementControls
            modes={refinementModes}
            selectedClass={selectedRefinementClass}
            onSelectClass={setSelectedRefinementClass}
            request={draft}
            onRequestChange={setDraft}
            onSubmit={handleRefinementFromOverlay}
            onDismiss={() => setRefinementOverlayOpen(false)}
            busy={busy}
            error={combinedError}
            showRequestInput={false}
            showHeader={false}
            surface="plain"
            helperText="The Apply refinement action uses the current Request Brief from the Create panel above. Leave the route hint unset if you want the backend classifier to decide the scope on its own."
            submitLabel="Apply refinement"
          />
        </StudioSlideOver>
      </div>
    </AdminWorkspaceLayout>
  )
}
