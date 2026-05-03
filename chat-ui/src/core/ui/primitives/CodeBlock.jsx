// CodeBlock — syntax-highlighted code display primitive for the ui.render event system.
//
// Payload contract:
//   title?    string
//   language? string   e.g. "python", "javascript", "bash"
//   code      string
//   filename? string
import { useState } from 'react';

export default function CodeBlock({ payload = {} }) {
  const { title, language, code = '', filename } = payload;
  const [copied, setCopied] = useState(false);

  const handleCopy = () => {
    navigator.clipboard.writeText(code).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };

  const displayLabel = filename || (language ? language.toLowerCase() : null);

  return (
    <div className="rounded-lg border border-border bg-card overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2 border-b border-border bg-muted/40">
        <div className="flex items-center gap-2">
          {title && <p className="text-xs font-semibold text-foreground">{title}</p>}
          {displayLabel && (
            <span className="text-[11px] font-mono text-muted-foreground bg-muted px-1.5 py-0.5 rounded">
              {displayLabel}
            </span>
          )}
        </div>
        <button
          onClick={handleCopy}
          className="text-xs text-muted-foreground hover:text-foreground transition-colors px-2 py-0.5 rounded hover:bg-muted"
        >
          {copied ? 'Copied!' : 'Copy'}
        </button>
      </div>
      {/* Code body */}
      <div className="overflow-x-auto">
        <pre className="p-4 text-sm font-mono text-foreground leading-relaxed whitespace-pre">
          <code>{code}</code>
        </pre>
      </div>
    </div>
  );
}
