import { useEffect, useState } from 'react'

import {
  ActionButton,
  Panel,
  StatusPill,
  StudioErrorState,
  StudioLoadingState,
} from '../../ui/components/StudioShared.jsx'
import { studioFetch } from './studioApi.js'

const SHA256 = /^[0-9a-f]{64}$/

function responseError(body, fallback) {
  return typeof body?.detail === 'string' ? body.detail : fallback
}

function verifiedClaim(body, artifactVersionId, targetAppId) {
  const claim = body?.genesis_import
  const version = body?.artifact_version
  if (body?.app_id !== targetAppId || version?.id !== artifactVersionId
    || version?.app_id !== targetAppId || claim?.build_record_id !== artifactVersionId
    || version?.commit_metadata?.metadata?.bundle_mode !== 'brownfield_genesis_import'
    || !['reserved', 'accepted'].includes(claim?.status)
    || !SHA256.test(claim?.bundle_sha256 || '')
    || !SHA256.test(claim?.manifest_sha256 || '')
    || !/^[A-Za-z0-9_-]+$/.test(claim?.bundle_name || '')) {
    throw new Error('The Genesis review does not match the selected imported source.')
  }
  return { claim, version }
}

async function archiveSha256(bytes) {
  if (!globalThis.crypto?.subtle) throw new Error('This browser cannot verify the source archive SHA-256.')
  const digest = await globalThis.crypto.subtle.digest('SHA-256', bytes)
  return Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('')
}

