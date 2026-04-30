// ==============================================================================
// FILE: ChatUI/src/workflows/AppGenerator/components/CodeEditorArtifact.js
// DESCRIPTION: Monaco code editor wrapper for AppWorkbench
// ==============================================================================

import React, { useCallback, useMemo, useState } from 'react';
import Editor from '@monaco-editor/react';
import { Check, Copy, Maximize2, Minimize2 } from 'lucide-react';

const languageMap = {
  js: 'javascript',
  jsx: 'javascript',
  ts: 'typescript',
  tsx: 'typescript',
  py: 'python',
  json: 'json',
  css: 'css',
  html: 'html',
  md: 'markdown',
  yml: 'yaml',
  yaml: 'yaml',
  sh: 'shell',
};

const getLanguageFromPath = (filePath, fallback = 'javascript') => {
  if (!filePath || typeof filePath !== 'string') return fallback;
  const ext = filePath.split('.').pop()?.toLowerCase();
  return languageMap[ext] || 'plaintext';
};

const CodeEditorArtifact = ({ filePath, content, onChange, config = {}, readOnly = false }) => {
  const editorCfg = useMemo(() => config?.artifacts?.['code-editor'] || {}, [config]);
  const [copied, setCopied] = useState(false);
  const [expanded, setExpanded] = useState(false);

  const language = useMemo(() => {
    const explicit = editorCfg.defaultLanguage;
    return getLanguageFromPath(filePath, explicit || 'javascript');
  }, [filePath, editorCfg]);

  const handleCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(content || '');
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch (e) {
      // ignore
    }
  }, [content]);

  const options = useMemo(
    () => ({
      readOnly: Boolean(readOnly || editorCfg.readOnly),
      minimap: { enabled: Boolean(editorCfg.showMinimap) },
      lineNumbers: editorCfg.showLineNumbers === false ? 'off' : 'on',
      fontSize: Number(editorCfg.fontSize) || 14,
      wordWrap: editorCfg.wordWrap || 'on',
      scrollBeyondLastLine: false,
      automaticLayout: true,
      tabSize: 2,
      formatOnPaste: Boolean(editorCfg.autoFormat),
      formatOnType: Boolean(editorCfg.autoFormat),
    }),
    [editorCfg, readOnly]
  );

  const container = ['rounded-xl overflow-hidden border border-white/10 bg-black/30', expanded ? 'fixed inset-4 z-50' : ''].join(
    ' '
  );

  return (
    <div className={container}>
      <div className="flex items-center justify-between px-4 py-2 bg-black/40 border-b border-white/10">
        <div className="flex items-center gap-3 min-w-0">
          <span className="text-xs text-[var(--color-text-muted)] font-mono truncate">{filePath || 'untitled'}</span>
          <span className="px-2 py-0.5 text-[10px] rounded bg-[rgba(var(--color-secondary-rgb),0.18)] text-[var(--color-secondary-light)] border border-[rgba(var(--color-secondary-rgb),0.25)]">
            {language}
          </span>
        </div>

        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={handleCopy}
            className="p-2 rounded-lg bg-white/5 hover:bg-white/10 text-[var(--color-text-secondary)] hover:text-white transition-colors"
            title="Copy"
          >
            {copied ? <Check className="w-4 h-4 text-[var(--color-success)]" /> : <Copy className="w-4 h-4" />}
          </button>
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            className="p-2 rounded-lg bg-white/5 hover:bg-white/10 text-[var(--color-text-secondary)] hover:text-white transition-colors"
            title={expanded ? 'Collapse' : 'Expand'}
          >
            {expanded ? <Minimize2 className="w-4 h-4" /> : <Maximize2 className="w-4 h-4" />}
          </button>
        </div>
      </div>

      <div className={expanded ? 'h-[calc(100%-44px)]' : 'h-[520px]'}>
        <Editor
          language={language}
          value={content || ''}
          theme={editorCfg.theme || 'vs-dark'}
          options={options}
          onChange={(val) => onChange?.(val ?? '')}
        />
      </div>
    </div>
  );
};

export default CodeEditorArtifact;
