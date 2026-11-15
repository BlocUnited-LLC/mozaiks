import { useEffect, useMemo, useRef, useState } from "react";
import { API_BASE, authHeaders, moduleAction } from "../lib/moduleApi.js";

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

function Section({ title, children }) {
  return (
    <section style={{ display: "grid", gap: 12 }}>
      <header style={{ display: "flex", gap: 8, alignItems: "center" }}>
        <h3 style={{ fontSize: 13, letterSpacing: 0, margin: 0, textTransform: "uppercase" }}>{title}</h3>
      </header>
      {children}
    </section>
  );
}

function EmptyState({ label }) {
  return (
    <div style={{ border: "1px solid var(--border-color, #d1d5db)", padding: 12, opacity: 0.75 }}>
      {label}
    </div>
  );
}

export function FriendListTab({ data, isOwner = false }) {
  const [friends, setFriends] = useState(Array.isArray(data?.friends) ? data.friends : []);
  const [requests, setRequests] = useState([]);
  const [nextCursor, setNextCursor] = useState(data?.next_cursor || null);
  const [recipient, setRecipient] = useState("");
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const pending = useRef(false);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => { setFriends(Array.isArray(data?.friends) ? data.friends : []); setNextCursor(data?.next_cursor || null); }, [data]);
  useEffect(() => {
    if (!isOwner) return undefined;
    let active = true;
    moduleAction("friends", "list_friend_requests", { direction: "incoming", status: "pending" })
      .then(result => { if (active) setRequests(result.requests || []); })
      .catch(() => { if (active) setError("Could not load friend requests."); });
    return () => { active = false; };
  }, [isOwner]);

  async function loadMoreFriends() {
    if (pending.current || !nextCursor) return;
    pending.current = true;
    setBusy(true);
    setError(null);
    try {
      const result = await moduleAction("friends", "list_friends", { before: nextCursor });
      if (!mounted.current) return;
      setFriends(current => [...current, ...(result.friends || []).filter(friend => !current.some(existing => existing.friendship_id === friend.friendship_id))]);
      setNextCursor(result.next_cursor || null);
    } catch (failure) {
      if (mounted.current) setError(failure.message);
    } finally {
      pending.current = false;
      if (mounted.current) setBusy(false);
    }
  }

  async function act(action, params) {
    if (pending.current) return;
    pending.current = true;
    setBusy(true);
    setError(null);
    const submittedUsername = params.username;
    try {
      if (action === "send_friend_request") {
        const response = await fetch(`${API_BASE}/api/users/${encodeURIComponent(params.username)}`, { headers: authHeaders() });
        if (!response.ok) throw new Error("Could not find that user.");
        const profile = await response.json();
        if (!profile.user_id) throw new Error("Could not find that user.");
        params = { recipient_id: profile.user_id };
      }
      if (!mounted.current) return;
      const result = await moduleAction("friends", action, params);
      if (!mounted.current) return;
      if (!result.success) throw new Error(result.error || "Could not update friends.");
      const [updatedFriends, updatedRequests] = await Promise.all([
        moduleAction("friends", "list_friends", {}),
        moduleAction("friends", "list_friend_requests", { direction: "incoming", status: "pending" }),
      ]);
      if (!mounted.current) return;
      setFriends(updatedFriends.friends || []);
      setNextCursor(updatedFriends.next_cursor || null);
      setRequests(updatedRequests.requests || []);
      if (action === "send_friend_request") setRecipient(current => current.trim() === submittedUsername ? "" : current);
    } catch (failure) {
      if (mounted.current) setError(failure.message);
    } finally {
      pending.current = false;
      if (mounted.current) setBusy(false);
    }
  }

  return (
    <Section title="Friends">
      {isOwner && <form onSubmit={event => { event.preventDefault(); if (recipient.trim()) act("send_friend_request", { username: recipient.trim() }); }}>
        <label>Friend's username <input required value={recipient} onChange={event => setRecipient(event.target.value)} /></label>
        <button type="submit" disabled={busy}>Send friend request</button>
      </form>}
      {error && <p role="alert">{error}</p>}
      {isOwner && requests.map(request => <article key={request.request_id}>
        <span>{request.requester_name || request.requester_id}</span>
        <button type="button" disabled={busy} onClick={() => act("accept_friend_request", {request_id: request.request_id})}>Accept request</button>
        <button type="button" disabled={busy} onClick={() => act("decline_friend_request", {request_id: request.request_id})}>Decline request</button>
      </article>)}
      {friends.length === 0 && <EmptyState label="No friends yet." />}
      <div style={{ display: "grid", gap: 8 }}>
        {friends.map(friend => <article key={friend.friendship_id || friend.friend_user_id} style={{ display: "flex", alignItems: "center", gap: 12, padding: 12 }}>
          <Avatar value={friend.display_name || friend.friend_user_id} />
          <strong>{friend.display_name || friend.friend_user_id}</strong>
          {friend.since && <span>{relativeTime(friend.since)}</span>}
          {isOwner && friend.acceptance_delivery_pending && friend.user_id === friend.recipient_id && <div>
            <span>Connection confirmation pending</span>
            <button type="button" disabled={busy} onClick={() => act("accept_friend_request", {request_id: friend.request_id})}>Send confirmation</button>
          </div>}
          {isOwner && <button type="button" disabled={busy} onClick={() => act("remove_friend", {friend_user_id: friend.friend_user_id})}>Remove friend</button>}
        </article>)}
      </div>
      {isOwner && nextCursor && <button type="button" disabled={busy} onClick={loadMoreFriends}>Load more friends</button>}
    </Section>
  );
}

export function ActivityFeedTab({ data }) {
  const source = Array.isArray(data?.events) ? data.events : [];
  const groups = useMemo(() => groupByApp(source), [source]);

  return (
    <Section title="Activity">
      {source.length === 0 && <EmptyState label="No activity yet." />}
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
  const source = Array.isArray(data?.posts) ? data.posts : [];
  const groups = useMemo(() => groupByApp(source), [source]);

  return (
    <Section title="Posts">
      {source.length === 0 && <EmptyState label="No posts yet." />}
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
