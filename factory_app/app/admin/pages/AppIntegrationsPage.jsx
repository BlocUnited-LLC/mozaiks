import { useEffect, useId, useState } from 'react'
import { useParams } from 'react-router-dom'

import { PageHeader } from '@mozaiks/chat-ui/ui'
import { WorkspaceLayout } from '@mozaiks/chat-ui/workspace'
import {
  ActionButton, Panel, StatusPill, StudioErrorState, StudioInlineEmptyState,
  StudioLoadingState, StudioSlideOver,
} from '../../ui/components/StudioShared.jsx'
import { studioFetch, studioModuleAction } from './studioApi.js'

const fieldStyle = 'mt-1 block w-full rounded-md border border-border bg-background px-3 py-2 text-foreground'

async function requestConnectors(appId, { method = 'GET', service, check = false, body, signal } = {}) {
  const suffix = service ? '/' + encodeURIComponent(service) : ''
  const path = '/api/studio/integrations/connectors' + suffix + (check ? '/health-check' : '')
  const query = new URLSearchParams({ dashboard_app_id: appId })
  const response = await studioFetch(path + '?' + query, {
    method, signal,
    ...(body && { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }),
  })
  if (!response.ok) {
    if (response.status === 403 || response.status === 404) throw new Error('Integration or app unavailable to this account.')
    if (response.status === 503) throw new Error('The credential store is unavailable. Try again after it is configured.')
    throw new Error(`Integration request failed (HTTP ${response.status}).`)
  }
  return response.json()
}

