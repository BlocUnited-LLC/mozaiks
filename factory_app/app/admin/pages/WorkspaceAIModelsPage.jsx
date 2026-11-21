import { useEffect, useState } from 'react'
import { WorkspaceLayout } from '@mozaiks/chat-ui/workspace'
import { ActionButton } from '../../ui/components/StudioShared.jsx'
import { studioFetch } from './studioApi.js'

const settingsURL = '/api/studio/settings/ai-models'

export default function WorkspaceAIModelsPage() {
  const [policy, setPolicy] = useState(null)
  const [saved, setSaved] = useState(null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)
  const [notice, setNotice] = useState(null)

  useEffect(() => {
    const controller = new AbortController()
    studioFetch(settingsURL, { signal: controller.signal })
      .then(response => {
        if (!response.ok) throw new Error('Could not load workspace AI model settings.')
        return response.json()
      })
      .then(data => {
        if (controller.signal.aborted) return
        setPolicy(data)
        setSaved(data)
      })
      .catch(err => {
        if (!controller.signal.aborted) setError(err.message)
      })
    return () => controller.abort()
  }, [])

  const availableDefaults = policy?.models.filter(model => (
    model.configured && policy.allowed.includes(model.id)
  )) ?? []
  const changed = policy && saved && (
    policy.default !== saved.default || policy.allowed.join(',') !== saved.allowed.join(',')
  )

  function toggleModel(id, checked) {
    setNotice(null)
    setPolicy(current => ({
      ...current,
      allowed: checked
        ? current.models.filter(model => model.id === id || current.allowed.includes(model.id)).map(model => model.id)
        : current.allowed.filter(model => model !== id),
    }))
  }

  async function save(event) {
    event.preventDefault()
    if (saving || !changed || !availableDefaults.some(model => model.id === policy.default)) return
    setSaving(true)
    setError(null)
    setNotice(null)
    try {
      const response = await studioFetch(settingsURL, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ default: policy.default, allowed: policy.allowed }),
      })
      if (!response.ok) {
        const result = await response.json().catch(() => null)
        throw new Error(typeof result?.detail === 'string' ? result.detail : 'Could not save workspace AI models.')
      }
      setSaved(policy)
      setNotice('AI model settings saved.')
    } catch (err) {
      setError(err.message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <WorkspaceLayout>
      <main className="mx-auto w-full max-w-2xl space-y-5 px-4 py-6">
        <h1 className="text-2xl font-semibold">AI models</h1>
        <p className="text-sm text-muted-foreground">
          Choose the default and allowed models for new Factory builds and refinements.
        </p>
        {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
        {notice && <p role="status" className="text-sm text-muted-foreground">{notice}</p>}
        {!policy && !error && <p role="status">Loading AI model settings…</p>}
        {policy && (
          <form onSubmit={save} className="space-y-5 rounded-lg border border-border bg-card p-4">
            <fieldset disabled={saving} className="space-y-3">
              <legend className="font-medium">Allowed models</legend>
              {policy.models.map(model => (
                <label key={model.id} className="flex items-center justify-between gap-3">
                  <span className="flex items-center gap-2">
                    <input
                      type="checkbox"
                      checked={policy.allowed.includes(model.id)}
                      onChange={event => toggleModel(model.id, event.target.checked)}
                      disabled={!model.configured && !policy.allowed.includes(model.id)}
                    />
                    {model.label}
                  </span>
                  <span className="text-sm text-muted-foreground">
                    {model.configured ? 'Configured' : 'Provider key missing'}
                  </span>
                </label>
              ))}
            </fieldset>
            <label className="block space-y-2">
              <span className="block font-medium">Default model</span>
              <select
                value={policy.default}
                onChange={event => {
                  setNotice(null)
                  setPolicy(current => ({ ...current, default: event.target.value }))
                }}
                disabled={saving || availableDefaults.length === 0}
                className="w-full rounded-md border border-border bg-background px-3 py-2"
              >
                {!availableDefaults.some(model => model.id === policy.default) && (
                  <option value={policy.default} disabled>Select a configured model</option>
                )}
                {availableDefaults.map(model => <option key={model.id} value={model.id}>{model.label}</option>)}
              </select>
            </label>
            <ActionButton
              type="submit"
              disabled={saving || !changed || !availableDefaults.some(model => model.id === policy.default)}
            >
              {saving ? 'Saving…' : 'Save settings'}
            </ActionButton>
          </form>
        )}
      </main>
    </WorkspaceLayout>
  )
}
