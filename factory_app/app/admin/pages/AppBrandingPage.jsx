import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { PageHeader } from '@mozaiks/chat-ui/ui'
import { WorkspaceLayout } from '@mozaiks/chat-ui/workspace'
import { Panel, StudioErrorState, StudioInlineEmptyState, StudioLoadingState } from '../../ui/components/StudioShared.jsx'
import SettingsRefinement, { SettingsResume } from './SettingsRefinement.jsx'
import { studioFetch } from './studioApi.js'

function SelectedAppBranding({ appId }) {
  const [branding, setBranding] = useState(null)
  const [error, setError] = useState(null)
  useEffect(() => {
    const controller = new AbortController()
    async function load() {
      const response = await studioFetch('/api/studio/apps/' + encodeURIComponent(appId) + '/branding', { signal: controller.signal })
      if (!response.ok) throw new Error('Branding for this app is unavailable to this account.')
      const body = await response.json()
      if (body.dashboard_app_id !== appId) throw new Error('The branding response does not match this app.')
      if (!controller.signal.aborted) setBranding(body)
    }
    load().catch(err => { if (!controller.signal.aborted) setError(err.message) })
    return () => controller.abort()
  }, [appId])
  if (error) return <StudioErrorState title="Branding unavailable" message={error} />
  if (!branding) return <StudioLoadingState label="Loading app branding." />
  const theme = branding.theme?.theme || {}
  const fields = Object.entries({ 'Primary color': theme.primary, 'Secondary color': theme.secondary, Font: theme.font, Appearance: theme.appearance })
    .filter(([, value]) => typeof value === 'string' && value)
  return <div className="space-y-6">
    <PageHeader title="Branding" subtitle={'Branding for ' + branding.app_name + '.'} />
    <SettingsResume resume={branding.resume} appId={appId} />
    {branding.status === 'configured' ? <>
      {fields.length ? <Panel title="Branding in selected build">
        <dl className="grid gap-4 sm:grid-cols-2">
          {fields.map(([label, value]) => <div key={label}><dt className="text-sm text-muted-foreground">{label}</dt><dd>{value}</dd></div>)}
        </dl>
      </Panel> : <StudioInlineEmptyState title="Branding details unavailable" description="The selected build has no saved theme values." />}
      <SettingsRefinement descriptor={branding.settings_refinement} appId={appId} />
    </> : !branding.resume && <StudioInlineEmptyState title="No editable Factory build" description="Branding edits become available when this app has a validated saved Factory build." />}
  </div>
}

export default function AppBrandingPage() {
  const { appId } = useParams()
  return <WorkspaceLayout><SelectedAppBranding key={appId} appId={appId} /></WorkspaceLayout>
}
