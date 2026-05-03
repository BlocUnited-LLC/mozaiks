/**
 * FileList primitive — file listing with status indicators and download links.
 *
 * Schema properties:
 *   id     {string}  — component id for event routing
 *   title  {string}  — optional heading
 *   files  {File[]}  — list of file records
 *
 * File schema:
 *   name    {string}  — display name
 *   type    {string}  — e.g. "Python", "YAML", "PDF"
 *   size    {string}  — e.g. "42 KB"
 *   url     {string}  — download/view URL
 *   status  {string}  — "ready" | "generating" | "error"
 *
 * Agent event: ui.filelist.update
 *   payload: { component_id, files }  — replaces file list
 *   payload: { component_id, name, status, url }  — updates a single file by name
 */

import { useState, useEffect } from 'react';
import { useAppEvent } from '../hooks/useAppEventBus.js';
import { cn } from '../lib/cn.js';

const STATUS_DOT = {
  ready:      'bg-success',
  generating: 'bg-warning animate-pulse',
  error:      'bg-destructive',
};
const STATUS_LABEL = {
  generating: 'text-warning',
  error:      'text-destructive',
};

const FILE_ICON = {
  python: '🐍', py: '🐍',
  javascript: '📜', js: '📜',
  typescript: '📘', ts: '📘',
  yaml: '⚙', yml: '⚙',
  json: '{}',
  pdf: '📄',
  markdown: '📝', md: '📝',
  html: '🌐',
  css: '🎨',
};

function getIcon(name, type) {
  const ext = name?.split('.').pop()?.toLowerCase() ?? '';
  return FILE_ICON[type?.toLowerCase()] ?? FILE_ICON[ext] ?? '📁';
}

export function FileList({ id, title, files: initialFiles = [], onAction, className }) {
  const [files, setFiles] = useState(initialFiles);

  useEffect(() => {
    setFiles(Array.isArray(initialFiles) ? initialFiles : []);
  }, [initialFiles]);

  useAppEvent('ui.filelist.update', id, (payload) => {
    if (Array.isArray(payload.files)) {
      setFiles(payload.files);
    } else if (payload.name) {
      setFiles((prev) => prev.map((f) =>
        f.name === payload.name ? { ...f, ...payload } : f
      ));
    }
  });

  return (
    <div className={cn('rounded-lg border border-border bg-card overflow-hidden', className)}>
      {title && (
        <div className="px-4 py-3 border-b border-border">
          <p className="text-sm font-semibold text-foreground">{title}</p>
        </div>
      )}
      <ul className="divide-y divide-border">
        {files.length === 0 && (
          <li className="px-4 py-6 text-sm text-center text-muted-foreground">No files.</li>
        )}
        {files.map((file, i) => {
          const dot   = STATUS_DOT[file.status   ?? 'ready'] ?? STATUS_DOT.ready;
          const label = STATUS_LABEL[file.status ?? 'ready'];
          return (
            <li key={i} className="flex items-center gap-3 px-4 py-3 hover:bg-muted/40 transition-colors">
              <span className="text-xl flex-shrink-0" aria-hidden="true">{getIcon(file.name, file.type)}</span>
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium text-foreground truncate">{file.name}</p>
                <div className="flex items-center gap-2 mt-0.5">
                  {file.type && <span className="text-xs text-muted-foreground">{file.type}</span>}
                  {file.size && <span className="text-xs text-muted-foreground">{file.size}</span>}
                  {file.status && file.status !== 'ready' && (
                    <span className={cn('text-xs', label)}>{file.status}</span>
                  )}
                </div>
              </div>
              <div className="flex items-center gap-2 flex-shrink-0">
                <span className={cn('w-1.5 h-1.5 rounded-full', dot)} />
                {file.url && file.status !== 'generating' && (
                  <a
                    href={file.url}
                    download={file.name}
                    onClick={() => onAction?.(`download:${file.name}`, [file])}
                    className="text-xs text-primary hover:underline"
                  >
                    Download
                  </a>
                )}
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
