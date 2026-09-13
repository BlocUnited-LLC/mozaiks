import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'

import {
  CollectionToolbar,
  InlineEmptyState,
  ResourceList,
} from '@mozaiks/chat-ui/ui'
import { WorkspaceLayout } from '@mozaiks/chat-ui/workspace'
import {
  ActionButton,
  StatusPill,
  StudioErrorState,
  StudioLoadingState,
} from '../../ui/components/StudioShared.jsx'
import { WorkspaceStudioHero, formatCompactNumber } from './AppStudioChrome.jsx'
import { getAppDisplayDescription, getAppDisplayName } from './appStudioModel.js'
import { studioFetch } from './studioApi.js'
import { useWorkspaceStudioData } from './useWorkspaceStudioData.js'

const FILTER_OPTIONS = [
  { label: 'All', value: 'all' },
  { label: 'Needs reply', value: 'needs-reply' },
  { label: 'Responded', value: 'responded' },
  { label: 'Resolved', value: 'resolved' },
]

const DashboardIcon = () => (
  <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/>
  </svg>
)

function parseTimestamp(value) {
  if (!value) return 0
  const parsed = Date.parse(value)
  return Number.isNaN(parsed) ? 0 : parsed
}

function normalizeSupportRequest(record) {
  const id = record.request_id || record.id
  const appId = record.subject_app_id || record.app_id || record.appId || 'workspace'
  const subject = record.subject || record.page_title || String(record.message || 'Support request').slice(0, 80)
  const lastMessageByRole = record.last_message_by_role || record.messages?.[record.messages.length - 1]?.role || 'user'
  return {
    id,
    ticketId: String(record.request_id || record.ticketId || id || 'SUP').toUpperCase(),
    appId,
    appName: record.app_name || record.appName || record.app_label || appId,
    subject,
    status: record.status || 'open',
    severity: record.severity || 'low',
    requester: record.user_id || record.requester || 'workspace user',
    updatedAt: record.updated_at || record.created_at || record.updatedAt || new Date().toISOString(),
    lastMessageByRole,
  }
}

