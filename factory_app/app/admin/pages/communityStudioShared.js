/**
 * communityStudioShared.js
 *
 * Shared helpers for community Studio pages: role constants, status/tone
 * mappers, date formatters, API call wrappers, and the composite
 * `loadAppCommunityData` loader used by AppCommunityPage,
 * AppGovernancePage, and AppCollaboratorsPage.
 */

// ---------------------------------------------------------------------------
// Module action transport
// ---------------------------------------------------------------------------

function _getAccessToken() {
  if (typeof window !== 'undefined' && window.mozaiksAuth?.getAccessToken) {
    return window.mozaiksAuth.getAccessToken()
  }
  if (typeof import.meta !== 'undefined' && import.meta.env?.VITE_MOCK_MODE === 'true') {
    return 'dev-token'
  }
  if (typeof localStorage === 'undefined') return null
  return (
    localStorage.getItem('mozaiks_access_token') ||
    localStorage.getItem('chatui_token') ||
    localStorage.getItem('access_token')
  )
}

function _authHeaders() {
  const token = _getAccessToken()
  return token ? { Authorization: `Bearer ${token}` } : {}
}

const _API_BASE =
  (typeof import.meta !== 'undefined' && import.meta.env?.VITE_API_URL) || ''

async function _callAction(moduleName, actionName, params = {}) {
  const response = await fetch(
    `${_API_BASE}/api/modules/${moduleName}/${actionName}`,
    {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Accept: 'application/json',
        ..._authHeaders(),
      },
      body: JSON.stringify(params || {}),
    },
  )
  if (!response.ok) {
    let body = null
    try {
      body = await response.json()
    } catch {
      // ignore parse failure
    }
    const err = new Error(
      body?.error ||
        body?.message ||
        `Module action failed: ${moduleName}.${actionName} ${response.status}`,
    )
    err.status = response.status
    if (body?.error_code) err.error_code = body.error_code
    if (body?.code) err.code = body.code
    throw err
  }
  return response.json()
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/** Ordered list of valid community membership roles. */
export const MEMBER_ROLES = ['owner', 'admin', 'contributor', 'reviewer', 'viewer']

/** Ordered list of valid governance proposal types. */
export const PROPOSAL_TYPES = ['general', 'policy', 'membership']

// ---------------------------------------------------------------------------
// User identity helpers
// ---------------------------------------------------------------------------

/**
 * Extract a stable user id string from a user object returned by
 * `useCurrentUser()`.
 */
export function getUserId(user) {
  return user?.id || user?.user_id || ''
}

// ---------------------------------------------------------------------------
// Date formatters
// ---------------------------------------------------------------------------

/** Format a date value as a short readable date string. Returns "-" for falsy. */
export function formatShortDate(value) {
  if (!value) return '-'
  return new Date(value).toLocaleDateString(undefined, {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
  })
}

/** Format a date value as a short readable date+time string. Returns "-" for falsy. */
export function formatShortDateTime(value) {
  if (!value) return '-'
  return new Date(value).toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  })
}

// ---------------------------------------------------------------------------
// Label helpers
// ---------------------------------------------------------------------------

/** Convert a snake_case value to a human-readable label. */
export function humanizeLabel(value) {
  return String(value || 'unknown').replace(/_/g, ' ')
}

// ---------------------------------------------------------------------------
// Status tone mappers (returns Mozaiks tone strings)
// ---------------------------------------------------------------------------

/** Tone for community/member status. */
export function memberStatusTone(status) {
  return (
    {
      active: 'success',
      pending: 'warning',
      archived: 'muted',
    }[String(status || '').toLowerCase()] || 'default'
  )
}

/** Tone for governance proposal status. */
export function proposalStatusTone(status) {
  return (
    {
      draft: 'default',
      open: 'primary',
      closed: 'muted',
    }[String(status || '').toLowerCase()] || 'default'
  )
}

/** Tone for governance proposal outcome status. */
export function outcomeStatusTone(status) {
  return (
    {
      approved: 'success',
      rejected: 'destructive',
    }[String(status || '').toLowerCase()] || 'muted'
  )
}

/** Tone for invitation status. */
export function invitationStatusTone(status) {
  return (
    {
      pending: 'warning',
      accepted: 'success',
      declined: 'muted',
      expired: 'destructive',
      revoked: 'muted',
    }[String(status || '').toLowerCase()] || 'default'
  )
}

