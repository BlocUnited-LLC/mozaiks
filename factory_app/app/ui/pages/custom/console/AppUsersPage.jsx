import { useState } from 'react'
import { useParams } from 'react-router-dom'

import { WorkspaceLayout } from '@mozaiks/chat-ui/workspace'
import {
  ActionButton,
  ConsoleErrorState,
  ConsoleInlineEmptyState,
  ConsoleLoadingState,
  Panel,
  StatusPill,
} from '../../../components/ConsoleShared.jsx'
import { AppConsoleHero, formatCompactNumber } from './AppConsoleChrome.jsx'
import { buildSubscriptionMix, getAppConsoleSnapshot, toArray } from './appConsoleDataHelpers.js'
import { useAppConsoleData } from './useAppConsoleData.js'

function downloadUsersCsv(appId, rows) {
  const headers = ['name', 'email', 'segment', 'status', 'subscription', 'last_seen']
  const lines = [
    headers.join(','),
    ...rows.map((row) => headers.map((header) => JSON.stringify(String(row?.[header] || ''))).join(',')),
  ]
  const blob = new Blob([lines.join('\n')], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = `${appId}-users.csv`
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
  URL.revokeObjectURL(url)
}

export default function AppUsersPage() {
  const { appId = 'workspace-app' } = useParams()
  const { data, loading, error, dataMode } = useAppConsoleData(appId)
  const [searchValue, setSearchValue] = useState('')
  const [selectedUserId, setSelectedUserId] = useState(null)

  if (loading) return <ConsoleLoadingState label="Loading app users…" />
  if (error || !data) return <ConsoleErrorState title="Users Unavailable" message={error || 'No user data returned.'} />

  const snapshot = getAppConsoleSnapshot(appId, data, dataMode)
  const usersRecord = snapshot.usersRecord
  const users = toArray(usersRecord?.users)
  const filteredUsers = users.filter((user) => {
    const search = searchValue.trim().toLowerCase()
    if (!search) return true
    return [user.name, user.email, user.segment, user.subscription]
      .filter(Boolean)
      .some((value) => String(value).toLowerCase().includes(search))
  })
  const selectedUser = filteredUsers.find((user) => user.id === selectedUserId) || filteredUsers[0] || null
  const subscriptionMix = buildSubscriptionMix(usersRecord)
  const recentActivity = toArray(data.activity).slice(0, 4)
  const summaryItems = [
    { id: 'total', label: 'Total Users', value: formatCompactNumber(usersRecord?.total_users, 'Pending'), detail: 'Known app users.' },
    { id: 'active', label: 'Active Users', value: formatCompactNumber(usersRecord?.active_users, 'Pending'), detail: 'Users active in the current window.' },
    { id: 'new', label: 'New Users', value: formatCompactNumber(usersRecord?.new_users, 'Pending'), detail: 'Newly activated users.' },
    { id: 'churned', label: 'Churned Users', value: formatCompactNumber(usersRecord?.churned_users, 'Pending'), detail: 'Users recently disengaged.' },
  ]

  return (
    <WorkspaceLayout>
      <div className="space-y-6">
        <AppConsoleHero
          appId={appId}
          summary={snapshot.summary}
          dataMode={dataMode}
          title="Users"
          currentSection="Users"
          subtitle="Review users, segments, subscriptions, and support signals."
          summaryItems={summaryItems}
        />

        <div className="grid gap-6 xl:grid-cols-[minmax(0,1.15fr)_minmax(0,0.85fr)]">
          <Panel
            title="People using this app"
            subtitle="Search by name, email, segment, or subscription."
            action={(
              <div className="flex flex-wrap gap-2">
                <ActionButton
                  variant="secondary"
                  size="sm"
                  disabled={filteredUsers.length === 0}
                  onClick={() => {
                    if (filteredUsers.length > 0) setSelectedUserId(filteredUsers[0].id)
                  }}
                >
                  View Profile
                </ActionButton>
                <ActionButton
                  variant="secondary"
                  size="sm"
                  disabled={filteredUsers.length === 0}
                  onClick={() => downloadUsersCsv(appId, filteredUsers)}
                >
                  Export Users
                </ActionButton>
              </div>
            )}
          >
            <div className="mb-4">
              <input
                type="search"
                value={searchValue}
                onChange={(event) => setSearchValue(event.target.value)}
                placeholder="Search users"
                className="w-full rounded-[var(--shell-control-radius,1rem)] border border-border/48 bg-card/36 px-4 py-3 text-sm text-foreground outline-none transition placeholder:text-muted-foreground/68 hover:border-border/70 focus:border-primary/42 focus:ring-2 focus:ring-primary/16"
              />
            </div>

            {filteredUsers.length > 0 ? (
              <div className="overflow-hidden rounded-2xl border border-border/42">
                <table className="min-w-full divide-y divide-border/32 text-sm">
                  <thead className="bg-background/34 text-left text-xs text-muted-foreground/84">
                    <tr>
                      <th className="px-4 py-3 font-semibold">User</th>
                      <th className="px-4 py-3 font-semibold">Segment</th>
                      <th className="px-4 py-3 font-semibold">Subscription</th>
                      <th className="px-4 py-3 font-semibold">Last Seen</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-border/28 bg-card/24">
                    {filteredUsers.map((user) => {
                      const active = selectedUser?.id === user.id
                      return (
                        <tr
                          key={user.id}
                          className={`cursor-pointer transition ${active ? 'bg-primary/10' : 'hover:bg-card/30'}`}
                          onClick={() => setSelectedUserId(user.id)}
                        >
                          <td className="px-4 py-3">
                            <div className="font-semibold text-foreground">{user.name || user.email}</div>
                            <div className="text-xs text-muted-foreground">{user.email}</div>
                          </td>
                          <td className="px-4 py-3 text-muted-foreground">{user.segment || 'Unassigned'}</td>
                          <td className="px-4 py-3">
                            <StatusPill tone={user.subscription === 'Enterprise' ? 'success' : user.subscription === 'Pro' || user.subscription === 'Growth' ? 'primary' : 'default'}>
                              {user.subscription || 'Unassigned'}
                            </StatusPill>
                          </td>
                          <td className="px-4 py-3 text-muted-foreground">{user.last_seen || 'Pending'}</td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            ) : (
              <ConsoleInlineEmptyState
                title="No user records yet"
                description="User profiles, segments, subscriptions, and support history will populate here once the app is live enough to accumulate real usage."
              />
            )}
          </Panel>

          <div className="space-y-6">
            <Panel
              title={selectedUser ? selectedUser.name || selectedUser.email : 'User profile'}
              subtitle="Selected user context."
            >
              {selectedUser ? (
                <div className="space-y-3 text-sm">
                  <div className="rounded-2xl border border-border/42 bg-card/30 px-4 py-3">
                    <div className="font-semibold text-foreground">{selectedUser.email}</div>
                    <div className="mt-2 text-muted-foreground">{selectedUser.segment || 'No segment assigned yet'}</div>
                  </div>
                  <div className="grid gap-3 sm:grid-cols-2">
                    <div className="rounded-2xl border border-border/42 bg-card/30 px-4 py-3">
                      <div className="text-[12px] font-medium text-muted-foreground/82">Status</div>
                      <div className="mt-2 font-semibold text-foreground">{selectedUser.status || 'Unknown'}</div>
                    </div>
                    <div className="rounded-2xl border border-border/42 bg-card/30 px-4 py-3">
                      <div className="text-[12px] font-medium text-muted-foreground/82">Subscription</div>
                      <div className="mt-2 font-semibold text-foreground">{selectedUser.subscription || 'Unassigned'}</div>
                    </div>
                  </div>
                </div>
              ) : (
                <ConsoleInlineEmptyState
                  title="No profiles to inspect yet"
                  description="Once users exist, selecting a row in the table will keep a lightweight app-scoped profile visible here."
                />
              )}
            </Panel>

            <Panel title="User segments" subtitle="Current user distribution.">
              {toArray(usersRecord?.segments).length > 0 ? (
                <div className="space-y-3">
                  {toArray(usersRecord?.segments).map((segment) => (
                    <div key={segment.label} className="flex items-center justify-between rounded-2xl border border-border/42 bg-card/30 px-4 py-3">
                      <div className="font-semibold text-foreground">{segment.label}</div>
                      <StatusPill tone="primary">{formatCompactNumber(segment.count, '0')}</StatusPill>
                    </div>
                  ))}
                </div>
              ) : (
                <ConsoleInlineEmptyState
                  title="Segments appear once user records exist"
                  description="Segment distribution will surface here when the app starts accumulating user profiles."
                />
              )}
            </Panel>

            <Panel title="Subscription mix" subtitle="Plan distribution by user.">
              {subscriptionMix.length > 0 ? (
                <div className="space-y-3">
                  {subscriptionMix.map((subscription) => (
                    <div key={subscription.label} className="flex items-center justify-between rounded-2xl border border-border/42 bg-card/30 px-4 py-3">
                      <div className="font-semibold text-foreground">{subscription.label}</div>
                      <div className="text-sm text-muted-foreground">{subscription.count} users</div>
                    </div>
                  ))}
                </div>
              ) : (
                <ConsoleInlineEmptyState
                  title="Subscription data is not populated yet"
                  description="This section will start to matter once billing or plan assignment becomes part of the live app workflow."
                />
              )}
            </Panel>
          </div>
        </div>

        <div className="grid gap-6 xl:grid-cols-2">
          <Panel title="Recent user-facing activity" subtitle="Latest user and subscription signals.">
            {recentActivity.length > 0 ? (
              <div className="space-y-3">
                {recentActivity.map((entry) => (
                  <div key={entry.id} className="rounded-2xl border border-border/42 bg-card/30 px-4 py-3">
                    <div className="flex items-center justify-between gap-3">
                      <div className="font-semibold text-foreground">{entry.title}</div>
                      <div className="text-xs text-muted-foreground">{entry.timestamp}</div>
                    </div>
                    <div className="mt-2 text-sm text-muted-foreground">{entry.detail}</div>
                  </div>
                ))}
              </div>
            ) : (
              <ConsoleInlineEmptyState
                title="No recent user activity"
                description="User-facing activity will appear here after the app has live users, subscription changes, or support events."
              />
            )}
          </Panel>

          <Panel title="Recent support and subscription notes" subtitle="Signals that may affect retention.">
            {toArray(usersRecord?.support_history).length > 0 ? (
              <div className="space-y-3">
                {toArray(usersRecord?.support_history).map((entry) => (
                  <div key={entry.id} className="rounded-2xl border border-border/42 bg-card/30 px-4 py-3">
                    <div className="font-semibold text-foreground">{entry.label}</div>
                    <div className="mt-2 text-sm text-muted-foreground">{entry.detail}</div>
                  </div>
                ))}
              </div>
            ) : (
              <ConsoleInlineEmptyState
                title="No support history recorded"
                description="Support events and subscription interventions will show up here once this app has live user operations."
              />
            )}
          </Panel>
        </div>
      </div>
    </WorkspaceLayout>
  )
}
