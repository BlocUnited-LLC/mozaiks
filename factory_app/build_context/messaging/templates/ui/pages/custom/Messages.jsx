import React, { useCallback, useEffect, useMemo, useState } from "react";
import { moduleAction } from "../../../lib/moduleApi.js";

const EMPTY_THREAD = { title: "", participantIds: "" };

function ThreadTitle({ thread }) {
  const title = thread?.title || thread?.related_type || thread?.thread_type || "Conversation";
  return <span>{title}</span>;
}

export default function Messages() {
  const [threads, setThreads] = useState([]);
  const [selectedThreadId, setSelectedThreadId] = useState(null);
  const [selectedMessages, setSelectedMessages] = useState([]);
  const [draft, setDraft] = useState("");
  const [newThread, setNewThread] = useState(EMPTY_THREAD);
  const [loading, setLoading] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError] = useState(null);

  const selectedThread = useMemo(
    () => threads.find((thread) => thread.thread_id === selectedThreadId) || null,
    [threads, selectedThreadId]
  );

  const loadThreads = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await moduleAction("messages", "list_threads", { limit: 50 });
      const nextThreads = result.threads || [];
      setThreads(nextThreads);
      if (!selectedThreadId && nextThreads.length > 0) {
        setSelectedThreadId(nextThreads[0].thread_id);
      }
    } catch (err) {
      setError(err?.message || "Could not load conversations.");
    } finally {
      setLoading(false);
    }
  }, [selectedThreadId]);

  const loadThread = useCallback(async (threadId) => {
    if (!threadId) return;
    setDetailLoading(true);
    setError(null);
    try {
      const result = await moduleAction("messages", "get_thread", {
        thread_id: threadId,
        message_limit: 100,
      });
      setSelectedMessages(result.messages || []);
      await moduleAction("messages", "mark_thread_read", { thread_id: threadId });
    } catch (err) {
      setError(err?.message || "Could not load the selected conversation.");
      setSelectedMessages([]);
    } finally {
      setDetailLoading(false);
    }
  }, []);

  useEffect(() => {
    loadThreads();
  }, [loadThreads]);

  useEffect(() => {
    loadThread(selectedThreadId);
  }, [selectedThreadId, loadThread]);

  async function createThread(event) {
    event.preventDefault();
    const title = newThread.title.trim();
    const participant_ids = newThread.participantIds
      .split(",")
      .map((value) => value.trim())
      .filter(Boolean);
    if (!title && participant_ids.length === 0) return;

    try {
      const result = await moduleAction("messages", "create_thread", {
        title,
        participant_ids,
        thread_type: participant_ids.length <= 1 ? "direct" : "group",
      });
      const thread = result.thread;
      if (thread) {
        setThreads((current) => [thread, ...current]);
        setSelectedThreadId(thread.thread_id);
        setNewThread(EMPTY_THREAD);
      }
    } catch (err) {
      setError(err?.message || "Could not create conversation.");
    }
  }

  async function sendMessage(event) {
    event.preventDefault();
    const body = draft.trim();
    if (!body || !selectedThreadId) return;
    try {
      const result = await moduleAction("messages", "send_message", {
        thread_id: selectedThreadId,
        body,
      });
      if (result.message) {
        setSelectedMessages((current) => [...current, result.message]);
      }
      setDraft("");
      await loadThreads();
    } catch (err) {
      setError(err?.message || "Could not send message.");
    }
  }

  return (
    <main className="messages-page" style={{ display: "grid", gridTemplateColumns: "320px 1fr", minHeight: "100vh" }}>
      <aside style={{ borderRight: "1px solid var(--border-color, #d1d5db)", padding: 16 }}>
        <header style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
          <h1 style={{ margin: 0, fontSize: 24 }}>Messages</h1>
          <button type="button" onClick={loadThreads} disabled={loading}>
            Refresh
          </button>
        </header>

        <form onSubmit={createThread} style={{ display: "grid", gap: 8, marginTop: 16 }}>
          <input
            value={newThread.title}
            onChange={(event) => setNewThread((current) => ({ ...current, title: event.target.value }))}
            placeholder="Thread title"
          />
          <input
            value={newThread.participantIds}
            onChange={(event) => setNewThread((current) => ({ ...current, participantIds: event.target.value }))}
            placeholder="Participant ids, comma separated"
          />
          <button type="submit">Start conversation</button>
        </form>

        {error && <p style={{ color: "var(--destructive, #dc2626)" }}>{error}</p>}

        <div style={{ display: "grid", gap: 8, marginTop: 16 }}>
          {threads.map((thread) => (
            <button
              key={thread.thread_id}
              type="button"
              onClick={() => setSelectedThreadId(thread.thread_id)}
              style={{
                padding: 12,
                textAlign: "left",
                border: selectedThreadId === thread.thread_id ? "1px solid var(--primary, #2563eb)" : "1px solid var(--border-color, #d1d5db)",
                background: selectedThreadId === thread.thread_id ? "var(--muted, #f3f4f6)" : "transparent",
              }}
            >
              <strong><ThreadTitle thread={thread} /></strong>
              <span style={{ display: "block", opacity: 0.7, fontSize: 12 }}>
                {thread.thread_type || "group"} - {thread.status || "open"}
              </span>
              {thread.last_message?.body_preview && (
                <span style={{ display: "block", marginTop: 4, fontSize: 12 }}>
                  {thread.last_message.body_preview}
                </span>
              )}
            </button>
          ))}
          {!loading && threads.length === 0 && <p>No conversations yet.</p>}
        </div>
      </aside>

      <section style={{ display: "grid", gridTemplateRows: "auto 1fr auto", minHeight: 0 }}>
        <header style={{ borderBottom: "1px solid var(--border-color, #d1d5db)", padding: 16 }}>
          <h2 style={{ margin: 0 }}>
            {selectedThread ? <ThreadTitle thread={selectedThread} /> : "Select a conversation"}
          </h2>
        </header>

        <div style={{ overflow: "auto", padding: 16 }}>
          {detailLoading && <p>Loading messages...</p>}
          {!detailLoading && selectedMessages.length === 0 && selectedThread && <p>No messages yet.</p>}
          {!selectedThread && <p>Pick a conversation or start a new one.</p>}
          {selectedMessages.map((message) => (
            <article key={message.message_id} style={{ marginBottom: 12 }}>
              <strong>{message.sender_role || "user"}</strong>
              <p style={{ margin: "4px 0 0" }}>{message.body}</p>
            </article>
          ))}
        </div>

        <form onSubmit={sendMessage} style={{ borderTop: "1px solid var(--border-color, #d1d5db)", display: "flex", gap: 8, padding: 16 }}>
          <input
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            disabled={!selectedThread}
            placeholder={selectedThread ? "Type a message" : "Select a conversation first"}
            style={{ flex: 1 }}
          />
          <button type="submit" disabled={!selectedThread || !draft.trim()}>
            Send
          </button>
        </form>
      </section>
    </main>
  );
}
