import React from "react";
import { marked } from "marked";
import DOMPurify from "dompurify";

// Local debug flag helper (duplicated intentionally to avoid cross-file import churn)
const debugFlag = (k) => { try { return ['1','true','on','yes'].includes((localStorage.getItem(k)||'').toLowerCase()); } catch { return false; } };

 function ChatMessage({ message, message_from, agentName, isTokenMessage, isWarningMessage, isLatest = false, isStructuredCapable = false, structuredOutput = null, structuredSchema = null, isThinking = false, attachment = null, trace = null }) {
  // No local state needed: always show pretty structured output
  const traceItems = Array.isArray(trace) ? trace : [];
  const [traceOpen, setTraceOpen] = React.useState(false);
  
  // Debug (disabled by default): uncomment to trace renders
  if (debugFlag('mozaiks.debug_render')) {
    try {
      console.log('[RENDER] ChatMessage component', {
        from: message_from,
        agent: agentName,
        len: (message||'').length,
        structured: isStructuredCapable,
        latest: isLatest
      });
    } catch {}
  }

  // Structured output detection – strict: only use explicit structuredOutput prop
  const detectStructuredOutput = (text) => {
    if (structuredOutput && typeof structuredOutput === 'object') {
      return { type: 'json', data: structuredOutput, raw: JSON.stringify(structuredOutput), textBefore: '', textAfter: '' };
    }
    return null; // Do not attempt heuristic parsing
  };

  const renderStructuredData = (structuredData) => {
    if (!structuredData) return null;

    // Order fields using structuredSchema if provided (object of field -> typeName)
    const root = structuredData.data || {};
    const schemaOrder = structuredSchema ? Object.keys(structuredSchema) : Object.keys(root);

    const renderPrimitive = (val) => <span className="text-[var(--color-success)]">{String(val)}</span>;

    const renderAny = (val, depth = 0) => {
      if (depth > 4) return <span className="text-gray-500">…</span>;
      if (val === null) return <span className="text-gray-400">null</span>;
      if (Array.isArray(val)) {
        if (!val.length) return <span className="text-gray-400">[]</span>;
        return (
          <div className="flex flex-col gap-1 mt-1">
            {val.slice(0, 25).map((item, i) => (
              <div key={i} className="pl-2 border-l border-gray-600/50 text-xs">
                <span className="text-blue-300">[{i}]</span> {renderAny(item, depth + 1)}
              </div>
            ))}
            {val.length > 25 && <span className="text-gray-500 text-xs">… {val.length - 25} more</span>}
          </div>
        );
      }
      if (typeof val === 'object') {
        const entries = Object.entries(val);
        if (!entries.length) return <span className="text-gray-400">{{}}</span>;
        return (
          <div className="flex flex-col gap-1 mt-1">
            {entries.slice(0, 50).map(([k, v]) => (
              <div key={k} className="pl-2 border-l border-gray-600/50">
                <span className="text-purple-300 mr-1 text-xs font-medium">{k}:</span>
                <span className="text-xs">{renderAny(v, depth + 1)}</span>
              </div>
            ))}
            {entries.length > 50 && <span className="text-gray-500 text-xs">… {entries.length - 50} more</span>}
          </div>
        );
      }
      return renderPrimitive(val);
    };

    return (
      <div className="mt-3 rounded-md border border-gray-600/60 bg-gray-800/40 overflow-hidden">
        <div className="px-3 py-2 bg-gray-800/60 border-b border-gray-600/40 flex items-center gap-2">
          <span className="text-xs text-blue-300 font-mono tracking-wide">STRUCTURED OUTPUT</span>
          {structuredSchema && (
            <span className="text-[10px] text-gray-400 font-mono">{schemaOrder.length} fields</span>
          )}
        </div>
        <div className="p-3 flex flex-col gap-2">
          {schemaOrder.map((field) => {
            if (!(field in root)) return null;
            const value = root[field];
            const typeName = structuredSchema ? structuredSchema[field] : typeof value;
            return (
              <div key={field} className="bg-gray-900/30 rounded-md px-2 py-1">
                <div className="flex flex-col w-full">
                  <div className="flex items-center gap-2 mb-0.5">
                    <span className="text-xs font-semibold text-purple-200 font-mono">{field}</span>
                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-gray-700/60 text-gray-300 font-mono">{typeName}</span>
                  </div>
                  <div className="text-[11px] leading-4 text-gray-200 break-words">
                    {renderAny(value)}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      </div>
    );
  };
 
  // Special styling for token and warning messages
  const getSystemMessageStyles = () => {
    if (isTokenMessage) {
      return {
        container: "bg-gradient-to-r from-[rgba(var(--color-error-rgb),0.2)] to-[rgba(var(--color-error-rgb),0.1)] border border-[rgba(var(--color-error-rgb),0.4)]",
  text: "text-[var(--color-error)] text-slate-100",
        icon: "💰"
      };
    }
    if (isWarningMessage) {
      return {
        container: "bg-gradient-to-r from-[rgba(var(--color-warning-rgb),0.2)] to-[rgba(var(--color-warning-rgb),0.15)] border border-[rgba(var(--color-warning-rgb),0.4)]",
  text: "text-[var(--color-warning)] text-slate-100",
        icon: "⚠️"
      };
    }
    return null;
  };

  const systemStyles = getSystemMessageStyles();

  const renderMarkdown = (text) => {
    try {
      const html = marked.parse(String(text || ""), { breaks: true });
      return { __html: DOMPurify.sanitize(html) };
    } catch {
      return { __html: DOMPurify.sanitize(String(text || "")) };
    }
  };

  // Main message content with structured output support
  const renderMessageContent = (text) => {
    const structuredData = detectStructuredOutput(text);
    if (structuredData) {
      return (
        <div className="w-full">
          {structuredData.textBefore && (
            <div className="mb-3" dangerouslySetInnerHTML={renderMarkdown(structuredData.textBefore)} />
          )}
          {renderStructuredData(structuredData)}
          {structuredData.textAfter && (
            <div className="mt-3" dangerouslySetInnerHTML={renderMarkdown(structuredData.textAfter)} />
          )}
        </div>
      );
    }
    return <div className="w-full" dangerouslySetInnerHTML={renderMarkdown(text)} />;
  };

  // If there's truly no textual content and no structured output and it's not a token/warning system message, avoid rendering any bubble at all
  const hasRenderableContent = !!(message && String(message).trim().length) || (structuredOutput && typeof structuredOutput === 'object');
  
  // Special case: thinking message
  if (isThinking) {
    return (
      <div className="flex justify-start px-0 message-container">
        <div className="mt-1 agent-message message thinking-indicator">
          <div className="flex flex-col">
            <div className="message-header">
              <span className="name-pill agent">
                <span className="pill-avatar" aria-hidden>🤖</span> {agentName || 'Agent'}
              </span>
            </div>
            <div className="message-body w-full flex items-center gap-2">
              <div className="flex items-center gap-1.5">
                <div className="w-2 h-2 bg-[var(--color-primary-light)] rounded-full animate-bounce [animation-delay:0ms]"></div>
                <div className="w-2 h-2 bg-[var(--color-primary-light)] rounded-full animate-bounce [animation-delay:150ms]"></div>
                <div className="w-2 h-2 bg-[var(--color-primary-light)] rounded-full animate-bounce [animation-delay:300ms]"></div>
              </div>
              <span className="text-sm text-[var(--color-text-secondary)] italic ml-1">thinking...</span>
            </div>
          </div>
        </div>
      </div>
    );
  }
  
  if (!hasRenderableContent && !isTokenMessage && !isWarningMessage) {
    // Allow non-text system messages that are rendered via metadata (e.g., attachment indicators)
    if (!(message_from === 'system' && attachment)) return null;
  }

  // System attachment indicator branch
  if (message_from === 'system' && attachment) {
    const filename = attachment.filename || 'Attachment';
    const sizeBytes = attachment.size_bytes;
    const sizeLabel = (typeof sizeBytes === 'number' && sizeBytes >= 0)
      ? (sizeBytes > 1024 * 1024
        ? `${(sizeBytes / (1024 * 1024)).toFixed(1)} MB`
        : `${Math.max(1, Math.round(sizeBytes / 1024))} KB`)
      : null;
    return (
      <div className="flex justify-center px-0 message-container">
        <div className="md:rounded-[10px] rounded-[10px] w-4/5 mt-1 leading-4 techfont px-[12px] py-[8px] border border-[rgba(var(--color-primary-light-rgb),0.25)] bg-[rgba(0,0,0,0.35)]">
          <div className="flex flex-col">
            <div className="text-xs mb-1 opacity-75 flex items-center gap-2 text-[var(--color-text-secondary)]">
              <span aria-hidden>📎</span>
              <span>Attachment uploaded</span>
            </div>
            <div className="flex items-center justify-between gap-3">
              <div className="text-sm text-white/90 break-all">{filename}</div>
              {sizeLabel && (
                <div className="text-[10px] px-2 py-0.5 rounded bg-white/5 text-white/70 font-mono">{sizeLabel}</div>
              )}
            </div>
          </div>
        </div>
      </div>
    );
  }

  // User message branch
  if (message_from === 'user') {
    if (!message) return null; // nothing to show
    return (
      <div className="flex justify-start px-0 message-container">
        <div className={`mt-1 user-message message ${isLatest ? 'latest' : ''}`}>
          <div className="flex flex-col">
            <div className="message-header justify-end">
              <span className="name-pill user"><span className="pill-avatar" aria-hidden>🧑</span> You</span>
            </div>
            <div className="message-body w-full flex justify-start text-left font-semibold">
              {renderMessageContent(message)}
            </div>
          </div>
        </div>
      </div>
    );
  }

  // System token/warning branch
  if (systemStyles) {
    return (
      <div className="flex justify-center mr-3 message-container">
        <div className={`md:rounded-[10px] rounded-[10px] w-4/5 mt-1 leading-4 techfont px-[12px] py-[6px] ${systemStyles.container}`}>
          <div className="flex flex-col">
            <div className={`text-xs mb-1 opacity-75 flex items-center gap-2 ${systemStyles.text}`}>
              <span>{systemStyles.icon}</span>
              <span>System Notice</span>
            </div>
            {message && (
              <div className={`sm:w-full flex pr-2 oxanium md:text-[16px] text-[10px] font-bold ${systemStyles.text}`}>
                {renderMessageContent(message)}
              </div>
            )}
          </div>
        </div>
      </div>
    );
  }

  // Agent branch
  if (!message) return null; // ensure no empty container
  return (
    <div className="flex justify-start px-0 message-container">
      <div className={`mt-1 agent-message message ${isLatest ? 'latest' : ''}`}>
        <div className="flex flex-col">
          <div className="message-header justify-between">
            <span className="name-pill agent"><span className="pill-avatar" aria-hidden>🤖</span> {agentName || 'Agent'}</span>
            {traceItems.length > 0 && (
              <button
                type="button"
                onClick={() => setTraceOpen((v) => !v)}
                className="ml-2 inline-flex items-center gap-1 px-2 py-0.5 rounded-full border border-[rgba(148,163,184,0.25)] bg-[rgba(15,23,42,0.35)] text-[10px] font-mono text-[rgba(226,232,240,0.85)] hover:border-[rgba(148,163,184,0.45)] hover:bg-[rgba(15,23,42,0.5)] transition"
                title="Show backend trace"
              >
                Backend
                <span className="opacity-75">({traceItems.length})</span>
              </button>
            )}
          </div>
          <div className="message-body w-full flex font-semibold">
            {renderMessageContent(message)}
          </div>
          {traceOpen && traceItems.length > 0 && (
            <div className="mt-2 rounded-md border border-[rgba(148,163,184,0.2)] bg-[rgba(2,6,23,0.6)] px-3 py-2">
              <div className="flex items-center justify-between mb-1">
                <span className="text-[10px] font-mono tracking-wide text-[rgba(148,163,184,0.95)]">Backend trace</span>
                <button
                  type="button"
                  onClick={() => setTraceOpen(false)}
                  className="text-[10px] font-mono text-[rgba(148,163,184,0.9)] hover:text-white transition"
                >
                  Close
                </button>
              </div>
              <div className="flex flex-col gap-2">
                {traceItems.slice(-20).map((t, i) => (
                  <div key={`${t?.ts || 't'}-${i}`} className="rounded border border-[rgba(148,163,184,0.18)] bg-[rgba(15,23,42,0.35)] px-2 py-1">
                    <div className="flex items-center justify-between gap-3">
                      <span className="text-[10px] font-mono text-[rgba(226,232,240,0.9)]">{t?.agent || t?.trace_agent || 'Trace'}</span>
                      <span className="text-[10px] font-mono text-[rgba(148,163,184,0.8)]">{t?.reason || ''}</span>
                    </div>
                    <pre className="mt-1 whitespace-pre-wrap text-[11px] leading-relaxed text-[rgba(226,232,240,0.8)] font-mono">
                      {String(t?.content || '')}
                    </pre>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
export default ChatMessage;
