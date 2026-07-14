/**
 * SocialProfileTabs — profile tab components for the social pack.
 *
 * Three tabs registered via friends/contracts/profile.yaml,
 * activity_feed/contracts/profile.yaml, and user_posts/contracts/profile.yaml:
 *
 *   FriendListTab   — friends list with friendship status
 *   ActivityFeedTab — per-user activity timeline, grouped by app
 *   UserPostsTab    — posts with reactions and comment counts, grouped by app
 *
 * Activity and Posts are grouped by app_id (stored on every document) so users
 * who are members of multiple apps see their content clearly separated.
 *
 * Each receives { tab, data } props from ProfilePage after the platform
 * hydrates the tab action via GET /api/me/profile-tabs.
 *
 * Demo data is shown when the collection is empty so the UI never looks blank.
 */

import { useState } from 'react'

// ── Helpers ───────────────────────────────────────────────────────────────────

function formatRelative(iso) {
  if (!iso) return ''
  try {
    const diff = Date.now() - new Date(iso).getTime()
    const mins = Math.floor(diff / 60000)
    if (mins < 1) return 'just now'
    if (mins < 60) return `${mins}m ago`
    const hrs = Math.floor(mins / 60)
    if (hrs < 24) return `${hrs}h ago`
    const days = Math.floor(hrs / 24)
    if (days < 7) return `${days}d ago`
    return new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
  } catch {
    return ''
  }
}

function ago(days) {
  return new Date(Date.now() - days * 864e5).toISOString()
}

function Avatar({ text, size = 'md', color = 0 }) {
  const PALETTES = [
    'bg-violet-500/20 text-violet-400',
    'bg-sky-500/20 text-sky-400',
    'bg-emerald-500/20 text-emerald-400',
    'bg-amber-500/20 text-amber-400',
    'bg-rose-500/20 text-rose-400',
    'bg-indigo-500/20 text-indigo-400',
  ]
  const palette = PALETTES[color % PALETTES.length]
  const sz = size === 'sm' ? 'h-8 w-8 text-[11px]' : size === 'lg' ? 'h-11 w-11 text-sm' : 'h-9 w-9 text-xs'
  return (
    <span className={`inline-flex shrink-0 items-center justify-center rounded-full font-bold ${palette} ${sz}`}>
      {String(text || '?').slice(0, 2).toUpperCase()}
    </span>
  )
}

/** Group an array by a key function, preserving insertion order. */
function groupBy(arr, keyFn) {
  const groups = []
  const map = {}
  for (const item of arr) {
    const key = keyFn(item)
    if (!map[key]) {
      const g = { key, items: [] }
      groups.push(g)
      map[key] = g
    }
    map[key].items.push(item)
  }
  return groups
}

function appLabel(appId) {
  if (!appId || appId === 'platform') return 'Platform'
  return appId
    .replace(/[-_]/g, ' ')
    .replace(/\b\w/g, c => c.toUpperCase())
}

// ── App group header (shared by Activity + Posts) ─────────────────────────────

function AppGroup({ label, count, isDemo, children }) {
  const [open, setOpen] = useState(true)
  return (
    <div className="mb-5">
      <button
        type="button"
        onClick={() => setOpen(v => !v)}
        className="flex items-center gap-2 w-full pb-2 border-b border-border/20 mb-3 group"
      >
        <span className={`text-[9px] text-muted-foreground/40 transition-transform ${open ? 'rotate-90' : ''}`}>▶</span>
        <span className="text-xs font-semibold uppercase tracking-widest text-muted-foreground group-hover:text-foreground transition-colors">
          {label}
        </span>
        <span className="inline-flex items-center rounded-full bg-muted/60 px-2 py-0.5 text-[10px] font-medium text-muted-foreground">
          {count}
        </span>
        {isDemo && (
          <span className="ml-1 inline-flex items-center rounded-full border border-primary/20 bg-primary/8 px-2 py-0.5 text-[10px] font-medium text-primary/60">
            demo
          </span>
        )}
      </button>
      {open && children}
    </div>
  )
}

// ── Demo data ─────────────────────────────────────────────────────────────────

