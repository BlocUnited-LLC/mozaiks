/**
 * WorkspaceUsersPage — workspace-level user counts and per-app account overview.
 *
 * Shows total/active/new user aggregates across all apps and a per-app
 * breakdown table. Each row links to the per-app access page for the full
 * user list. User counts are sourced from /api/admin/users when available
 * and supplemented by the apps list.
 */

import { useEffect, useMemo, useState } from 'react'
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
import { getAppDisplayName } from './appStudioModel.js'
import { API_BASE } from './studioApi.js'
import { useWorkspaceApps } from './useWorkspaceApps.js'

function useWorkspaceUserSummary() {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    const controller = new AbortController()
    fetch(`${API_BASE}/api/admin/users`, { signal: controller.signal })
      .then((res) => (res.ok ? res.json() : null))
      .then((payload) => {
        if (!controller.signal.aborted) setData(payload)
      })
      .catch((err) => {
        if (!controller.signal.aborted && err?.name !== 'AbortError') {
          console.warn('[WorkspaceUsersPage] /api/admin/users not available:', err?.message)
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false)
      })
    return () => controller.abort()
  }, [])

  return { data, loading }
}

function UsersMobileItem({ row, onSelect }) {
  return (
    <article
      className="rounded-[1.15rem] border border-border/45 bg-card/34 p-4 shadow-sm shadow-black/5 cursor-pointer hover:bg-card/50 transition-colors"
      onClick={() => onSelect(row)}
    >
      <div className="font-semibold text-foreground">{row.name}</div>
      <div className="mt-3 grid grid-cols-3 gap-3 text-sm">
        <div>
          <div className="text-[12px] text-muted-foreground">Total</div>
          <div className="mt-1 font-medium text-foreground tabular-nums">
            {formatCompactNumber(row.totalUsers, '—')}
          </div>
        </div>
        <div>
          <div className="text-[12px] text-muted-foreground">Active</div>
          <div className="mt-1 font-medium text-foreground tabular-nums">
            {formatCompactNumber(row.activeUsers, '—')}
          </div>
        </div>
        <div>
          <div className="text-[12px] text-muted-foreground">New</div>
          <div className="mt-1 font-medium text-foreground tabular-nums">
            {row.newUsers > 0 ? `+${formatCompactNumber(row.newUsers, '0')}` : '—'}
          </div>
        </div>
      </div>
    </article>
  )
}

export default function WorkspaceUsersPage() {
  const navigate = useNavigate()
  const { apps, loading: appsLoading, error: appsError } = useWorkspaceApps('Could not load apps.')
  const { data: usersData, loading: usersLoading } = useWorkspaceUserSummary()
  const [searchValue, setSearchValue] = useState('')

  const appRows = useMemo(() => {
    const usersByApp = usersData?.by_app || {}
    const appsArray = Array.isArray(apps) ? apps : []
    return appsArray
      .map((app) => {
        const appId = app.app_id || app.app?.app_id || app.id
        const name = getAppDisplayName(app.app || app) || appId || 'Unknown app'
        const appUsers = usersByApp[appId] || {}
        return {
          id: appId,
          name,
          totalUsers: Number(appUsers.total_users || 0),
          activeUsers: Number(appUsers.active_users || 0),
          newUsers: Number(appUsers.new_users || 0),
          searchText: String(name).toLowerCase(),
        }
      })
      .filter((row) => Boolean(row.id))
      .sort((a, b) => b.activeUsers - a.activeUsers || b.totalUsers - a.totalUsers)
  }, [apps, usersData])

  const filteredRows = useMemo(() => {
    const search = searchValue.trim().toLowerCase()
    return search ? appRows.filter((row) => row.searchText.includes(search)) : appRows
  }, [appRows, searchValue])

  const totalUsers = usersData?.total_users ?? appRows.reduce((sum, row) => sum + row.totalUsers, 0)
  const activeUsers = usersData?.active_users ?? appRows.reduce((sum, row) => sum + row.activeUsers, 0)
  const newUsers = usersData?.new_users ?? appRows.reduce((sum, row) => sum + row.newUsers, 0)
  const appsWithUsers = appRows.filter((row) => row.totalUsers > 0).length

  const summaryItems = [
    { id: 'total', label: 'Total users', value: formatCompactNumber(totalUsers, '0') },
    { id: 'active', label: 'Active users', value: formatCompactNumber(activeUsers, '0') },
    { id: 'new', label: 'New users', value: formatCompactNumber(newUsers, '0') },
    {
      id: 'apps',
      label: 'Apps with users',
      value: formatCompactNumber(appsWithUsers, '0'),
      detail: `${formatCompactNumber(appRows.length, '0')} total apps`,
    },
  ]

  function handleRowSelect(row) {
    navigate(`/apps/${encodeURIComponent(row.id)}/access`)
  }

  const columns = [
    {
      id: 'app',
      header: 'App',
      width: '40%',
      render: (row) => (
        <div className="font-semibold text-foreground">{row.name}</div>
      ),
    },
    {
      id: 'total',
      header: 'Total users',
      width: '18%',
      cellClassName: 'text-muted-foreground tabular-nums',
      render: (row) => formatCompactNumber(row.totalUsers, '—'),
    },
    {
      id: 'active',
      header: 'Active',
      width: '18%',
      cellClassName: 'text-muted-foreground tabular-nums',
      render: (row) => formatCompactNumber(row.activeUsers, '—'),
    },
    {
      id: 'new',
      header: 'New',
      width: '14%',
      render: (row) => row.newUsers > 0
        ? <StatusPill tone="success">+{formatCompactNumber(row.newUsers, '0')}</StatusPill>
        : <span className="text-muted-foreground tabular-nums">—</span>,
    },
    {
      id: 'action',
      header: '',
      width: '10%',
      headerClassName: 'text-right',
      cellClassName: 'text-right',
      render: (row) => (
        <ActionButton
          size="sm"
          variant="ghost"
          className="text-muted-foreground hover:text-foreground"
          onClick={(event) => {
            event.stopPropagation()
            handleRowSelect(row)
          }}
        >
          Users
        </ActionButton>
      ),
    },
  ]

  if (appsLoading || usersLoading) return <StudioLoadingState label="Loading workspace users…" />
  if (appsError) return <StudioErrorState title="Workspace Users Unavailable" message={appsError} />

  return (
    <WorkspaceLayout>
      <div className="space-y-6">
        <WorkspaceStudioHero
          title="Users"
          subtitle="Active accounts and user engagement across your apps."
          summaryItems={summaryItems}
        />

        <section className="space-y-4">
          <CollectionToolbar
            searchValue={searchValue}
            onSearchChange={setSearchValue}
            searchPlaceholder="Search apps..."
            actions={null}
          />

          {filteredRows.length > 0 ? (
            <ResourceList
              items={filteredRows}
              columns={columns}
              getItemId={(row) => row.id}
              onRowClick={handleRowSelect}
              renderMobileItem={(row) => (
                <UsersMobileItem row={row} onSelect={handleRowSelect} />
              )}
              empty={{
                title: 'No apps match this search',
                description: 'Adjust the search term or clear the filter.',
              }}
            />
          ) : (
            <InlineEmptyState
              title="No user data yet"
              description="User counts appear after apps have active accounts. Select an app to manage its access."
            />
          )}
        </section>
      </div>
    </WorkspaceLayout>
  )
}
