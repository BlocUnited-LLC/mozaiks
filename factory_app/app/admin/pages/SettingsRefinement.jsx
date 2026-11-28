import { useEffect, useRef, useState } from 'react'
import { useWorkflowStart } from '@mozaiks/chat-ui/hooks/useWorkflowStart.js'
import HarnessDecisionCard from '../../ui/components/HarnessDecisionCard.jsx'
import { ActionButton, LinkButton } from '../../ui/components/StudioShared.jsx'
import RefinementControls from './RefinementControls.jsx'
import { studioFetch } from './studioApi.js'

export function SettingsResume({ resume, appId }) {
  const [state, setState] = useState({})
  const controller = useRef(null)
  useEffect(() => {
    const request = new AbortController()
    controller.current = request
    setState({})
    return () => request.abort()
  }, [appId, resume?.chat_id])
  if (!resume) return null
  const query = new URLSearchParams({ mode: 'workflow', workflow: resume.workflow_id, chat_id: resume.chat_id, app_id: resume.app_id })
  async function pause() {
    const request = controller.current
    if (state.busy || !request || request.signal.aborted) return
    setState({ busy: true })
    try {
      const response = await studioFetch('/api/studio/apps/' + encodeURIComponent(appId) + '/refinement/pause', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ chat_id: resume.chat_id, kind: resume.kind }), signal: request.signal,
      })
      const body = await response.json()
      if (!response.ok || body.status !== 'paused' || body.chat_id !== resume.chat_id) {
        throw new Error(typeof body.detail === 'string' ? body.detail : 'The edit could not be confirmed paused.')
      }
      if (controller.current === request && !request.signal.aborted) setState({ paused: true })
    } catch (error) {
      if (controller.current === request && !request.signal.aborted) setState({ error: error.message })
    }
  }
  return <div className="space-y-3">
    <p className="text-sm text-muted-foreground">{state.paused ? 'Edit paused. Resume its saved session when ready.' : 'An edit is in progress for this app. Resume its saved session to continue or review the result.'}</p>
    <div className="flex flex-wrap gap-3">
      <LinkButton to={'/chat?' + query}>Resume edit</LinkButton>
      {resume.kind && !state.paused && <ActionButton variant="outline" disabled={state.busy} onClick={pause}>{state.busy ? 'Pausing edit...' : 'Pause edit'}</ActionButton>}
      <LinkButton to={'/apps/' + encodeURIComponent(appId) + '/activity'} variant="outline">Review saved builds</LinkButton>
    </div>
    {state.error && <p role="alert">{state.error}</p>}
  </div>
}