export default function GenesisImportReview({ artifactVersionId, buildRegistryId, targetAppId, onAccepted }) {
  const [review, setReview] = useState(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [notice, setNotice] = useState(null)
  const [retry, setRetry] = useState(0)
  const [downloadVerified, setDownloadVerified] = useState(false)
  const [acknowledged, setAcknowledged] = useState(false)
  const query = `?build_registry_id=${encodeURIComponent(buildRegistryId)}`
  const path = `/api/studio/build/artifacts/${encodeURIComponent(artifactVersionId)}`

  useEffect(() => {
    const controller = new AbortController()
    setLoading(true)
    setError(null)
    setNotice(null)
    setReview(null)
    setDownloadVerified(false)
    setAcknowledged(false)
    async function load() {
      try {
        const response = await studioFetch(`${path}/review${query}`, { signal: controller.signal })
        const body = await response.json().catch(() => null)
        if (!response.ok) throw new Error(responseError(body, `Genesis review unavailable: ${response.status}`))
        verifiedClaim(body, artifactVersionId, targetAppId)
        if (!controller.signal.aborted) setReview(body)
      } catch (reason) {
        if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : 'Genesis review unavailable.')
      } finally {
        if (!controller.signal.aborted) setLoading(false)
      }
    }
    load()
    return () => controller.abort()
  }, [artifactVersionId, buildRegistryId, targetAppId, path, query, retry])

  const claim = review?.genesis_import
  const version = review?.artifact_version
  const finalized = claim?.status === 'accepted'
    && version?.lifecycle_status === 'current'
    && version?.validation_status === 'passed'
    && version?.app_validation_status === 'passed'

  async function downloadArchive() {
    setBusy(true)
    setError(null)
    setNotice(null)
    setDownloadVerified(false)
    setAcknowledged(false)
    try {
      const response = await studioFetch(`${path}/download${query}`)
      if (!response.ok) {
        const body = await response.json().catch(() => null)
        throw new Error(responseError(body, `Source archive unavailable: ${response.status}`))
      }
      const bytes = await response.arrayBuffer()
      if (await archiveSha256(bytes) !== claim.bundle_sha256) {
        throw new Error('Downloaded source archive SHA-256 differs from the reserved Genesis source.')
      }
      const url = URL.createObjectURL(new Blob([bytes], { type: 'application/zip' }))
      try {
        const anchor = document.createElement('a')
        anchor.href = url
        anchor.download = `${claim.bundle_name}.zip`
        document.body.appendChild(anchor)
        anchor.click()
        anchor.remove()
      } finally {
        setTimeout(() => URL.revokeObjectURL(url), 0)
      }
      setDownloadVerified(true)
      setNotice('Source archive SHA-256 verified. Inspect the downloaded files before acknowledging review.')
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Source archive could not be verified.')
    } finally {
      setBusy(false)
    }
  }

  async function acceptGenesis() {
    if (!claim || finalized || !downloadVerified || !acknowledged || busy) return
    setBusy(true)
    setError(null)
    setNotice(null)
    try {
      const response = await studioFetch(`${path}/accept-genesis${query}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          confirm_exact_source_review: true,
          reviewed_bundle_sha256: claim.bundle_sha256,
          reviewed_manifest_sha256: claim.manifest_sha256,
        }),
      })
      const body = await response.json().catch(() => null)
      if (!response.ok) throw new Error(responseError(body, `Genesis acceptance failed: ${response.status}`))
      const confirmed = verifiedClaim({
        app_id: body?.app_id,
        artifact_version: body?.artifact_version,
        genesis_import: body?.genesis_import,
      }, artifactVersionId, targetAppId)
      if (body?.accepted !== true || body?.app_id !== targetAppId
        || confirmed.claim.bundle_sha256 !== claim.bundle_sha256
        || confirmed.claim.manifest_sha256 !== claim.manifest_sha256
        || confirmed.claim.status !== 'accepted'
        || confirmed.version.lifecycle_status !== 'current'
        || confirmed.version.validation_status !== 'passed'
        || confirmed.version.app_validation_status !== 'passed') {
        throw new Error('Genesis acceptance did not confirm this exact source. Reload the review before retrying.')
      }
      setReview({ ...review, genesis_import: body.genesis_import, artifact_version: body.artifact_version })
      setNotice('Imported source accepted as the brownfield baseline. It has not been deployed.')
      onAccepted?.()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : 'Genesis acceptance failed.')
    } finally {
      setBusy(false)
    }
  }

  if (loading) return <StudioLoadingState label="Loading imported Genesis review..." />
  if (!review) return (
    <div className="space-y-3">
      <StudioErrorState title="Imported Genesis review unavailable" message={error || 'The reserved source could not be loaded.'} />
      <ActionButton onClick={() => setRetry(value => value + 1)}>Retry loading review</ActionButton>
    </div>
  )

  return (
    <Panel title="Review imported Genesis source" subtitle="This exact repository snapshot becomes the baseline for later changes only after your review and validation.">
      <div className="space-y-4">
        <StatusPill tone={finalized ? 'success' : 'warning'}>
          {finalized ? 'Accepted baseline' : claim.status === 'accepted' ? 'Acceptance finalization pending' : 'Awaiting source review'}
        </StatusPill>
        <p className="text-sm text-muted-foreground">Reviewing this source does not activate or deploy it.</p>
        <dl className="space-y-2 text-sm">
          <div><dt className="font-medium text-foreground">Source</dt><dd className="break-all text-muted-foreground">{claim.source_id}</dd></div>
          <div><dt className="font-medium text-foreground">Source revision</dt><dd className="break-all text-muted-foreground">{claim.revision_id}</dd></div>
          <div><dt className="font-medium text-foreground">Source tree</dt><dd className="break-all text-muted-foreground">{claim.tree_id}</dd></div>
          <div><dt className="font-medium text-foreground">Archive SHA-256</dt><dd><code className="break-all text-foreground">{claim.bundle_sha256}</code></dd></div>
          <div><dt className="font-medium text-foreground">Manifest SHA-256</dt><dd><code className="break-all text-foreground">{claim.manifest_sha256}</code></dd></div>
        </dl>
        <div className="flex flex-wrap gap-2">
          <ActionButton variant="secondary" onClick={downloadArchive} disabled={busy}>Download verified source archive</ActionButton>
          <ActionButton variant="secondary" onClick={() => setRetry(value => value + 1)} disabled={busy}>Reload source review</ActionButton>
        </div>
        {!finalized && (
          <>
            <label className="flex items-start gap-2 text-sm text-foreground">
              <input type="checkbox" checked={acknowledged} disabled={!downloadVerified || busy} onChange={event => setAcknowledged(event.target.checked)} />
              <span>I inspected the downloaded source and confirm these archive and manifest SHA-256 values for this Genesis baseline.</span>
            </label>
            <ActionButton onClick={acceptGenesis} disabled={!downloadVerified || !acknowledged || busy}>
              {busy ? 'Working...' : 'Accept exact source as Genesis baseline'}
            </ActionButton>
          </>
        )}
        {notice && <p role="status" className="text-sm text-foreground">{notice}</p>}
        {error && <StudioErrorState title="Genesis source review failed" message={error} />}
      </div>
    </Panel>
  )
}
