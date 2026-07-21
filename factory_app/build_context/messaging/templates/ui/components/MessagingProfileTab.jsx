import { useMemo } from "react";

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

function threadTitle(thread) {
  return thread?.title || thread?.related_type || thread?.thread_type || "Conversation";
}

export default function MessagingProfileTab({ data }) {
  const threads = useMemo(
    () => (Array.isArray(data?.threads) ? data.threads : []),
    [data]
  );

  if (threads.length === 0) {
    return (
      <section style={{ border: "1px solid var(--border-color, #d1d5db)", padding: 16 }}>
        <p style={{ margin: 0 }}>No conversations yet.</p>
      </section>
    );
  }

  return (
    <section style={{ display: "grid", gap: 8 }}>
      {threads.map((thread) => (
        <a
          key={thread.thread_id}
          href={`/messages?thread=${encodeURIComponent(thread.thread_id)}`}
          style={{
            border: "1px solid var(--border-color, #d1d5db)",
            color: "inherit",
            display: "block",
            padding: 12,
            textDecoration: "none",
          }}
        >
          <strong>{threadTitle(thread)}</strong>
          {thread.last_message?.body_preview && (
            <p style={{ margin: "4px 0", opacity: 0.75 }}>{thread.last_message.body_preview}</p>
          )}
          <span style={{ fontSize: 12, opacity: 0.65 }}>{relativeTime(thread.updated_at)}</span>
        </a>
      ))}
    </section>
  );
}
