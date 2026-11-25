import { useEffect, useRef, useState } from 'react'
import { useParams } from 'react-router-dom'
import { Button, ErrorState, InlineEmptyState, LoadingState, PageHeader, Panel } from '@mozaiks/chat-ui/ui'
import { WorkspaceLayout } from '@mozaiks/chat-ui/workspace'
import { studioFetch } from './studioApi.js'

async function launchRequest(path, signal) {
  const response = await studioFetch(path, { signal, cache: 'no-store' })
  if (!response.ok) {
    const body = await response.json().catch(() => null)
    throw new Error(typeof body?.detail === 'string' ? body.detail : `Launch request failed (HTTP ${response.status}).`)
  }
  return response
}

function AppLaunch({ appId }) {
  const [launch, setLaunch] = useState(null)
  const [loadError, setLoadError] = useState('')
  const [downloadError, setDownloadError] = useState('')
  const [busy, setBusy] = useState(false)
  const [revision, setRevision] = useState(0)
  const download = useRef(null)
  const path = `/api/studio/apps/${encodeURIComponent(appId)}/launch`

  useEffect(() => {
    const controller = new AbortController()
    setLaunch(null)
    setLoadError('')
    setDownloadError('')
    launchRequest(path, controller.signal)
      .then(response => response.json())
      .then(result => { if (!controller.signal.aborted) setLaunch(result) })
      .catch(error => { if (!controller.signal.aborted) setLoadError(error.message) })
    return () => {
      controller.abort()
      download.current?.abort()
    }
  }, [path, revision])

  async function downloadBundle() {
    if (download.current) return
    const controller = new AbortController()
    download.current = controller
    setBusy(true)
    setDownloadError('')
    try {
      const response = await launchRequest(`${path}/${encodeURIComponent(launch.artifact_version_id)}/download`, controller.signal)
      const bundle = await response.blob()
      if (controller.signal.aborted) return
      const url = URL.createObjectURL(bundle)
      try {
        const anchor = document.createElement('a')
        anchor.href = url
        anchor.download = launch.filename
        document.body.appendChild(anchor)
        anchor.click()
        anchor.remove()
      } finally {
        setTimeout(() => URL.revokeObjectURL(url), 0)
      }
    } catch (error) {
      if (!controller.signal.aborted) setDownloadError(error.message)
    } finally {
      download.current = null
      if (!controller.signal.aborted) setBusy(false)
    }
  }

  const refresh = () => setRevision(value => value + 1)
  if (loadError) return <ErrorState title="Launch unavailable" message={loadError} action={<Button onClick={refresh}>Try again</Button>} />
  if (!launch) return <LoadingState label="Loading launch details…" />

  const needsBuild = launch.status === 'no_build'
  return (
    <div className="space-y-6">
      <PageHeader title="Launch" subtitle="Download this app’s validated build for self-hosting." />
      {launch.status === 'ready' ? (
        <Panel title="App bundle" subtitle={launch.filename}>
          <p className="mb-4 text-sm text-muted-foreground">
            Extract the ZIP. In the folder containing <code>app.json</code> or <code>app/app.json</code>, configure MongoDB and
            the app’s sign-in provider, then start the platform backend with <code>mozaiks serve . --host platform</code>.
          </p>
          <Button onClick={downloadBundle} disabled={busy}>{busy ? 'Downloading…' : 'Download app bundle'}</Button>
          {downloadError && (
            <div role="alert" className="mt-3 space-y-2 text-sm text-destructive">
              <p>{downloadError}</p>
              <Button variant="outline" size="sm" onClick={refresh}>Refresh build</Button>
            </div>
          )}
        </Panel>
      ) : (
        <InlineEmptyState title={needsBuild ? 'No app build yet' : 'Build validation required'}
          description={needsBuild ? 'Complete an app build to download its bundle.' : 'The bundle is available once the app passes build validation.'} />
      )}
    </div>
  )
}

export default function AppLaunchPage() {
  const { appId } = useParams()
  return <WorkspaceLayout><AppLaunch key={appId} appId={appId} /></WorkspaceLayout>
}