function AdapterEditor({ connector, editing, catalog, credentialStorageAvailable, busy, error, onClose, onSave, onDelete }) {
  const formId = useId()
  const [service, setService] = useState(connector.service || '')
  const [name, setName] = useState(connector.display_name || '')
  const [notes, setNotes] = useState(connector.notes || '')
  const [credential, setCredential] = useState('')
  const footer = (
    <div className="flex flex-wrap justify-end gap-2">
      {editing && <ActionButton variant="destructive" disabled={busy} onClick={onDelete}>Delete integration</ActionButton>}
      <ActionButton variant="secondary" disabled={busy} onClick={onClose}>Cancel</ActionButton>
      <ActionButton form={formId} type="submit" disabled={busy}>{busy ? 'Saving…' : 'Save integration'}</ActionButton>
    </div>
  )
  return (
    <StudioSlideOver open title={editing ? 'Edit integration' : 'Add integration'} onClose={onClose} footer={footer}>
      <form id={formId} className="space-y-4" onSubmit={event => {
        event.preventDefault()
        const serviceId = service.trim().toLowerCase()
        const displayName = name.trim() || catalog.find(item => item.id === serviceId)?.name || serviceId
        onSave(serviceId, { display_name: displayName, notes: notes.trim(), ...(credential && { secret_value: credential }) })
      }}>
        <label className="block text-sm">Service ID
          <input className={fieldStyle} required maxLength={128} pattern="(?!\.$)(?!.*\.\.)[A-Za-z0-9_.\-]+" value={service}
            disabled={editing || busy} list={formId + '-services'} onChange={event => setService(event.target.value)}
            title="Use letters, numbers, underscores, hyphens or dots. A single dot or consecutive dots are invalid."
            placeholder="e.g. analytics_provider" />
        </label>
        <datalist id={formId + '-services'}>{catalog.map(item => <option key={item.id} value={item.id}>{item.name}</option>)}</datalist>
        <label className="block text-sm">Display name
          <input className={fieldStyle} value={name} disabled={busy} onChange={event => setName(event.target.value)} />
        </label>
        <div className="text-sm">
          <label htmlFor={formId + '-notes'}>Notes</label>
          <textarea id={formId + '-notes'} className={fieldStyle} value={notes} disabled={busy}
            onChange={event => setNotes(event.target.value)} />
        </div>
        {credentialStorageAvailable ? (
          <div className="text-sm">
            <label htmlFor={formId + '-credential'}>API key or secret</label>
            <input id={formId + '-credential'} aria-describedby={formId + '-credential-hint'}
              className={fieldStyle} type="password" autoComplete="new-password" value={credential}
              disabled={busy} onChange={event => setCredential(event.target.value)} />
            <p id={formId + '-credential-hint'} className="text-xs text-muted-foreground">{editing ? 'Leave blank to keep the saved credential.' : 'Optional for a connection record.'}</p>
          </div>
        ) : <p className="text-sm text-muted-foreground">Credential storage is unavailable. You can still save connection details.</p>}
        {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
      </form>
    </StudioSlideOver>
  )
}

function SelectedAppIntegrations({ appId }) {
  const [data, setData] = useState(null)
  const [loadError, setLoadError] = useState('')
  const [actionError, setActionError] = useState('')
  const [editor, setEditor] = useState(null)
  const [busy, setBusy] = useState(false)
  const [revision, setRevision] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    requestConnectors(appId, { signal: controller.signal })
      .then(result => {
        if (controller.signal.aborted) return
        setData(result)
        setLoadError('')
      })
      .catch(error => {
        if (!controller.signal.aborted) setLoadError(error.message)
      })
    return () => controller.abort()
  }, [appId, revision])

  async function update(action) {
    if (busy) return
    setBusy(true)
    setActionError('')
    try {
      await action()
      setEditor(null)
      setRevision(value => value + 1)
    } catch (error) {
      setActionError(error.message)
    } finally {
      setBusy(false)
    }
  }
  function closeEditor() {
    if (busy) return
    setEditor(null)
    setActionError('')
  }
  function openEditor(record, editing = false) {
    setActionError('')
    setEditor({ record, editing })
  }
  if (loadError) return <StudioErrorState title="Integrations unavailable" message={loadError} />
  if (!data) return <StudioLoadingState label="Loading integrations…" />
  const { connectors, declarations, catalog } = data
  return (
    <div className="space-y-6">
      <PageHeader title="Integrations" subtitle="Manage service connections for this app."
        actions={[{ id: 'add-integration', label: 'Add integration' }]} onAction={() => openEditor({})} />
      {actionError && !editor && <p role="alert" className="text-destructive">{actionError}</p>}
      <Panel title="App connections">
        {connectors.length === 0 ? <StudioInlineEmptyState title="No connections yet" description="Add an integration to configure a service for this app." /> : (
          <ul className="divide-y divide-border">
            {connectors.map(connector => (
              <li key={connector.service} className="flex flex-wrap items-center justify-between gap-3 py-3">
                <div className="min-w-0">
                  <h3 className="break-words font-semibold">{connector.display_name || connector.service}</h3>
                  <div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
                    <span>{connector.service}</span>
                    <StatusPill tone={connector.ready ? 'success' : 'muted'}>{connector.ready ? 'Ready' : (connector.status || 'Needs setup').replaceAll('_', ' ')}</StatusPill>
                    {connector.secret_available && <span>Credential saved</span>}
                  </div>
                  {connector.notes && <p className="mt-1 whitespace-pre-wrap break-words text-sm">{connector.notes}</p>}
                  {connector.health?.status && <p className="text-xs text-muted-foreground">Health · {connector.health.status.replaceAll('_', ' ')}</p>}
                </div>
                <div className="flex gap-2">
                  {connector.health?.health_check_supported && <ActionButton size="sm" variant="secondary" disabled={busy} onClick={() => update(() => requestConnectors(appId, { service: connector.service, check: true, method: 'POST' }))}>Check status</ActionButton>}
                  <ActionButton size="sm" variant="secondary" disabled={busy} onClick={() => openEditor(connector, true)}>Edit</ActionButton>
                </div>
              </li>
            ))}
          </ul>
        )}
      </Panel>
      {declarations.length > 0 && (
        <Panel title="Build requirements">
          <ul className="divide-y divide-border">{declarations.map(need => (
            <li key={need.service} className="flex flex-wrap items-center justify-between gap-2 py-3">
              <div><h3 className="font-semibold">{need.display_name || need.service}</h3>
                <p className="text-sm text-muted-foreground">{need.optional ? 'Optional' : 'Required'}{need.required_at && ' at ' + need.required_at}{!need.catalog_id && ' · App-specific'}</p>
                {need.purpose && <p className="text-sm">{need.purpose}</p>}
              </div>
              <div className="flex gap-2">
                <ActionButton variant="secondary" disabled={busy} onClick={() => {
                  const saved = connectors.find(item => item.service === need.service)
                  openEditor(saved || { service: need.service, display_name: need.display_name }, Boolean(saved))
                }}>Configure</ActionButton>
              {need.removable && <ActionButton variant="secondary" disabled={busy} onClick={() => {
                if (window.confirm('Remove this integration requirement from the app?')) update(() => studioModuleAction('workspace_integrations', 'delete_app_integration_need', { app_id: data.app_id, service: need.service }))
              }}>Remove requirement</ActionButton>}
              </div>
            </li>
          ))}</ul>
        </Panel>
      )}
      {editor && <AdapterEditor connector={editor.record} editing={editor.editing} catalog={catalog} credentialStorageAvailable={data.credential_storage_available}
        busy={busy} error={actionError} onClose={closeEditor}
        onSave={(service, fields) => {
          if (editor.editing) {
            update(() => requestConnectors(appId, { service: editor.record.service, method: 'PATCH', body: fields }))
          } else if (connectors.some(connector => connector.service === service)) {
            setActionError('This service already has an integration. Open it to make changes.')
          } else {
            update(() => requestConnectors(appId, { method: 'POST', body: { service, ...fields } }))
          }
        }}
        onDelete={() => {
          if (window.confirm(`Delete the integration for ${editor.record.display_name || editor.record.service} from this app?`)) {
            update(() => requestConnectors(appId, { method: 'DELETE', service: editor.record.service }))
          }
        }} />}
    </div>
  )
}

export default function AppIntegrationsPage() {
  const { appId } = useParams()
  return <WorkspaceLayout><SelectedAppIntegrations key={appId} appId={appId} /></WorkspaceLayout>
}
