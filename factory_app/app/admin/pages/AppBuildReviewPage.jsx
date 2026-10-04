import { useEffect, useMemo, useState } from 'react'
import { useParams } from 'react-router-dom'
import { UIToolRenderer } from '@mozaiks/chat-ui'
import { WorkspaceLayout } from '@mozaiks/chat-ui/workspace'
import {
  ActionButton,
  StudioErrorState,
  StudioInlineEmptyState,
  StudioLoadingState,
  Panel,
  StatusPill,
} from '../../ui/components/StudioShared.jsx'
import CarryForwardReportSummary from './CarryForwardReportSummary.jsx'
import AppStudioHero, { formatDateTimeLabel } from './AppStudioChrome.jsx'
import { getAppStudioSnapshot } from './appStudioDataHelpers.js'
import { studioFetch } from './studioApi.js'
import { useAppStudioData } from './useAppStudioData.js'

function artifactId(artifact) {
  return artifact?.id || artifact?._id || artifact?.artifact_version_id || null
}

function SavedArtifactWorkbench({ appId, buildRegistryId, artifactVersionId, dataMode }) {
  const [loaded, setLoaded] = useState(null)
  const [retry, setRetry] = useState(0)
  const requestKey = JSON.stringify([appId, buildRegistryId, artifactVersionId, dataMode, retry])

  useEffect(() => {
    const controller = new AbortController()
    let cancelled = false

    async function loadBundle() {
      if (!artifactVersionId || !buildRegistryId || dataMode === 'demo') return
      try {
        const response = await studioFetch(
          `/api/studio/build/artifacts/${encodeURIComponent(artifactVersionId)}/bundle?build_registry_id=${encodeURIComponent(buildRegistryId)}`,
          { signal: controller.signal },
        )
        const body = await response.json().catch(() => null)
        if (!response.ok) throw new Error(body?.detail || `Saved build unavailable: ${response.status}`)
        const workbench = body?.workbench
        if (body?.artifact_version_id !== artifactVersionId || body?.app_id !== appId
          || body?.build_family !== 'app_bundle'
          || workbench?.artifact_version_id !== artifactVersionId
          || workbench?.target_app_id !== appId || workbench?.build_registry_id !== buildRegistryId
          || workbench?.build_family !== 'app_bundle') {
          throw new Error('The saved build response does not match the selected app and version.')
        }
        const descriptor = body.workbench_ui
        if (typeof descriptor?.component !== 'string' || !descriptor.component.trim()
          || typeof descriptor?.workflow_name !== 'string' || !descriptor.workflow_name.trim()) {
          throw new Error('This saved build has no registered review surface.')
        }
        if (!cancelled) setLoaded({ requestKey, body })
      } catch (err) {
        if (!cancelled) setLoaded(previous => ({
          ...previous,
          error: { requestKey, message: err instanceof Error ? err.message : 'Saved build unavailable.' },
        }))
      }
    }

    loadBundle()
    return () => { cancelled = true; controller.abort() }
  }, [appId, buildRegistryId, artifactVersionId, dataMode, requestKey])

  if (dataMode === 'demo') return <StudioInlineEmptyState title="Review unavailable in demo data" description="Open a saved live build to preview and refine it." />
  if (!artifactVersionId || !buildRegistryId) return <StudioInlineEmptyState title="No saved build selected" description="Select an app build version to open its review." />
  const currentError = loaded?.error?.requestKey === requestKey ? loaded.error.message : null
  const isCurrent = loaded?.requestKey === requestKey && !currentError
  const body = loaded?.body
  const event = body ? {
    tool_name: body.workbench_ui.component,
    component_type: body.workbench_ui.component,
    workflow_name: body.workbench_ui.workflow_name,
    payload: {
      ...body.workbench,
      artifact_kind: body.build_family,
      artifact_key: body.build_key,
      review: body.review,
    },
  } : null
  // A saved bundle has no workflow response or export-confirmation items.
  // The registered Workbench owns preview, refinement and server-gated review.
  return (
    <div>
      {!isCurrent && (currentError ? (
        <div className="space-y-3">
          <StudioErrorState title="Saved build unavailable" message={currentError} />
          <ActionButton onClick={() => setRetry(value => value + 1)}>Retry opening build</ActionButton>
        </div>
      ) : <StudioLoadingState label="Opening saved build..." />)}
      {/* Keep preview ownership in useSandbox while preventing stale-version interaction. */}
      <div hidden={!isCurrent} inert={!isCurrent} aria-hidden={!isCurrent}>
        {event ? <UIToolRenderer event={event} /> : null}
      </div>
    </div>
  )
}