const DEMO_FRIENDS = [
  { friendship_id: 'fs_demo1', friend_user_id: 'jada.kim',   since: ago(42), display_name: 'Jada Kim' },
  { friendship_id: 'fs_demo2', friend_user_id: 'alex.river', since: ago(18), display_name: 'Alex Rivera' },
  { friendship_id: 'fs_demo3', friend_user_id: 'sam.chen',   since: ago(7),  display_name: 'Sam Chen' },
  { friendship_id: 'fs_demo4', friend_user_id: 'priya.n',    since: ago(3),  display_name: 'Priya Nair' },
  { friendship_id: 'fs_demo5', friend_user_id: 'marco.v',    since: ago(1),  display_name: 'Marco Vitale' },
]

const DEMO_ACTIVITY = [
  { event_id: 'ae_d1', app_id: 'my-saas-app',    activity_type: 'friendship.created', actor_id: 'alex.river', subject_label: 'made a new connection',              created_at: ago(0.01) },
  { event_id: 'ae_d2', app_id: 'my-saas-app',    activity_type: 'post.published',     actor_id: 'jada.kim',   subject_label: 'shared a post about AI tooling',      created_at: ago(0.1)  },
  { event_id: 'ae_d3', app_id: 'my-saas-app',    activity_type: 'post.commented',     actor_id: 'sam.chen',   subject_label: 'commented on a post',                 created_at: ago(0.4)  },
  { event_id: 'ae_d4', app_id: 'community-hub',  activity_type: 'post.published',     actor_id: 'priya.n',    subject_label: 'shared a post about design systems',  created_at: ago(1)    },
  { event_id: 'ae_d5', app_id: 'community-hub',  activity_type: 'friendship.created', actor_id: 'marco.v',    subject_label: 'made a new connection',              created_at: ago(1.5)  },
  { event_id: 'ae_d6', app_id: 'community-hub',  activity_type: 'post.published',     actor_id: 'alex.river', subject_label: 'shared a post about workflow automation', created_at: ago(2) },
]

const DEMO_POSTS = [
  {
    post_id: 'p_demo1', app_id: 'my-saas-app',
    author_id: 'anonymous_user',
    body: 'Just shipped a new workflow that cuts content creation from 2 days to 45 minutes. The key was automating the brief → outline → draft loop with structured AI calls. Happy to share the config if anyone is interested.',
    reaction_count: 24, comment_count: 7, created_at: ago(0.3),
  },
  {
    post_id: 'p_demo2', app_id: 'my-saas-app',
    author_id: 'anonymous_user',
    body: 'Hot take: most "AI agents" are just stateful API chains with a retry loop. Real agents need memory, planning, and the ability to recover from failure gracefully. We\'re getting there though.',
    reaction_count: 61, comment_count: 14, created_at: ago(2),
  },
  {
    post_id: 'p_demo3', app_id: 'community-hub',
    author_id: 'anonymous_user',
    body: 'First week using Mozaiks in production. Impressions: module system is genuinely solid, the AppGenerator surprised me, and the profile tab extensibility is chef\'s kiss. Some rough edges in the UI shell but it\'s all fixable.',
    reaction_count: 38, comment_count: 9, created_at: ago(5),
  },
]

// ── FriendListTab ─────────────────────────────────────────────────────────────
// Friends are cross-app by nature (a friend is a friend regardless of which app
// the connection was made in), so no app grouping here.

