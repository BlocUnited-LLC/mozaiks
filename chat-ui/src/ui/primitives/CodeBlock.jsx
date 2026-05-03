/**
 * CodeBlock primitive — syntax-highlighted code display with copy action.
 *
 * Schema properties:
 *   id        {string}  — component id for event routing
 *   code      {string}  — source code content
 *   language  {string}  — e.g. "python", "javascript", "bash"
 *   filename  {string}  — optional file label shown in header
 *   title     {string}  — optional section title above the block
 *
 * Agent event: ui.codeblock.update
 *   payload: { component_id, code, language, filename }
 */

import { useState, useEffect } from 'react';
import { useAppEvent } from '../hooks/useAppEventBus.js';
import { cn } from '../lib/cn.js';

export function CodeBlock({ id, code: initialCode = '', language: initialLanguage, filename: initialFilename, title, className }) {
  const [code,     setCode]     = useState(initialCode);
  const [language, setLanguage] = useState(initialLanguage);
  const [filename, setFilename] = useState(initialFilename);
  const [copied,   setCopied]   = useState(false);

  useEffect(() => { setCode(initialCode); },     [initialCode]);
  useEffect(() => { setLanguage(initialLanguage); }, [initialLanguage]);
  useEffect(() => { setFilename(initialFilename); }, [initialFilename]);

  useAppEvent('ui.codeblock.update', id, (payload) => {
    if (payload.code     !== undefined) setCode(payload.code);
    if (payload.language !== undefined) setLanguage(payload.language);
    if (payload.filename !== undefined) setFilename(payload.filename);
  });

  const handleCopy = () => {
    navigator.clipboard.writeText(code).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  };

  const displayLabel = filename || (language ? language.toLowerCase() : null);

  return (
    <div className={cn('rounded-lg border border-border bg-card overflow-hidden', className)}>
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
      <div className="overflow-x-auto">
        <pre className="p-4 text-sm font-mono text-foreground leading-relaxed whitespace-pre">
          <code>{code}</code>
        </pre>
      </div>
    </div>
  );
}
