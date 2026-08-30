import { useEffect, useMemo, useState } from 'react'
import { useParams } from 'react-router-dom'
import {
  CheckCircle2,
  GitBranch,
  Upload,
  XCircle,
} from 'lucide-react'

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


function validationTone(status) {
  if (status === 'passed') return 'success'
  if (status === 'failed') return 'destructive'
  return 'warning'
}

function lifecycleTone(status) {
  if (status === 'current') return 'success'
  if (status === 'draft') return 'default'
  if (status === 'stale' || status === 'superseded') return 'warning'
  return 'default'
}

function reviewTone(status) {
  const value = String(status || '').toLowerCase()
  if (value === 'validated' || value === 'accepted' || value === 'promoted') return 'success'
  if (value === 'failed' || value === 'rejected') return 'destructive'
  if (value === 'draft' || value === 'pending') return 'warning'
  return 'default'
}

function artifactId(artifact) {
  return artifact?.id || artifact?._id || artifact?.artifact_version_id || null
}

function actionIcon(actionId, className = 'h-3.5 w-3.5') {
  if (actionId === 'accept') return <CheckCircle2 className={className} />
  if (actionId === 'reject') return <XCircle className={className} />
  if (actionId === 'promote') return <Upload className={className} />
  return <GitBranch className={className} />
}

function actionVariant(actionId) {
  if (actionId === 'accept' || actionId === 'promote') return 'primary'
  if (actionId === 'reject') return 'destructive'
  return 'outline'
}

async function postReviewAction({ appId, artifactVersionId, actionId }) {
  const endpointByAction = {
    accept: 'accept',
    reject: 'reject',
    promote: 'promote',
  }
  const endpoint = endpointByAction[actionId]
  if (!endpoint) throw new Error(`${actionId} is not available yet.`)
  const response = await studioFetch(
    `/api/studio/build/artifacts/${encodeURIComponent(artifactVersionId)}/${endpoint}?app_id=${encodeURIComponent(appId)}`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: endpoint === 'accept' ? JSON.stringify({ allow_validation_override: false }) : undefined,
    },
  )
  if (!response.ok) {
    const payload = await response.json().catch(() => null)
    throw new Error(payload?.detail || `Review action failed: ${response.status}`)
  }
  return response.json()
}