export default function SettingsRefinement({ descriptor, appId }) {
  const { startWorkflow, starting, error: startError } = useWorkflowStart()
  const [open, setOpen] = useState(false)
  const [request, setRequest] = useState('')
  const [pending, setPending] = useState(null)
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)
  const [cancelling, setCancelling] = useState(false)
  const [stateReady, setStateReady] = useState(false)
  const [otherPending, setOtherPending] = useState(null)
  const active = useRef(null)
  const key = JSON.stringify(descriptor?.selection)
  const selection = descriptor?.selection
  const label = selection?.kind === 'branding' ? 'branding' : 'subscription'

  useEffect(() => {
    const controller = new AbortController()
    active.current = controller
    setOpen(false); setPending(null); setResult(null); setError(null); setRequest(''); setCancelling(false); setStateReady(false); setOtherPending(null)
    if (selection) {
      studioFetch('/api/session/state?build_registry_id=' + encodeURIComponent(selection.build_registry_id), { signal: controller.signal })
        .then(async response => {
          if (!response.ok) throw new Error('The saved edit state is unavailable. Reload these settings before starting an edit.')
          const state = (await response.json()).session_state
          if (controller.signal.aborted) return
          const proposal = state?.pending_harness_decision
          if (proposal) {
            const savedSelection = proposal.metadata?.settings_selection
            if (savedSelection?.build_registry_id === selection.build_registry_id && savedSelection.kind === selection.kind) {
              setPending(proposal)
              setRequest(proposal.trigger_payload?.refinement_request?.raw_user_request || '')
              setOpen(true)
              setStateReady(true)
            } else {
              const suffix = savedSelection?.kind === 'branding' ? 'branding' : savedSelection?.kind === 'subscription' ? 'billing' : 'building'
              setOtherPending('/apps/' + encodeURIComponent(appId) + '/' + suffix)
            }
          } else setStateReady(true)
        }).catch(err => { if (!controller.signal.aborted) setError(err.message) })
    }
    return () => controller.abort()
  }, [key])

  if (!descriptor || !selection) return null
  const busy = starting || cancelling
  const pendingSelection = pending?.metadata?.settings_selection
  const currentProposal = !pending || JSON.stringify(pendingSelection) === key

  async function submit(action = null) {
    const controller = active.current
    const isCurrent = () => active.current === controller && !controller.signal.aborted
    if (busy || !stateReady || !request.trim() || !controller || !isCurrent()) return
    setError(null)
    const triggerPayload = action ? {
      ...pending.trigger_payload,
      harness_action: { action_id: action.action_id },
      change_request_id: pending.change_request_id,
      revision_id: pending.revision_id,
    } : {
      refinement_request: {
        artifact_kind: descriptor.artifact_kind, artifact_key: descriptor.artifact_key,
        artifact_version_id: selection.artifact_version_id, raw_user_request: request.trim(),
        source_surface: selection.kind + '_settings',
      },
      ...(selection.kind === 'branding' ? { coding_request: {} } : {}),
    }
    try {
      const response = await startWorkflow(null, {}, {
        trigger_source: 'refinement', app_id: descriptor.app_id,
        build_registry_id: selection.build_registry_id, settings_selection: selection,
        trigger_payload: triggerPayload, signal: controller.signal,
      })
      if (!response || !isCurrent()) return
      if (response.execution_mode === 'harness_decision') {
        // The persisted decision owns confirmation and atomic cancellation.
        const saved = await studioFetch('/api/session/state?build_registry_id=' + encodeURIComponent(selection.build_registry_id), { signal: controller.signal })
        if (!saved.ok) throw new Error('The proposed edit could not be reloaded. Reload these settings.')
        const proposal = (await saved.json()).session_state?.pending_harness_decision
        if (!isCurrent()) return
        if (proposal?.metadata?.settings_selection?.build_registry_id !== selection.build_registry_id
            || proposal.metadata.settings_selection.kind !== selection.kind) {
          throw new Error('The proposed edit was not saved for these settings. Reload before continuing.')
        }
        setPending(proposal)
      } else if (!response.chat_id) {
        setPending(null); setResult(response)
      }
    } catch (err) { if (isCurrent()) setError(err.message) }
  }

  async function cancel() {
    const controller = active.current
    const isCurrent = () => active.current === controller && !controller.signal.aborted
    if (busy || !controller || !isCurrent()) return
    setError(null)
    if (!pending) { setOpen(false); setRequest(''); return }
    setCancelling(true)
    try {
      const response = await studioFetch('/api/session/decisions/resolve', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ decision_id: pending.decision_id, accepted: false, build_registry_id: selection.build_registry_id }),
        signal: controller.signal,
      })
      if (!response.ok) throw new Error('The proposal could not be cancelled. Reload these settings to check its state.')
      if (isCurrent()) { setPending(null); setOpen(false); setRequest('') }
    } catch (err) { if (isCurrent()) setError(err.message) }
    finally { if (isCurrent()) setCancelling(false) }
  }

  const decision = pending ? { ...pending, actions: currentProposal ? (pending.actions || []).filter(action =>
    ['confirm_recommended_workflow', 'run_recommended_workflow', 'apply_proposed_scope'].includes(action.action_id)) : [] } : null
  return <div className="space-y-4">
    {otherPending && <div className="space-y-2"><p role="status">Another edit for this app is awaiting a decision.</p><LinkButton to={otherPending}>Open pending edit</LinkButton></div>}
    {!otherPending && !open && !result && <ActionButton variant="outline" className="rounded-full" disabled={!stateReady} onClick={() => setOpen(true)}>Edit {label}</ActionButton>}
    {open && !pending && !result && <RefinementControls modes={[]} request={request} onRequestChange={setRequest}
      title={'Edit ' + label} description="Describe the change to this app. The existing refinement flow stages a saved candidate for review."
      helperText="Review the result in its saved build session before accepting it."
      onSubmit={() => submit()} onDismiss={busy ? null : cancel} busy={busy} error={error || startError}
      submitLabel="Prepare edit" />}
    {pending && <>
      {!currentProposal && <p role="status" className="text-sm text-warning">This proposal uses an earlier build. Cancel it, then reload these settings to select the current build.</p>}
      <HarnessDecisionCard decision={decision} busy={busy} error={error || startError} onAction={submit} />
      <ActionButton variant="secondary" disabled={busy} onClick={cancel}>Cancel edit</ActionButton>
    </>}
    {result && <div className="space-y-3">
      <p role="status">{result.coding_worker?.metadata?.build_record_id || result.surface_result?.metadata?.build_record_id
        ? 'A result was saved. Open build review to check its validation and review state.'
        : 'The edit has no confirmed saved candidate. Open saved builds to check its result.'}</p>
      <LinkButton to={'/apps/' + encodeURIComponent(appId) + '/activity'}>Review saved builds</LinkButton>
    </div>}
    {!open && error && <p role="alert" className="text-sm text-destructive">{error}</p>}
  </div>
}