export function FriendListTab({ data }) {
  const rawFriends = Array.isArray(data?.friends) ? data.friends : null
  const friends = rawFriends ?? DEMO_FRIENDS
  const isDemo = rawFriends === null

  return (
    <div>
      <div className="flex items-center gap-2 pb-3 border-b border-border/20 mb-4">
        <h3 className="text-xs font-semibold uppercase tracking-widest text-muted-foreground">Friends</h3>
        {friends.length > 0 && (
          <span className="inline-flex items-center rounded-full bg-muted/60 px-2 py-0.5 text-[10px] font-medium text-muted-foreground">
            {friends.length}
          </span>
        )}
        {isDemo && (
          <span className="ml-1 inline-flex items-center rounded-full border border-primary/20 bg-primary/8 px-2 py-0.5 text-[10px] font-medium text-primary/60">
            demo
          </span>
        )}
      </div>

      {friends.length === 0 ? (
        <div className="py-12 text-center">
          <p className="text-sm text-muted-foreground">No friends yet.</p>
          <p className="mt-1 text-xs text-muted-foreground/50">Send a friend request from someone's profile.</p>
        </div>
      ) : (
        <div className="grid gap-2 sm:grid-cols-2">
          {friends.map((f, i) => {
            const uid = f.display_name || f.friend_user_id || f.user_id || ''
            const handle = f.friend_user_id || f.user_id || ''
            return (
              <div
                key={f.friendship_id || uid}
                className="flex items-center gap-3 rounded-2xl border border-border/30 bg-card/40 px-4 py-3 hover:border-primary/20 hover:bg-card/70 transition-colors"
              >
                <Avatar text={uid} color={i} />
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-semibold text-foreground truncate leading-none">{uid}</p>
                  {handle !== uid && (
                    <p className="text-[11px] text-muted-foreground/60 mt-0.5 truncate">@{handle}</p>
                  )}
                  {f.since && (
                    <p className="text-[10px] text-muted-foreground/50 mt-1">
                      Friends since {formatRelative(f.since)}
                    </p>
                  )}
                </div>
                <button
                  type="button"
                  className="shrink-0 rounded-xl border border-border/40 bg-muted/30 px-3 py-1 text-[11px] font-medium text-muted-foreground hover:border-primary/30 hover:text-primary transition-colors"
                >
                  View
                </button>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

// ── ActivityFeedTab ───────────────────────────────────────────────────────────

const ACTIVITY_META = {
  'friendship.created': { label: 'made a new connection', icon: '🤝' },
  'post.published':     { label: 'shared a post',         icon: '✍️' },
  'post.commented':     { label: 'left a comment',        icon: '💬' },
}

function ActivityItem({ ev, index }) {
  const meta = ACTIVITY_META[ev.activity_type] || { label: ev.activity_type || 'did something', icon: '•' }
  const label = ev.subject_label || meta.label
  return (
    <div className="flex items-start gap-3 pl-2 py-2.5 rounded-xl hover:bg-muted/10 transition-colors">
      <div className="relative z-10 mt-0.5">
        <Avatar text={ev.actor_id} size="sm" color={index % 6} />
      </div>
      <div className="min-w-0 flex-1 pt-0.5">
        <p className="text-sm text-foreground leading-snug">
          <span className="font-semibold">{ev.actor_id}</span>
          {' '}
          <span className="text-muted-foreground">{label}</span>
          {' '}
          <span className="text-base leading-none">{meta.icon}</span>
        </p>
        <p className="text-[11px] text-muted-foreground/50 mt-0.5">{formatRelative(ev.created_at)}</p>
      </div>
    </div>
  )
}

export function ActivityFeedTab({ data }) {
  const rawEvents = Array.isArray(data?.events) ? data.events : null
  const events = rawEvents ?? DEMO_ACTIVITY
  const isDemo = rawEvents === null

  if (events.length === 0) {
    return (
      <div className="py-12 text-center">
        <p className="text-sm text-muted-foreground">No activity yet.</p>
      </div>
    )
  }

  const groups = groupBy(events, ev => ev.app_id || 'platform')

  // Single app — no grouping needed, just the timeline
  if (groups.length === 1) {
    return (
      <div>
        <div className="flex items-center gap-2 pb-3 border-b border-border/20 mb-4">
          <h3 className="text-xs font-semibold uppercase tracking-widest text-muted-foreground">Activity</h3>
          <span className="inline-flex items-center rounded-full bg-muted/60 px-2 py-0.5 text-[10px] font-medium text-muted-foreground">{events.length}</span>
          {isDemo && <span className="ml-1 inline-flex items-center rounded-full border border-primary/20 bg-primary/8 px-2 py-0.5 text-[10px] font-medium text-primary/60">demo</span>}
        </div>
        <div className="relative">
          <div className="absolute left-4 top-2 bottom-2 w-px bg-border/25" />
          <div className="space-y-1">
            {events.map((ev, i) => <ActivityItem key={ev.event_id} ev={ev} index={i} />)}
          </div>
        </div>
      </div>
    )
  }

  // Multiple apps — group with collapsible headers
  let globalIndex = 0
  return (
    <div>
      {groups.map(group => (
        <AppGroup
          key={group.key}
          label={appLabel(group.key)}
          count={group.items.length}
          isDemo={isDemo}
        >
          <div className="relative">
            <div className="absolute left-4 top-2 bottom-2 w-px bg-border/25" />
            <div className="space-y-1">
              {group.items.map(ev => {
                const idx = globalIndex++
                return <ActivityItem key={ev.event_id} ev={ev} index={idx} />
              })}
            </div>
          </div>
        </AppGroup>
      ))}
    </div>
  )
}

// ── UserPostsTab ──────────────────────────────────────────────────────────────

const REACTION_EMOJI = { like: '👍', love: '❤️', celebrate: '🎉', support: '🤝' }

function PostCard({ post, index }) {
  const [expanded, setExpanded] = useState(false)
  const body = post.body || post.body_preview || ''
  const isLong = body.length > 240
  const displayBody = isLong && !expanded ? body.slice(0, 240) + '…' : body

  return (
    <div className="rounded-2xl border border-border/30 bg-card/40 p-4 space-y-3 hover:border-border/50 transition-colors">
      <div className="flex items-center gap-2.5">
        <Avatar text={post.author_id} size="sm" color={index % 6} />
        <div>
          <p className="text-xs font-semibold text-foreground leading-none">{post.author_id}</p>
          <p className="text-[10px] text-muted-foreground/50 mt-0.5">{formatRelative(post.created_at)}</p>
        </div>
      </div>

      <p className="text-sm text-foreground leading-relaxed whitespace-pre-wrap">{displayBody}</p>
      {isLong && (
        <button
          type="button"
          onClick={() => setExpanded(v => !v)}
          className="text-xs text-primary hover:underline"
        >
          {expanded ? 'Show less' : 'Read more'}
        </button>
      )}

      {(post.reaction_count > 0 || post.comment_count > 0) && (
        <div className="flex items-center gap-3 pt-1 border-t border-border/15">
          {post.reaction_count > 0 && (
            <span className="flex items-center gap-1 text-xs text-muted-foreground">
              <span>{REACTION_EMOJI.like}</span>
              <span>{post.reaction_count}</span>
            </span>
          )}
          {post.comment_count > 0 && (
            <span className="flex items-center gap-1 text-xs text-muted-foreground">
              <span>💬</span>
              <span>{post.comment_count}</span>
            </span>
          )}
          <button type="button" className="ml-auto text-[11px] font-medium text-muted-foreground/60 hover:text-primary transition-colors">
            React
          </button>
          <button type="button" className="text-[11px] font-medium text-muted-foreground/60 hover:text-primary transition-colors">
            Comment
          </button>
        </div>
      )}
    </div>
  )
}

export function UserPostsTab({ data }) {
  const rawPosts = Array.isArray(data?.posts) ? data.posts : null
  const posts = rawPosts ?? DEMO_POSTS
  const isDemo = rawPosts === null

  if (posts.length === 0) {
    return (
      <div className="py-12 text-center">
        <p className="text-sm text-muted-foreground">No posts yet.</p>
      </div>
    )
  }

  const groups = groupBy(posts, p => p.app_id || 'platform')

  // Single app — no grouping needed
  if (groups.length === 1) {
    return (
      <div>
        <div className="flex items-center gap-2 pb-3 border-b border-border/20 mb-4">
          <h3 className="text-xs font-semibold uppercase tracking-widest text-muted-foreground">Posts</h3>
          <span className="inline-flex items-center rounded-full bg-muted/60 px-2 py-0.5 text-[10px] font-medium text-muted-foreground">{posts.length}</span>
          {isDemo && <span className="ml-1 inline-flex items-center rounded-full border border-primary/20 bg-primary/8 px-2 py-0.5 text-[10px] font-medium text-primary/60">demo</span>}
        </div>
        <div className="space-y-3">
          {posts.map((p, i) => <PostCard key={p.post_id} post={p} index={i} />)}
        </div>
      </div>
    )
  }

  // Multiple apps — group with collapsible headers
  let globalIndex = 0
  return (
    <div>
      {groups.map(group => (
        <AppGroup
          key={group.key}
          label={appLabel(group.key)}
          count={group.items.length}
          isDemo={isDemo}
        >
          <div className="space-y-3">
            {group.items.map(p => {
              const idx = globalIndex++
              return <PostCard key={p.post_id} post={p} index={idx} />
            })}
          </div>
        </AppGroup>
      ))}
    </div>
  )
}
