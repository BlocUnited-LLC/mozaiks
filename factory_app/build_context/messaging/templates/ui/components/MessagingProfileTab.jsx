import { useEffect, useMemo, useState } from "react";
import { moduleAction } from "../lib/moduleApi.js";

function relativeTime(value) {
  if (!value) return "";
  const timestamp = new Date(value).getTime();
  if (!Number.isFinite(timestamp)) return "";
  const minutes = Math.max(0, Math.floor((Date.now() - timestamp) / 60000));
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

function threadTitle(thread) {
  return thread?.title || thread?.related_type || thread?.thread_type || "Conversation";
}

function messageTime(value) {
  const timestamp = new Date(value || "").getTime();
  return Number.isFinite(timestamp)
    ? new Date(timestamp).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })
    : "";
}

export default function MessagingProfileTab({ data }) {
  const threads = useMemo(() => (Array.isArray(data?.threads) ? data.threads : []), [data]);
  const friends = useMemo(() => (Array.isArray(data?.friends) ? data.friends : []), [data]);
  const [selectedId, setSelectedId] = useState(threads[0]?.thread_id || null);
  const [selectedThread, setSelectedThread] = useState(null);
  const [messages, setMessages] = useState([]);
  const [query, setQuery] = useState("");
  const [body, setBody] = useState("");
  const [loading, setLoading] = useState(false);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState(null);

  const visibleThreads = useMemo(() => {
    const value = query.trim().toLowerCase();
    return value ? threads.filter((thread) => threadTitle(thread).toLowerCase().includes(value)) : threads;
  }, [query, threads]);

  useEffect(() => {
    if (!selectedId) {
      setSelectedThread(null);
      setMessages([]);
      return undefined;
    }
    let active = true;
    setLoading(true);
    setError(null);
    moduleAction("messages", "get_thread", { thread_id: selectedId, message_limit: 100 })
      .then((result) => {
        if (!active) return;
        setSelectedThread(result?.thread || threads.find((thread) => thread.thread_id === selectedId) || null);
        setMessages(Array.isArray(result?.messages) ? result.messages : []);
        if (result?.thread?.thread_id) moduleAction("messages", "mark_thread_read", { thread_id: selectedId }).catch(() => {});
      })
      .catch(() => active && setError("This conversation is unavailable."))
      .finally(() => active && setLoading(false));
    return () => { active = false; };
  }, [selectedId, threads]);

  async function startConversation(friendId) {
    setError(null);
    try {
      const result = await moduleAction("messages", "find_or_create_dm", { participant_ids: [friendId] });
      const threadId = result?.thread?.thread_id;
      if (!threadId) throw new Error("Missing thread");
      setSelectedId(threadId);
    } catch (_error) {
      setError("Could not start conversation.");
    }
  }

  async function sendMessage(event) {
    event.preventDefault();
    const value = body.trim();
    if (!value || !selectedId || sending) return;
    setSending(true);
    setError(null);
    try {
      await moduleAction("messages", "send_message", { thread_id: selectedId, body: value });
      setBody("");
      const result = await moduleAction("messages", "get_thread", { thread_id: selectedId, message_limit: 100 });
      setMessages(Array.isArray(result?.messages) ? result.messages : []);
      setSelectedThread(result?.thread || selectedThread);
    } catch (_error) {
      setError("Could not send message.");
    } finally {
      setSending(false);
    }
  }

  return (
    <section style={{ border: "1px solid var(--border-color, #d1d5db)", display: "grid", gridTemplateColumns: "minmax(180px, 30%) 1fr", minHeight: 420, overflow: "hidden" }}>
      <aside style={{ borderRight: "1px solid var(--border-color, #d1d5db)" }}>
        <header style={{ borderBottom: "1px solid var(--border-color, #d1d5db)", padding: 12 }}>
          <strong>Messages</strong>
          <input aria-label="Search conversations" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search conversations" style={{ display: "block", marginTop: 10, maxWidth: "100%", width: "100%" }} />
        </header>
        {friends.length > 0 && <div style={{ borderBottom: "1px solid var(--border-color, #d1d5db)", padding: 12 }}>
          <strong style={{ fontSize: 12 }}>Friends</strong>
          {friends.map((friend) => <button key={friend.friend_user_id} type="button" onClick={() => startConversation(friend.friend_user_id)} style={{ background: "none", border: 0, cursor: "pointer", display: "block", padding: "8px 0", textAlign: "left", width: "100%" }}>{friend.display_name || friend.friend_user_id}</button>)}
        </div>}
        <div>
          {visibleThreads.map((thread) => <button key={thread.thread_id} type="button" onClick={() => setSelectedId(thread.thread_id)} style={{ background: selectedId === thread.thread_id ? "var(--muted, #f3f4f6)" : "transparent", border: 0, borderBottom: "1px solid var(--border-color, #d1d5db)", cursor: "pointer", display: "block", padding: 12, textAlign: "left", width: "100%" }}><strong>{threadTitle(thread)}</strong><span style={{ display: "block", fontSize: 12, opacity: 0.7 }}>{thread.last_message?.body_preview || "No messages yet"}</span><span style={{ fontSize: 12, opacity: 0.6 }}>{relativeTime(thread.updated_at)}</span></button>)}
          {visibleThreads.length === 0 && <p style={{ margin: 0, padding: 12, opacity: 0.7 }}>{query ? "No conversations match your search." : "No conversations yet."}</p>}
        </div>
      </aside>
      <div style={{ display: "flex", flexDirection: "column", minWidth: 0 }}>
        {selectedThread ? <>
          <header style={{ borderBottom: "1px solid var(--border-color, #d1d5db)", padding: 12 }}><strong>{threadTitle(selectedThread)}</strong></header>
          <div style={{ flex: 1, overflowY: "auto", padding: 16 }}>{loading ? <p>Loading conversation...</p> : error ? <p role="alert">{error}</p> : messages.length ? messages.map((message) => <div key={message.message_id} style={{ marginBottom: 10 }}><p style={{ margin: 0 }}>{message.body}</p><span style={{ fontSize: 11, opacity: 0.6 }}>{messageTime(message.created_at)}</span></div>) : <p>No messages yet. Say something.</p>}</div>
          <form onSubmit={sendMessage} style={{ borderTop: "1px solid var(--border-color, #d1d5db)", display: "flex", gap: 8, padding: 12 }}><input aria-label="Message conversation" value={body} onChange={(event) => setBody(event.target.value)} placeholder="Message conversation..." style={{ flex: 1 }} /><button type="submit" disabled={!body.trim() || sending}>{sending ? "Sending..." : "Send"}</button></form>
        </> : <div style={{ alignItems: "center", display: "flex", flex: 1, justifyContent: "center", padding: 24, textAlign: "center" }}><div><strong>Your conversation space</strong><p style={{ opacity: 0.7 }}>Select a conversation or a friend to start messaging.</p></div></div>}
      </div>
    </section>
  );
}