async function fetchSupportRequests({ scope = 'workspace', appId = null } = {}) {
  const payload = { status: 'all', limit: 200, scope }
  if (appId) payload.subject_app_id = appId
  const res = await studioFetch('/api/modules/workspace_support/list_support_requests', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.json()
}

async function fetchCurrentProfileAppId() {
  try {
    const res = await studioFetch('/api/me')
    if (!res.ok) return null
    const profile = await res.json()
    return profile?.app_id || profile?.appId || null
  } catch (_) {
    return null
  }
}

function statusForRow(row) {
  if (row.needsReplyCount > 0 && row.highPriorityCount > 0) {
    return { label: 'Needs reply', tone: 'destructive', bucket: 'needs-reply' }
  }
  if (row.needsReplyCount > 0) {
    return { label: 'Needs reply', tone: 'warning', bucket: 'needs-reply' }
  }
  if (row.respondedCount > 0) {
    return { label: 'Responded', tone: 'primary', bucket: 'responded' }
  }
  return { label: 'Resolved', tone: 'success', bucket: 'resolved' }
}

function appIdFromRecord(app) {
  return app?.app_id || app?.app?.app_id || app?.id || null
}

function buildSupportRows(apps, supportRequests) {
  const rowsByApp = new Map()

  for (const app of Array.isArray(apps) ? apps : []) {
    const id = appIdFromRecord(app)
    if (!id) continue
    const name = getAppDisplayName(app?.app || app) || id
    rowsByApp.set(id, {
      id,
      app,
      name,
      description: getAppDisplayDescription(app?.app || app) || 'No app description yet.',
      totalCount: 0,
      openCount: 0,
      resolvedCount: 0,
      needsReplyCount: 0,
      respondedCount: 0,
      highPriorityCount: 0,
      latestAt: 0,
    })
  }

  for (const request of supportRequests) {
    const appId = request.appId || 'workspace'
    if (!rowsByApp.has(appId)) {
      rowsByApp.set(appId, {
        id: appId,
        app: null,
        name: request.appName || appId,
        description: 'Support requests not attached to a registered app record.',
        totalCount: 0,
        openCount: 0,
        resolvedCount: 0,
        needsReplyCount: 0,
        respondedCount: 0,
        highPriorityCount: 0,
        latestAt: 0,
      })
    }

    const row = rowsByApp.get(appId)
    const timestamp = parseTimestamp(request.updatedAt)
    row.totalCount += 1
    row.latestSubject = timestamp >= row.latestAt ? request.subject : row.latestSubject
    row.latestRequester = timestamp >= row.latestAt ? request.requester : row.latestRequester
    if (request.status === 'resolved') row.resolvedCount += 1
    else {
      row.openCount += 1
      if (request.lastMessageByRole === 'operator') row.respondedCount += 1
      else row.needsReplyCount += 1
    }
    if (request.status !== 'resolved' && request.lastMessageByRole !== 'operator' && request.severity === 'high') row.highPriorityCount += 1
    if (timestamp >= row.latestAt) row.latestAt = timestamp
  }

  return Array.from(rowsByApp.values())
    .filter((row) => row.totalCount > 0)
    .map((row) => {
      const supportStatus = statusForRow(row)
      return {
        ...row,
        supportStatus,
        dashboardHref: `/apps/${encodeURIComponent(row.id)}/support`,
        searchText: [
          row.name,
          supportStatus.label,
        ].filter(Boolean).join(' ').toLowerCase(),
      }
    })
    .sort((left, right) => (
      right.openCount - left.openCount ||
      right.highPriorityCount - left.highPriorityCount ||
      right.latestAt - left.latestAt ||
      left.name.localeCompare(right.name)
    ))
}

function matchesFilter(row, activeFilter) {
  if (activeFilter === 'all') return true
  return row.supportStatus.bucket === activeFilter
}

function AppCell({ row }) {
  return (
    <div>
      <div className="font-semibold text-foreground">{row.name}</div>
      <div className="mt-1 text-sm leading-6 text-muted-foreground">
        {formatCompactNumber(row.totalCount, '0')} support {row.totalCount === 1 ? 'chat' : 'chats'}
      </div>
      {row.latestSubject && (
        <div className="mt-1 truncate text-xs text-muted-foreground/70">
          Latest: {row.latestSubject}
        </div>
      )}
    </div>
  )
}

const columns = [
  {
    id: 'app',
    header: 'App',
    width: '52%',
    render: (row) => <AppCell row={row} />,
  },
  {
    id: 'status',
    header: 'Status',
    width: '18%',
    render: (row) => <StatusPill tone={row.supportStatus.tone}>{row.supportStatus.label}</StatusPill>,
  },
  {
    id: 'open',
    header: 'Open',
    width: '12%',
    cellClassName: 'text-muted-foreground tabular-nums',
    render: (row) => formatCompactNumber(row.openCount, '0'),
  },
  {
    id: 'action',
    header: '',
    width: '18%',
    headerClassName: 'text-right',
    cellClassName: 'text-right',
    render: (row) => (
      <ActionButton
        onClick={(event) => {
          event.stopPropagation()
          row.onDashboard?.(row)
        }}
        size="sm"
        variant="ghost"
        className="px-2 text-muted-foreground hover:text-foreground"
        aria-label="Dashboard"
      >
        <span className="inline-flex items-center gap-1.5">
          <DashboardIcon />
          <span>Dashboard</span>
        </span>
      </ActionButton>
    ),
  },
]

function SupportMobileItem({ row, onDashboard }) {
  return (
    <article
      className="cursor-pointer rounded-[1.15rem] border border-border/45 bg-card/34 p-4 shadow-sm shadow-black/5 transition-colors hover:bg-card/50"
      onClick={() => onDashboard(row)}
    >
      <div className="flex items-start justify-between gap-3">
        <AppCell row={row} />
        <ActionButton
          onClick={(event) => {
            event.stopPropagation()
            onDashboard(row)
          }}
          size="sm"
          variant="ghost"
          className="px-2 text-muted-foreground hover:text-foreground"
          aria-label="Dashboard"
        >
          <DashboardIcon />
        </ActionButton>
      </div>
      <div className="mt-3">
        <StatusPill tone={row.supportStatus.tone}>{row.supportStatus.label}</StatusPill>
      </div>
      <div className="mt-4 grid grid-cols-2 gap-3 text-sm">
        <div>
          <div className="text-[12px] text-muted-foreground">Support chats</div>
          <div className="mt-1 font-medium tabular-nums text-foreground">{formatCompactNumber(row.totalCount, '0')}</div>
        </div>
        <div>
          <div className="text-[12px] text-muted-foreground">Open</div>
          <div className="mt-1 font-medium tabular-nums text-foreground">{formatCompactNumber(row.openCount, '0')}</div>
        </div>
      </div>
    </article>
  )
}

export default function UserSupportPage() {
  const navigate = useNavigate()
  const { apps, loading, error, dataMode } = useWorkspaceStudioData('Support apps could not be loaded.')
  const [supportRequests, setSupportRequests] = useState([])
  const [supportError, setSupportError] = useState(null)
  const [searchValue, setSearchValue] = useState('')
  const [activeFilter, setActiveFilter] = useState('all')

  const loadSupportRequests = useCallback(async () => {
    setSupportError(null)
    try {
      const workspaceBody = await fetchSupportRequests()
      let requests = Array.isArray(workspaceBody?.requests) ? workspaceBody.requests : []

      if (requests.length === 0) {
        const appIds = new Set(
          (Array.isArray(apps) ? apps : [])
            .map(appIdFromRecord)
            .filter(Boolean),
        )
        const profileAppId = await fetchCurrentProfileAppId()
        if (profileAppId) appIds.add(profileAppId)

        const appBodies = await Promise.all(
          Array.from(appIds).map((appId) => fetchSupportRequests({ scope: 'app', appId })),
        )
        requests = appBodies.flatMap((body) => Array.isArray(body?.requests) ? body.requests : [])
      }

      setSupportRequests(requests.map(normalizeSupportRequest))
    } catch (err) {
      setSupportRequests([])
      setSupportError(err?.message || 'Support chats could not be loaded.')
    }
  }, [apps])

  useEffect(() => { loadSupportRequests() }, [loadSupportRequests])

  const supportSource = supportRequests
  const rows = useMemo(() => buildSupportRows(apps, supportSource), [apps, supportSource])
  const rowsWithActions = useMemo(
    () => rows.map((row) => ({ ...row, onDashboard: openDashboard })),
    [rows],
  )
  const filteredRows = useMemo(() => {
    const search = searchValue.trim().toLowerCase()
    return rowsWithActions.filter((row) => {
      const matchesSearch = !search || row.searchText.includes(search)
      return matchesSearch && matchesFilter(row, activeFilter)
    })
  }, [activeFilter, rowsWithActions, searchValue])

  const filterOptions = useMemo(() => (
    FILTER_OPTIONS.map((filter) => ({
      ...filter,
      count: filter.value === 'all'
        ? rows.length
        : rows.filter((row) => matchesFilter(row, filter.value)).length,
    }))
  ), [rows])

  const totalOpen = rows.reduce((total, row) => total + row.openCount, 0)
  const summaryItems = [
    { id: 'apps', label: 'Apps with support', value: formatCompactNumber(rows.length, '0') },
    { id: 'open', label: 'Open chats', value: formatCompactNumber(totalOpen, '0') },
  ]

  function openDashboard(row) {
    if (!row?.id) return
    navigate(`/apps/${encodeURIComponent(row.id)}/support`)
  }

  if (loading) return <StudioLoadingState label="Loading support dashboards..." />
  if (error || supportError) return <StudioErrorState title="Support Unavailable" message={error || supportError} />

  return (
    <WorkspaceLayout>
      <div className="space-y-6">
        <WorkspaceStudioHero
          title="Support"
          subtitle="Open an app to review, respond, resolve, or remove support chats."
          actions={null}
          onAction={null}
          summaryItems={summaryItems}
        />

        <section className="space-y-4">
          <CollectionToolbar
            searchValue={searchValue}
            onSearchChange={setSearchValue}
            searchPlaceholder="Search apps..."
            filters={filterOptions}
            activeFilter={activeFilter}
            onFilterChange={setActiveFilter}
            actions={null}
          />

          {filteredRows.length > 0 ? (
            <ResourceList
              items={filteredRows}
              columns={columns}
              getItemId={(row) => row.id}
              onRowClick={openDashboard}
              renderMobileItem={(row) => <SupportMobileItem row={row} onDashboard={openDashboard} />}
            />
          ) : (
            <InlineEmptyState
              title="No app support chats"
              description="Apps appear here when they have support activity."
            />
          )}
        </section>
      </div>
    </WorkspaceLayout>
  )
}