function ReviewPackagePanel({ appId, artifactVersionId, dataMode, onRefresh }) {
  const [payload, setPayload] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [actionState, setActionState] = useState({ busy: null, error: null })

  useEffect(() => {
    let cancelled = false

    async function loadReviewPackage() {
      if (!artifactVersionId || dataMode === 'demo') {
        setPayload(null)
        setLoading(false)
        setError(null)
        return
      }
      setLoading(true)
      setError(null)
      try {
        const response = await studioFetch(
          `/api/studio/build/artifacts/${encodeURIComponent(artifactVersionId)}/review?app_id=${encodeURIComponent(appId)}`,
        )
        if (!response.ok) {
          const body = await response.json().catch(() => null)
          throw new Error(body?.detail || `Review unavailable: ${response.status}`)
        }
        const body = await response.json()
        if (!cancelled) setPayload(body)
      } catch (err) {
        if (!cancelled) {
          setPayload(null)
          setError(err instanceof Error ? err.message : 'Review unavailable.')
        }
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    loadReviewPackage()
    return () => {
      cancelled = true
    }
  }, [appId, artifactVersionId, dataMode])

  async function handleAction(action) {
    if (!action?.enabled || !artifactVersionId) return
    setActionState({ busy: action.id, error: null })
    try {
      const nextPayload = await postReviewAction({ appId, artifactVersionId, actionId: action.id })
      setPayload(nextPayload)
      if (typeof onRefresh === 'function') onRefresh()
      setActionState({ busy: null, error: null })
    } catch (err) {
      setActionState({
        busy: null,
        error: err instanceof Error ? err.message : 'Review action failed.',
      })
    }
  }

  const review = payload?.review || null
  const routeDecision = review?.route_decision || {}
  const actions = Array.isArray(review?.actions) ? review.actions : []
  const visibleActions = actions.filter((action) => ['accept', 'reject', 'promote'].includes(action.id))
  const changedFiles = Array.isArray(review?.changed_files) ? review.changed_files : []
  const affectedPaths = Array.isArray(review?.affected_paths) ? review.affected_paths : []
  const riskNotes = Array.isArray(review?.risk_notes) ? review.risk_notes : []

  return (
    <Panel
      title="Selected artifact review"
      subtitle="Review the staged output, validation result, and where acceptance writes back."
    >
      {!artifactVersionId ? (
        <StudioInlineEmptyState
          title="No artifact selected"
          description="Select a build version to review its staged output."
        />
      ) : dataMode === 'demo' ? (
        <StudioInlineEmptyState
          title="Review unavailable in demo data"
          description="Live builds show staged output, validation state, and available actions."
        />
      ) : loading ? (
        <StudioLoadingState label="Loading review..." />
      ) : error ? (
        <StudioErrorState title="Review Unavailable" message={error} />
      ) : !review ? (
        <StudioInlineEmptyState
          title="No review available"
          description="This build version does not have review data yet."
        />
      ) : (
        <div className="space-y-5">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="text-sm font-semibold text-foreground">
                {review.write_back_label || 'Review staged output'}
              </div>
              <div className="mt-1 max-w-2xl text-sm leading-6 text-muted-foreground">
                {review.write_back_description || 'Accepted changes stay in the selected staged output lane.'}
              </div>
              {review.write_back_target ? (
                <div className="mt-2 text-xs text-muted-foreground/75">
                  Target: {review.write_back_target}
                </div>
              ) : null}
            </div>
            <div className="flex flex-wrap gap-2">
              <StatusPill tone={reviewTone(review.review_status)}>{review.review_status || 'review'}</StatusPill>
              <StatusPill tone={validationTone(review.validation_status)}>{review.validation_status || 'pending'}</StatusPill>
              <StatusPill tone={lifecycleTone(review.lifecycle_status)}>{review.lifecycle_status || 'draft'}</StatusPill>
            </div>
          </div>

          <div className="grid gap-3 lg:grid-cols-3">
            <div className="rounded-lg border border-border/42 bg-background/28 px-3 py-3">
              <div className="text-[11px] font-semibold uppercase text-muted-foreground/65">Change</div>
              <div className="mt-1 text-sm font-medium text-foreground">{review.change_class || 'Unclassified'}</div>
              <div className="mt-1 text-xs text-muted-foreground">{review.refinement_lane || 'No lane recorded'}</div>
            </div>
            <div className="rounded-lg border border-border/42 bg-background/28 px-3 py-3">
              <div className="text-[11px] font-semibold uppercase text-muted-foreground/65">Route</div>
              <div className="mt-1 text-sm font-medium text-foreground">{review.workflow_sequence || 'No sequence'}</div>
              <div className="mt-1 text-xs text-muted-foreground">{routeDecision.execution_mode || 'No execution mode'}</div>
            </div>
            <div className="rounded-lg border border-border/42 bg-background/28 px-3 py-3">
              <div className="text-[11px] font-semibold uppercase text-muted-foreground/65">Files</div>
              <div className="mt-1 text-sm font-medium text-foreground">{review.changed_file_count || 0} changed</div>
              <div className="mt-1 text-xs text-muted-foreground">{affectedPaths.length || 0} affected path(s)</div>
            </div>
          </div>

          {routeDecision.rationale || routeDecision.scope_summary ? (
            <div className="rounded-lg border border-border/42 bg-card/28 px-3 py-3 text-sm leading-6 text-muted-foreground">
              {routeDecision.rationale ? <div>{routeDecision.rationale}</div> : null}
              {routeDecision.scope_summary ? <div>{routeDecision.scope_summary}</div> : null}
            </div>
          ) : null}

          <div>
            <div className="mb-2 text-[11px] font-semibold uppercase text-muted-foreground/65">Actions</div>
            <div className="flex flex-wrap gap-2">
              {visibleActions.map((action) => (
                <ActionButton
                  key={action.id}
                  size="sm"
                  variant={actionVariant(action.id)}
                  disabled={!action.enabled || actionState.busy === action.id}
                  onClick={() => handleAction(action)}
                  title={action.reason || action.label}
                  className="inline-flex items-center gap-1.5"
                >
                  {actionIcon(action.id)}
                  {actionState.busy === action.id ? 'Working...' : action.label}
                </ActionButton>
              ))}
            </div>
            {visibleActions.length === 0 ? (
              <div className="rounded-lg border border-border/42 bg-background/24 px-3 py-3 text-sm text-muted-foreground">
                No actions are available for this build version.
              </div>
            ) : null}
            {actionState.error ? (
              <div className="mt-2 rounded-lg border border-destructive/35 bg-destructive/8 px-3 py-2 text-xs text-destructive">
                {actionState.error}
              </div>
            ) : null}
          </div>

          {riskNotes.length > 0 ? (
            <div className="rounded-lg border border-warning/35 bg-warning/8 px-3 py-3">
              <div className="text-[11px] font-semibold uppercase text-muted-foreground/65">Risk notes</div>
              <ul className="mt-2 space-y-1 text-sm text-muted-foreground">
                {riskNotes.slice(0, 4).map((note) => (
                  <li key={note}>{note}</li>
                ))}
              </ul>
            </div>
          ) : null}

          <div className="grid gap-4 lg:grid-cols-[minmax(0,0.7fr)_minmax(0,1.3fr)]">
            <div>
              <div className="mb-2 text-[11px] font-semibold uppercase text-muted-foreground/65">Affected paths</div>
              {affectedPaths.length > 0 ? (
                <div className="max-h-56 overflow-auto rounded-lg border border-border/42 bg-background/24">
                  {affectedPaths.slice(0, 24).map((path) => (
                    <div key={path} className="border-b border-border/28 px-3 py-2 text-xs text-muted-foreground last:border-b-0">
                      {path}
                    </div>
                  ))}
                </div>
              ) : (
                <div className="rounded-lg border border-border/42 bg-background/24 px-3 py-3 text-sm text-muted-foreground">
                  No affected paths recorded.
                </div>
              )}
            </div>
            <div>
              <div className="mb-2 text-[11px] font-semibold uppercase text-muted-foreground/65">Changed files</div>
              {changedFiles.length > 0 ? (
                <div className="space-y-3">
                  {changedFiles.slice(0, 6).map((file) => (
                    <div key={file.path} className="rounded-lg border border-border/42 bg-background/24">
                      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border/28 px-3 py-2">
                        <div className="text-xs font-medium text-foreground">{file.path}</div>
                        <StatusPill tone="default">{file.change_type || 'modified'}</StatusPill>
                      </div>
                      {file.diff_preview ? (
                        <pre className="max-h-72 overflow-auto whitespace-pre-wrap px-3 py-3 text-[11px] leading-5 text-muted-foreground">
                          {file.diff_preview}
                        </pre>
                      ) : (
                        <div className="px-3 py-3 text-sm text-muted-foreground">No text diff available.</div>
                      )}
                    </div>
                  ))}
                </div>
              ) : (
                <div className="rounded-lg border border-border/42 bg-background/24 px-3 py-3 text-sm text-muted-foreground">
                  No changed files detected.
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </Panel>
  )
}

export default function AppBuildReviewPage() {
  const { appId = 'workspace-app' } = useParams()
  const { data, loading, error, dataMode, refresh } = useAppStudioData(appId)
  const [selectedArtifactId, setSelectedArtifactId] = useState(null)
  const snapshot = useMemo(() => getAppStudioSnapshot(appId, data, dataMode), [appId, data, dataMode])
  const buildHistory = snapshot.buildHistory || []
  const latestArtifact = buildHistory[0] || null
  const activeArtifactId = selectedArtifactId || artifactId(latestArtifact)

  useEffect(() => {
    if (selectedArtifactId && buildHistory.some((artifact) => artifactId(artifact) === selectedArtifactId)) return
    setSelectedArtifactId(artifactId(latestArtifact))
  }, [buildHistory, latestArtifact, selectedArtifactId])

  if (loading) return <StudioLoadingState label="Loading build review..." />
  if (error || !data?.summary) return <StudioErrorState title="Build Review Unavailable" message={error || 'No summary returned.'} />

  const summaryItems = [
    {
      id: 'versions',
      label: 'Build Versions',
      value: String(buildHistory.length || 0),
      detail: latestArtifact ? `Latest: v${latestArtifact.version_number}` : 'No versions yet',
    },
    {
      id: 'latest',
      label: 'Latest Build',
      value: latestArtifact ? `v${latestArtifact.version_number}` : 'Pending',
      detail: latestArtifact ? formatDateTimeLabel(latestArtifact.created_at) : 'No saved build versions',
    },
    {
      id: 'validation',
      label: 'Latest Validation',
      value: latestArtifact?.validation_status || 'Pending',
      detail: latestArtifact?.lifecycle_status || 'No status',
    },
    {
      id: 'carry_forward',
      label: 'Carry-forward Reports',
      value: String(buildHistory.filter((v) => v?.commit_metadata?.metadata?.carry_forward_report).length),
      detail: 'Builds with preservation audit',
    },
  ]

  return (
    <WorkspaceLayout>
      <div className="space-y-6">
        <AppStudioHero
          appId={appId}
          summary={data.summary}
          dataMode={dataMode}
          title="Build Review"
          subtitle="Review build versions, validation state, staged output, and promotion decisions."
          currentSection="activity"
          summaryItems={summaryItems}
        />

        <Panel
          title="Build versions"
          subtitle="Select one version to inspect validation, diffs, and review actions."
        >
          {buildHistory.length === 0 ? (
            <StudioInlineEmptyState
              title="No build versions yet"
              description="Build versions appear after generation or refinement saves an app bundle."
            />
          ) : (
            <div className="space-y-3">
              {buildHistory.map((artifact) => {
                if (!artifact) return null
                const cfReport = artifact?.commit_metadata?.metadata?.carry_forward_report || null
                return (
                  <div
                    key={artifact.id || artifact.version_number}
                    className="rounded-2xl border border-border/42 bg-card/30 px-4 py-4"
                  >
                    <div className="flex flex-wrap items-start justify-between gap-3">
                      <div>
                        <div className="text-[12px] font-medium text-muted-foreground/82">
                          Build artifact
                        </div>
                        <div className="mt-1 text-base font-semibold text-foreground">
                          v{artifact.version_number}
                        </div>
                        <div className="mt-0.5 text-sm text-muted-foreground">
                          {formatDateTimeLabel(artifact.created_at)}
                        </div>
                        {artifact.commit_metadata?.message && (
                          <div className="mt-1 text-[12px] text-muted-foreground/70">
                            {artifact.commit_metadata.message}
                          </div>
                        )}
                        {artifact.commit_metadata?.source_workflow && (
                          <div className="mt-0.5 text-[11px] text-muted-foreground/56">
                            {artifact.commit_metadata.source_workflow}
                          </div>
                        )}
                      </div>
                      <div className="flex flex-wrap items-center justify-end gap-2">
                        <div className="flex flex-wrap gap-2">
                          <StatusPill tone={validationTone(artifact.validation_status)}>
                            {artifact.validation_status || 'pending'}
                          </StatusPill>
                          <StatusPill tone={lifecycleTone(artifact.lifecycle_status)}>
                            {artifact.lifecycle_status || 'draft'}
                          </StatusPill>
                        </div>
                        {artifactId(artifact) ? (
                          <ActionButton
                            size="sm"
                            variant={activeArtifactId === artifactId(artifact) ? 'primary' : 'outline'}
                            onClick={() => setSelectedArtifactId(artifactId(artifact))}
                            className="inline-flex items-center gap-1.5"
                          >
                            <GitBranch className="h-3.5 w-3.5" />
                            Select
                          </ActionButton>
                        ) : null}
                      </div>
                    </div>

                    {cfReport && (
                      <CarryForwardReportSummary report={cfReport} />
                    )}
                    {!cfReport && (
                      <div className="mt-2 text-[11px] text-muted-foreground/50">
                        No carry-forward preservation for this build.
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          )}
        </Panel>

        {buildHistory.length > 0 && (
          <ReviewPackagePanel
            appId={appId}
            artifactVersionId={activeArtifactId}
            dataMode={dataMode}
            onRefresh={refresh}
          />
        )}
      </div>
    </WorkspaceLayout>
  )
}
