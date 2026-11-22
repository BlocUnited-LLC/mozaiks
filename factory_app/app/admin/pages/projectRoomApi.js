import { studioFetch } from './studioApi.js'
import { PROJECT_DOCUMENT_FAMILIES } from './projectRoomModel.js'

function historyUrl(buildRegistryId, params) {
  const query = new URLSearchParams({ build_registry_id: buildRegistryId, ...params })
  return `/api/studio/build/history?${query.toString()}`
}

async function readVersions(response) {
  if (!response?.ok) throw new Error(`Saved documents unavailable: ${response?.status ?? 'no response'}`)
  const body = await response.json()
  return Array.isArray(body?.artifact_versions) ? body.artifact_versions : []
}

/**
 * Every saved version of each Project document.
 *
 * History sorts by version number across families and caps the page, so one
 * unfiltered request can drop a document behind many internal records. Each
 * document family is therefore read with its own `build_family` filter.
 */
export async function fetchProjectVersions(buildRegistryId, { signal, limit = 25 } = {}) {
  const results = await Promise.allSettled(PROJECT_DOCUMENT_FAMILIES.map((family) => (
    studioFetch(historyUrl(buildRegistryId, { build_family: family, limit: String(limit) }), { signal })
      .then(readVersions)
  )))
  const failed = results.filter((result) => result.status === 'rejected')
  if (failed.length === results.length) throw failed[0].reason
  return {
    versions: results.flatMap((result) => (result.status === 'fulfilled' ? result.value : [])),
    incomplete: failed.length > 0,
  }
}

/** One request per app for My Apps: the app's most recent saved versions. */
export async function fetchRecentVersions(buildRegistryId, { signal } = {}) {
  return readVersions(await studioFetch(historyUrl(buildRegistryId, { limit: '100' }), { signal }))
}
