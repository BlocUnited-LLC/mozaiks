/**
 * SocialProfileTabs — profile tab components for the social pack.
 *
 * Three tabs registered via friends/contracts/profile.yaml,
 * activity_feed/contracts/profile.yaml, and user_posts/contracts/profile.yaml:
 *
 *   FriendListTab   — friends list with friendship status
 *   ActivityFeedTab — per-user activity timeline
 *   UserPostsTab    — posts with reactions and comment counts
 *
 * Each receives { tab, data } props from ProfilePage after the platform
 * hydrates the tab action via GET /api/me/profile-tabs.
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

function EmptyState({ message }) {
  return (
    <div className="py-12 text-center">
      <p className="text-sm text-muted-foreground italic">{message}</p>
    </div>
  )
}

function Avatar({ text, size = 'md' }) {
  const sz = size === 'sm' ? 'h-7 w-7 text-[10px]' : 'h-9 w-9 text-xs'
  return (
    <span className={`inline-flex shrink-0 items-center justify-center rounded-full bg-primary/10 font-bold text-primary ${sz}`}>
      {String(text || '?').slice(0, 2).toUpperCase()}
    </span>
  )
}

// ── FriendListTab ─────────────────────────────────────────────────────────────

export function FriendListTab({ data }) {
  const friends = Array.isArray(data?.friends) ? data.friends : []

  if (friends.length === 0) {
    return <EmptyState message="No friends yet." />
  }

  return (
    <div className="divide-y divide-border/30">
      {friends.map((f) => {
        const uid = f.friend_user_id || f.user_id || ''
        return (
          <div key={f.friendship_id || uid} className="flex items-center gap-3 py-3">
            <Avatar text={uid} />
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium text-foreground truncate">{uid}</p>
              {f.since && (
                <p className="text-xs text-muted-foreground">Friends since {formatRelative(f.since)}</p>
              )}
            </div>
          </div>
        )
      })}
    </div>
  )
}

// ── ActivityFeedTab ───────────────────────────────────────────────────────────

const ACTIVITY_LABELS = {
  'friendship.created': 'made a new connection',
  'post.published': 'shared a post',
  'post.commented': 'commented on a post',
}

export function ActivityFeedTab({ data }) {
  const events = Array.isArray(data?.events) ? data.events : []

  if (events.length === 0) {
    return <EmptyState message="No activity yet." />
  }

  return (
    <div className="space-y-3">
      {events.map((ev) => {
        const label = ACTIVITY_LABELS[ev.activity_type] || ev.activity_type || 'did something'
        return (
          <div key={ev.event_id} className="flex items-start gap-3">
            <Avatar text={ev.actor_id} size="sm" />
            <div className="min-w-0 flex-1 pt-0.5">
              <p className="text-sm text-foreground leading-snug">
                <span className="font-medium">{ev.actor_id}</span>
                {' '}
                <span className="text-muted-foreground">{label}</span>
                {ev.subject_label && ev.subject_label !== label ? (
                  <span className="text-muted-foreground"> — {ev.subject_label}</span>
                ) : null}
              </p>
              <p className="text-xs text-muted-foreground/60 mt-0.5">{formatRelative(ev.created_at)}</p>
            </div>
          </div>
        )
      })}
    </div>
  )
}

// ── UserPostsTab ──────────────────────────────────────────────────────────────

const REACTION_EMOJI = { like: '👍', love: '❤️', celebrate: '🎉', support: '🤝' }

function PostCard({ post }) {
  const [expanded, setExpanded] = useState(false)
  const body = post.body || post.body_preview || ''
  const isLong = body.length > 280
  const displayBody = isLong && !expanded ? body.slice(0, 280) + '…' : body

  return (
    <div className="rounded-2xl border border-border/40 bg-card/50 p-4 space-y-2">
      <p className="text-sm text-foreground leading-relaxed whitespace-pre-wrap">{displayBody}</p>
      {isLong && (
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="text-xs text-primary hover:underline"
        >
          {expanded ? 'Show less' : 'Read more'}
        </button>
      )}
      <div className="flex items-center gap-4 pt-1">
        {(post.reaction_count > 0 || post.comment_count > 0) && (
          <>
            {post.reaction_count > 0 && (
              <span className="text-xs text-muted-foreground">
                {REACTION_EMOJI.like} {post.reaction_count}
              </span>
            )}
            {post.comment_count > 0 && (
              <span className="text-xs text-muted-foreground">
                💬 {post.comment_count}
              </span>
            )}
          </>
        )}
        <span className="ml-auto text-xs text-muted-foreground/50">{formatRelative(post.created_at)}</span>
      </div>
    </div>
  )
}

export function UserPostsTab({ data }) {
  const posts = Array.isArray(data?.posts) ? data.posts : []

  if (posts.length === 0) {
    return <EmptyState message="No posts yet." />
  }

  return (
    <div className="space-y-3">
      {posts.map((p) => (
        <PostCard key={p.post_id} post={p} />
      ))}
    </div>
  )
}