export default function AppBuildReviewPage() {
  const { appId = 'workspace-app' } = useParams()
  const { data, loading, error, dataMode } = useAppStudioData(appId)
  const [selectedArtifactId, setSelectedArtifactId] = useState(null)
  const snapshot = useMemo(() => getAppStudioSnapshot(appId, data, dataMode), [appId, data, dataMode])
  const buildHistory = snapshot.buildHistory || []
  const latestArtifact = buildHistory[0] || null
  const selectedArtifact = buildHistory.find(artifact => artifactId(artifact) === selectedArtifactId) || latestArtifact
  const activeArtifactId = artifactId(selectedArtifact)

  if (loading) return <StudioLoadingState label="Loading build review..." />
  if (error || !data?.summary) return <StudioErrorState title="Build Review Unavailable" message={error || 'No summary returned.'} />

  return (
    <WorkspaceLayout>
      <div className="min-w-0 space-y-5">
        <AppStudioHero
          appId={appId}
          summary={data.summary}
          dataMode={dataMode}
          title="Build Review"
          subtitle="Try a saved version, request changes, then review it for activation."
          currentSection="activity"
        />

        {buildHistory.length === 0 ? (
          <StudioInlineEmptyState title="No build versions yet" description="Build versions appear after generation or refinement saves an app bundle." />
        ) : (
          <>
            <div className="flex flex-wrap items-center gap-3">
              <label htmlFor="saved-build-version" className="text-sm font-medium text-foreground">Starting version</label>
              <select
                id="saved-build-version"
                className="min-w-0 max-w-full rounded-lg border border-border bg-card px-3 py-2 text-sm text-foreground"
                value={activeArtifactId || ''}
                onChange={event => setSelectedArtifactId(event.target.value)}
              >
                {buildHistory.filter(artifact => artifactId(artifact)).map(artifact => (
                  <option key={artifactId(artifact)} value={artifactId(artifact)}>
                    v{artifact.version_number} · {formatDateTimeLabel(artifact.created_at)}
                  </option>
                ))}
              </select>
            </div>

            <SavedArtifactWorkbench
              appId={appId}
              buildRegistryId={data.buildRegistryId}
              artifactVersionId={activeArtifactId}
              dataMode={dataMode}
            />

            <details className="rounded-xl border border-border/42 bg-card/20 p-4">
              <summary className="cursor-pointer text-sm font-medium text-foreground">Build history and preservation reports</summary>
              <div className="mt-4">
                <Panel title="Build versions" subtitle="Saved versions and their recorded preservation evidence.">
                  <div className="space-y-3">
                    {buildHistory.map(artifact => {
                      if (!artifact) return null
                      const cfReport = artifact?.commit_metadata?.metadata?.carry_forward_report || null
                      return (
                        <div key={artifactId(artifact)} className="rounded-lg border border-border/42 p-3">
                          <div className="flex flex-wrap items-center justify-between gap-2">
                            <div className="text-sm font-medium text-foreground">v{artifact.version_number} · {formatDateTimeLabel(artifact.created_at)}</div>
                            <StatusPill tone={artifact.validation_status === 'passed' ? 'success' : 'warning'}>{artifact.validation_status || 'pending'}</StatusPill>
                          </div>
                          {artifact.commit_metadata?.message ? <p className="mt-2 break-words text-sm text-muted-foreground">{artifact.commit_metadata.message}</p> : null}
                          {cfReport ? <CarryForwardReportSummary report={cfReport} /> : <p className="mt-2 text-xs text-muted-foreground">No carry-forward preservation report for this build.</p>}
                        </div>
                      )
                    })}
                  </div>
                </Panel>
              </div>
            </details>
          </>
        )}
      </div>
    </WorkspaceLayout>
  )
}
