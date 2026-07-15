import { useMemo } from "react";

function daysAgo(days) {
  return new Date(Date.now() - days * 86400000).toISOString();
}

function relativeTime(value) {
  if (!value) return "";
  const diff = Date.now() - new Date(value).getTime();
  const minutes = Math.max(0, Math.floor(diff / 60000));
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

function initials(value) {
  return String(value || "?")
    .split(/[\s._-]+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0])
    .join("")
    .toUpperCase();
}

function Avatar({ value }) {
  return (
    <span style={{
      alignItems: "center",
      background: "var(--muted, #f3f4f6)",
      borderRadius: "999px",
      display: "inline-flex",
      fontSize: 12,
      fontWeight: 700,
      height: 36,
      justifyContent: "center",
      width: 36,
    }}>
      {initials(value)}
    </span>
  );
}

function groupByApp(items) {
  const groups = new Map();
  for (const item of items) {
    const key = item.app_id || "app";
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(item);
  }
  return [...groups.entries()];
}

function Section({ title, isDemo, children }) {
  return (
    <section style={{ display: "grid", gap: 12 }}>
      <header style={{ display: "flex", gap: 8, alignItems: "center" }}>
        <h3 style={{ fontSize: 13, letterSpacing: 0, margin: 0, textTransform: "uppercase" }}>{title}</h3>
        {isDemo && <span style={{ fontSize: 11, opacity: 0.65 }}>demo data</span>}
      </header>
      {children}
    </section>
  );
}

const DEMO_FRIENDS = [
  { friendship_id: "friend_1", friend_user_id: "jada.kim", display_name: "Jada Kim", since: daysAgo(42) },
  { friendship_id: "friend_2", friend_user_id: "alex.river", display_name: "Alex Rivera", since: daysAgo(18) },
  { friendship_id: "friend_3", friend_user_id: "sam.chen", display_name: "Sam Chen", since: daysAgo(7) },
];

const DEMO_ACTIVITY = [
  { event_id: "activity_1", app_id: "community-hub", actor_id: "alex.river", subject_label: "accepted a friend request", created_at: daysAgo(0.2) },
  { event_id: "activity_2", app_id: "community-hub", actor_id: "jada.kim", subject_label: "published a post", created_at: daysAgo(1) },
  { event_id: "activity_3", app_id: "creator-club", actor_id: "sam.chen", subject_label: "commented on a post", created_at: daysAgo(2) },
];

const DEMO_POSTS = [
  { post_id: "post_1", app_id: "community-hub", author_id: "demo.user", body: "Working through a new community workflow today. The feed and friend requests are starting to feel cohesive.", reaction_count: 12, comment_count: 3, created_at: daysAgo(0.5) },
  { post_id: "post_2", app_id: "creator-club", author_id: "demo.user", body: "Testing creator updates with comments and reactions before wiring real seed data.", reaction_count: 8, comment_count: 2, created_at: daysAgo(4) },
];

export function FriendListTab({ data }) {
  const source = Array.isArray(data?.friends) ? data.friends : DEMO_FRIENDS;
  const isDemo = !Array.isArray(data?.friends);

  return (
    <Section title="Friends" isDemo={isDemo}>
      <div style={{ display: "grid", gap: 8 }}>
        {source.map((friend) => (
          <article key={friend.friendship_id || friend.friend_user_id} style={{ alignItems: "center", border: "1px solid var(--border-color, #d1d5db)", display: "flex", gap: 12, padding: 12 }}>
            <Avatar value={friend.display_name || friend.friend_user_id} />
            <div>
              <strong>{friend.display_name || friend.friend_user_id}</strong>
              <p style={{ margin: "4px 0 0", opacity: 0.7 }}>{friend.friend_user_id}</p>
            </div>
            {friend.since && <span style={{ marginLeft: "auto", opacity: 0.7 }}>{relativeTime(friend.since)}</span>}
          </article>
        ))}
      </div>
    </Section>
  );
}

export function ActivityFeedTab({ data }) {
  const source = Array.isArray(data?.events) ? data.events : DEMO_ACTIVITY;
  const isDemo = !Array.isArray(data?.events);
  const groups = useMemo(() => groupByApp(source), [source]);

  return (
    <Section title="Activity" isDemo={isDemo}>
      {groups.map(([appId, events]) => (
        <div key={appId} style={{ display: "grid", gap: 8 }}>
          <strong>{appId}</strong>
          {events.map((event) => (
            <article key={event.event_id} style={{ border: "1px solid var(--border-color, #d1d5db)", padding: 12 }}>
              <strong>{event.actor_id}</strong>
              <p style={{ margin: "4px 0" }}>{event.subject_label || event.activity_type}</p>
              <span style={{ opacity: 0.7 }}>{relativeTime(event.created_at)}</span>
            </article>
          ))}
        </div>
      ))}
    </Section>
  );
}

export function UserPostsTab({ data }) {
  const source = Array.isArray(data?.posts) ? data.posts : DEMO_POSTS;
  const isDemo = !Array.isArray(data?.posts);
  const groups = useMemo(() => groupByApp(source), [source]);

  return (
    <Section title="Posts" isDemo={isDemo}>
      {groups.map(([appId, posts]) => (
        <div key={appId} style={{ display: "grid", gap: 8 }}>
          <strong>{appId}</strong>
          {posts.map((post) => (
            <article key={post.post_id} style={{ border: "1px solid var(--border-color, #d1d5db)", padding: 12 }}>
              <strong>{post.author_id}</strong>
              <p>{post.body || post.body_preview}</p>
              <span style={{ opacity: 0.7 }}>
                {post.reaction_count || 0} reactions, {post.comment_count || 0} comments
              </span>
            </article>
          ))}
        </div>
      ))}
    </Section>
  );
}
