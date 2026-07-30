import { useMemo, useState } from 'react'
import { useParams } from 'react-router-dom'
import { Copy, Download, ExternalLink } from 'lucide-react'

import { WorkspaceLayout } from '@mozaiks/chat-ui/workspace'
import {
  ActionButton,
  LinkButton,
  Panel,
  StatusPill,
  StudioErrorState,
  StudioInlineEmptyState,
  StudioLoadingState,
} from '../../ui/components/StudioShared.jsx'
import { AppStudioHero, formatCompactNumber } from './AppStudioChrome.jsx'
import { getAppStudioSnapshot, toArray } from './appStudioDataHelpers.js'
import { useAppStudioData } from './useAppStudioData.js'

function userKey(user) {
  return user?.id || user?.email || user?.name || 'user'
}

function userDisplayName(user) {
  return user?.name || user?.email || 'Unnamed user'
}

function userStatusTone(status) {
  const value = String(status || '').toLowerCase()
  if (value === 'active') return 'success'
  if (value === 'inactive') return 'warning'
  if (value === 'suspended' || value === 'blocked') return 'destructive'
  return 'default'
}

function planTone(plan) {
  const value = String(plan || '').toLowerCase()
  if (value === 'enterprise') return 'success'
  if (value === 'pro' || value === 'growth') return 'primary'
  return 'default'
}

function userInitials(user) {
  const name = userDisplayName(user)
  const parts = name.split(/\s+/).filter(Boolean)
  if (parts.length >= 2) return `${parts[0][0]}${parts[1][0]}`.toUpperCase()
  return name.slice(0, 2).toUpperCase()
}