/** Tone for delegation status. */
export function delegationStatusTone(status) {
  return (
    {
      active: 'success',
      revoked: 'muted',
    }[String(status || '').toLowerCase()] || 'default'
  )
}

// ---------------------------------------------------------------------------
// Role permission helpers
// ---------------------------------------------------------------------------

/** Returns true if the role has admin or owner permissions. */
export function isAdminRole(role) {
  return ['owner', 'admin'].includes(String(role || '').toLowerCase())
}

/** Returns true if the role can manage members (same as isAdminRole). */
export function canManageMembers(role) {
  return isAdminRole(role)
}

/**
 * Returns true if the role has at least contributor access
 * (can create proposals etc).
 */
export function isContributorRole(role) {
  return ['owner', 'admin', 'contributor', 'reviewer'].includes(
    String(role || '').toLowerCase(),
  )
}

// ---------------------------------------------------------------------------
// Community selection helpers
// ---------------------------------------------------------------------------

/**
 * Find the community to select by default.
 * Prefers the community with the given `communityId`, otherwise returns the
 * first community in the list.
 */
export function findDefaultCommunity(communities, communityId) {
  if (!Array.isArray(communities) || communities.length === 0) return null
  return communities.find((c) => c.community_id === communityId) || communities[0]
}

// ---------------------------------------------------------------------------
// URL helpers
// ---------------------------------------------------------------------------

/**
 * Append a `?community=<id>` (or `&community=<id>`) query param to a path.
 * Returns the path unchanged if communityId is falsy.
 */
export function withCommunityParam(path, communityId) {
  if (!communityId) return path
  const separator = path.includes('?') ? '&' : '?'
  return `${path}${separator}community=${encodeURIComponent(communityId)}`
}

// ---------------------------------------------------------------------------
// API call wrappers
// ---------------------------------------------------------------------------

/**
 * Call a module action and throw a structured error if the response
 * indicates failure.
 */
export async function callModuleAction(module, action, params = {}) {
  const result = await _callAction(module, action, params)
  if (result?.success === false) {
    throw new Error(result.error || result.error_code || 'Action failed')
  }
  if (typeof result?.error === 'string' && result.error) {
    throw new Error(result.error)
  }
  return result
}

/**
 * Fetch the app community access summary for the current viewer.
 * Throws if the summary is unavailable.
 */
export async function getAppCommunitySummary(appId, communityId = '') {
  const result = await callModuleAction('community_membership', 'get_app_community_summary', {
    app_id: appId,
    ...(communityId ? { community_id: communityId } : {}),
  })
  if (!result?.summary) {
    throw new Error('Community access summary unavailable.')
  }
  return result.summary
}

/**
 * Guard that throws when the viewer has a pending invitation.
 * Returns the summary unchanged when it is valid.
 */
export function assertNotPendingMember(summary) {
  if (summary?.viewer_membership_status === 'pending') {
    throw new Error(
      'Invitation pending. Accept the invitation before using the App community Studio.',
    )
  }
  return summary
}

/**
 * Load the composite community data bundle used by the app-scoped community
 * Studio pages (Community, Governance, Collaborators).
 *
 * Returns `{ app, summary, communities, community, selectedCommunityId }`.
 */
export async function loadAppCommunityData(appId, communityId = '') {
  const [summary, communitiesResult] = await Promise.all([
    getAppCommunitySummary(appId),
    callModuleAction('community_membership', 'list_communities', {
      app_id: appId,
      limit: 200,
    }),
  ])

  const validatedSummary = assertNotPendingMember(summary)
  const app = { app_id: validatedSummary.app_id, name: validatedSummary.app_name }
  const communities = communitiesResult.communities || []
  const defaultCommunity = findDefaultCommunity(communities, communityId)

  if (!defaultCommunity) {
    return {
      app,
      summary: validatedSummary,
      communities,
      community: null,
      selectedCommunityId: '',
    }
  }

  const communityResult = await callModuleAction('community_membership', 'get_community', {
    community_id: defaultCommunity.community_id,
  })

  return {
    app,
    summary: validatedSummary,
    communities,
    community: communityResult.community || defaultCommunity,
    selectedCommunityId: defaultCommunity.community_id,
  }
}
