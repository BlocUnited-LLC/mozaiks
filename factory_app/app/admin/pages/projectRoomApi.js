import { studioFetch } from './studioApi.js'
import {
  BUILD_STEPS,
  FAMILY_PAGE_LIMIT,
  HISTORY_PAGE_LIMIT,
  PROJECT_DOCUMENT_FAMILIES,
  familiesToFollowUp,
} from './projectRoomModel.js'

function historyUrl(buildRegistryId, params) {
  const query = new URLSearchParams({ build_registry_id: buildRegistryId, ...params })
  return `/api/studio/build/history?${query.toString()}`
}

async function readVersions(buildRegistryId, params, signal) {
  const response = await studioFetch(historyUrl(buildRegistryId, params), { signal })
  if (!response?.ok) throw new Error(`Saved documents unavailable: ${response?.status ?? 'no response'}`)
  const body = await response.json()
  return Array.isArray(body?.artifact_versions) ? body.artifact_versions : []
}

function firstPage(buildRegistryId, signal) {
  return readVersions(buildRegistryId, { limit: String(HISTORY_PAGE_LIMIT) }, signal)
}

function familyPage(buildRegistryId, family, signal) {
  return readVersions(buildRegistryId, { build_family: family, limit: String(FAMILY_PAGE_LIMIT) }, signal)
}

/**
 * Saved versions of the Project documents, in as few requests as the data allows.
 *
 * History sorts by version number across every family and caps the page, so
 * internal records (App Intelligence indexes) can push a document off a full
 * first page. One unfiltered request is enough when that page is not full;
 * otherwise each document family missing from it is read on its own.
 * A failed first request throws; a failed follow-up leaves its family unknown.
 */
export async function fetchProjectVersions(buildRegistryId, { signal } = {}) {
  const first = await firstPage(buildRegistryId, signal)
  const missing = familiesToFollowUp(first, PROJECT_DOCUMENT_FAMILIES, HISTORY_PAGE_LIMIT)
  const results = await Promise.allSettled(missing.map((family) => familyPage(buildRegistryId, family, signal)))
  const unknownFamilies = missing.filter((_, index) => results[index].status === 'rejected')
  const followUps = results.flatMap((result) => (result.status === 'fulfilled' ? result.value : []))
  return {
    versions: [...first, ...followUps],
    unknownFamilies,
    // Older versions may exist beyond a full page.
    capped: first.length >= HISTORY_PAGE_LIMIT
      || results.some((result) => result.status === 'fulfilled' && result.value.length >= FAMILY_PAGE_LIMIT),
    requests: 1 + missing.length,
  }
}

/**
 * My Apps: one request per listed app, plus one for the app version only when
 * a full first page left it out (it decides built or failed). Other build
 * families missing from a full page are reported unknown, so no next step is
 * guessed for them.
 */
export async function fetchRecentVersions(buildRegistryId, { signal } = {}) {
  const first = await firstPage(buildRegistryId, signal)
  const buildFamilies = BUILD_STEPS.map((step) => step.family)
  const missing = familiesToFollowUp(first, buildFamilies, HISTORY_PAGE_LIMIT)
  const extra = missing.includes('app_bundle') ? await familyPage(buildRegistryId, 'app_bundle', signal) : []
  return {
    versions: [...first, ...extra],
    unknownFamilies: missing.filter((family) => family !== 'app_bundle'),
    requests: missing.includes('app_bundle') ? 2 : 1,
  }
}