function downloadUsersCsv(appId, rows) {
  const headers = ['name', 'email', 'status', 'subscription', 'segment', 'last_seen']
  const lines = [
    headers.join(','),
    ...rows.map((row) => headers.map((header) => JSON.stringify(String(row?.[header] || ''))).join(',')),
  ]
  const blob = new Blob([lines.join('\n')], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = `${appId}-access-users.csv`
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
  URL.revokeObjectURL(url)
}

async function copyToClipboard(value) {
  if (!value || typeof navigator === 'undefined' || !navigator.clipboard?.writeText) return false
  await navigator.clipboard.writeText(value)
  return true
}

export default function AppAccessPage() {
  const { appId = 'workspace-app' } = useParams()
  const { data, loading, error, dataMode } = useAppStudioData(appId)
  const [searchValue, setSearchValue] = useState('')
  const [selectedUserId, setSelectedUserId] = useState(null)
  const [copiedUserId, setCopiedUserId] = useState(null)

  const snapshot = useMemo(
    () => (data ? getAppStudioSnapshot(appId, data, dataMode) : null),
    [appId, data, dataMode],
  )

  const usersRecord = snapshot?.usersRecord
  const users = useMemo(() => toArray(usersRecord?.users), [usersRecord])
  const filteredUsers = useMemo(() => {
    const search = searchValue.trim().toLowerCase()
    if (!search) return users
    return users.filter((user) => [user.name, user.email, user.status, user.subscription, user.segment]
      .filter(Boolean)
      .some((value) => String(value).toLowerCase().includes(search)))
  }, [searchValue, users])

  const selectedUser = useMemo(() => {
    return filteredUsers.find((user) => userKey(user) === selectedUserId) || filteredUsers[0] || null
  }, [filteredUsers, selectedUserId])

  if (loading) return <StudioLoadingState label="Loading app access..." />
  if (error || !data || !snapshot) {
    return <StudioErrorState title="App Users Unavailable" message={error || 'No access data returned.'} />
  }

  const activeUsers = users.filter((user) => String(user.status || '').toLowerCase() === 'active').length
  const attentionUsers = users.filter((user) => {
    const status = String(user.status || '').toLowerCase()
    return status && status !== 'active'
  }).length
  const assignedPlans = new Set(users.map((user) => user.subscription).filter(Boolean)).size
  const unassignedPlans = users.filter((user) => !user.subscription).length

  const summaryItems = [
    {
      id: 'managed',
      label: 'Managed Accounts',
      value: formatCompactNumber(usersRecord?.total_users ?? users.length, '0'),
      detail: `${formatCompactNumber(filteredUsers.length, '0')} visible`,
    },
    {
      id: 'active',
      label: 'Active Access',
      value: formatCompactNumber(usersRecord?.active_users ?? activeUsers, '0'),
      detail: attentionUsers > 0 ? `${attentionUsers} need review` : 'No access exceptions',
    },
    {
      id: 'plans',
      label: 'Assigned Plans',
      value: assignedPlans > 0 ? formatCompactNumber(assignedPlans, '0') : 'None',
      detail: unassignedPlans > 0 ? `${unassignedPlans} unassigned` : 'Plan state complete',
    },
  ]

  async function handleCopyEmail(user) {
    const copied = await copyToClipboard(user?.email)
    if (!copied) return
    const id = userKey(user)
    setCopiedUserId(id)
    window.setTimeout(() => setCopiedUserId((current) => (current === id ? null : current)), 1600)
  }

  return (
    <WorkspaceLayout>
      <div className="space-y-5">
        <AppStudioHero
          appId={appId}
          summary={snapshot.summary}
          dataMode={dataMode}
          title="Users"
          currentSection="Users"
          subtitle="Manage app accounts, user access state, and plan assignment."
          summaryItems={summaryItems}
          actions={[
            {
              id: 'export',
              label: 'Export',
              variant: 'outline',
              disabled: filteredUsers.length === 0,
            },
          ]}
          onAction={(id) => {
            if (id === 'export' && filteredUsers.length > 0) downloadUsersCsv(appId, filteredUsers)
          }}
        />

        <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_360px]">
          <Panel
            title="Account management"
            subtitle="Search accounts and select one to manage access context."
            action={(
              <ActionButton
                variant="outline"
                size="sm"
                disabled={filteredUsers.length === 0}
                onClick={() => downloadUsersCsv(appId, filteredUsers)}
              >
                <Download className="h-3.5 w-3.5" aria-hidden="true" />
                Export
              </ActionButton>
            )}
            className="min-w-0"
          >
            <div className="mb-3 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <input
                type="search"
                value={searchValue}
                onChange={(event) => setSearchValue(event.target.value)}
                placeholder="Search by name, email, status, or plan"
                className="h-10 w-full rounded-lg border border-border/48 bg-card/36 px-3 text-sm text-foreground outline-none transition placeholder:text-muted-foreground/68 hover:border-border/70 focus:border-primary/42 focus:ring-2 focus:ring-primary/16 sm:max-w-sm"
              />
              <div className="text-xs font-medium text-muted-foreground">
                {formatCompactNumber(filteredUsers.length, '0')} of {formatCompactNumber(users.length, '0')}
              </div>
            </div>

            {filteredUsers.length > 0 ? (
              <div className="overflow-x-auto rounded-lg border border-border/42">
                <table className="min-w-full divide-y divide-border/32 text-sm">
                  <thead className="bg-background/46 text-left text-xs text-muted-foreground">
                    <tr>
                      <th className="px-3 py-2.5 font-semibold">Account</th>
                      <th className="px-3 py-2.5 font-semibold">Access</th>
                      <th className="px-3 py-2.5 font-semibold">Plan</th>
                      <th className="px-3 py-2.5 font-semibold">Last activity</th>
                      <th className="px-3 py-2.5 text-right font-semibold">Actions</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border/28 bg-card/18">
                    {filteredUsers.map((user) => {
                      const id = userKey(user)
                      const active = selectedUser && userKey(selectedUser) === id
                      return (
                        <tr
                          key={id}
                          className={`transition ${active ? 'bg-primary/8' : 'hover:bg-card/36'}`}
                        >
                          <td className="px-3 py-3">
                            <button
                              type="button"
                              className="flex min-w-0 items-center gap-3 text-left"
                              onClick={() => setSelectedUserId(id)}
                            >
                              <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md border border-border/40 bg-background/60 text-xs font-semibold text-muted-foreground">
                                {userInitials(user)}
                              </span>
                              <span className="min-w-0">
                                <span className="block truncate font-semibold text-foreground">{userDisplayName(user)}</span>
                                <span className="block truncate text-xs text-muted-foreground">{user.email || 'No email'}</span>
                              </span>
                            </button>
                          </td>
                          <td className="px-3 py-3">
                            <StatusPill tone={userStatusTone(user.status)}>{user.status || 'Unknown'}</StatusPill>
                          </td>
                          <td className="px-3 py-3">
                            <StatusPill tone={planTone(user.subscription)}>{user.subscription || 'Unassigned'}</StatusPill>
                          </td>
                          <td className="px-3 py-3 text-muted-foreground">{user.last_seen || 'Pending'}</td>
                          <td className="px-3 py-3">
                            <div className="flex justify-end gap-2">
                              <ActionButton
                                variant="ghost"
                                size="sm"
                                onClick={() => setSelectedUserId(id)}
                              >
                                Details
                              </ActionButton>
                              <ActionButton
                                variant="outline"
                                size="sm"
                                disabled={!user.email}
                                onClick={() => handleCopyEmail(user)}
                              >
                                <Copy className="h-3.5 w-3.5" aria-hidden="true" />
                                {copiedUserId === id ? 'Copied' : 'Email'}
                              </ActionButton>
                            </div>
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            ) : (
              <StudioInlineEmptyState
                title="No managed accounts yet"
                description="Accounts will appear here after this app has persisted access records."
              />
            )}
          </Panel>

          <div className="space-y-5">
            <Panel
              title={selectedUser ? userDisplayName(selectedUser) : 'Selected account'}
              subtitle="Account state and operational links."
            >
              {selectedUser ? (
                <div className="space-y-4">
                  <div className="flex items-start gap-3">
                    <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-lg border border-border/40 bg-card/45 text-sm font-semibold text-foreground">
                      {userInitials(selectedUser)}
                    </div>
                    <div className="min-w-0">
                      <div className="truncate text-sm font-semibold text-foreground">{selectedUser.email || 'No email'}</div>
                      <div className="mt-1 truncate text-sm text-muted-foreground">{selectedUser.segment || 'No segment assigned'}</div>
                    </div>
                  </div>

                  <div className="grid grid-cols-2 gap-2">
                    <div className="rounded-lg border border-border/36 bg-card/22 p-3">
                      <div className="text-xs font-medium text-muted-foreground">Access</div>
                      <div className="mt-2">
                        <StatusPill tone={userStatusTone(selectedUser.status)}>{selectedUser.status || 'Unknown'}</StatusPill>
                      </div>
                    </div>
                    <div className="rounded-lg border border-border/36 bg-card/22 p-3">
                      <div className="text-xs font-medium text-muted-foreground">Plan</div>
                      <div className="mt-2">
                        <StatusPill tone={planTone(selectedUser.subscription)}>{selectedUser.subscription || 'Unassigned'}</StatusPill>
                      </div>
                    </div>
                  </div>

                  <div className="rounded-lg border border-border/36 bg-card/22 p-3 text-sm">
                    <div className="flex items-center justify-between gap-3">
                      <span className="text-muted-foreground">Last activity</span>
                      <span className="font-medium text-foreground">{selectedUser.last_seen || 'Pending'}</span>
                    </div>
                  </div>

                  <div className="flex flex-wrap gap-2">
                    <ActionButton
                      variant="outline"
                      size="sm"
                      disabled={!selectedUser.email}
                      onClick={() => handleCopyEmail(selectedUser)}
                    >
                      <Copy className="h-3.5 w-3.5" aria-hidden="true" />
                      {copiedUserId === userKey(selectedUser) ? 'Copied' : 'Copy email'}
                    </ActionButton>
                    <LinkButton to={`/apps/${encodeURIComponent(appId)}/usage`} variant="outline" size="sm">
                      <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
                      Usage
                    </LinkButton>
                    <LinkButton to={`/apps/${encodeURIComponent(appId)}/activity`} variant="outline" size="sm">
                      <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
                      Activity
                    </LinkButton>
                  </div>
                </div>
              ) : (
                <StudioInlineEmptyState
                  title="No account selected"
                  description="Select an account from the table to review access context."
                />
              )}
            </Panel>

            <Panel title="Access state" subtitle="Current account exceptions.">
              <div className="space-y-2 text-sm">
                <div className="flex items-center justify-between rounded-lg border border-border/36 bg-card/22 px-3 py-2.5">
                  <span className="text-muted-foreground">Active accounts</span>
                  <span className="font-semibold text-foreground">{formatCompactNumber(activeUsers, '0')}</span>
                </div>
                <div className="flex items-center justify-between rounded-lg border border-border/36 bg-card/22 px-3 py-2.5">
                  <span className="text-muted-foreground">Needs review</span>
                  <StatusPill tone={attentionUsers > 0 ? 'warning' : 'success'}>
                    {formatCompactNumber(attentionUsers, '0')}
                  </StatusPill>
                </div>
                <div className="flex items-center justify-between rounded-lg border border-border/36 bg-card/22 px-3 py-2.5">
                  <span className="text-muted-foreground">Unassigned plans</span>
                  <StatusPill tone={unassignedPlans > 0 ? 'warning' : 'success'}>
                    {formatCompactNumber(unassignedPlans, '0')}
                  </StatusPill>
                </div>
              </div>
            </Panel>
          </div>
        </div>
      </div>
    </WorkspaceLayout>
  )
}
