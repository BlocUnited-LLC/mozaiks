// ==============================================================================
// FILE: factory_app/workflows/AppGenerator/ui/E2BPreviewArtifact.js
// DESCRIPTION: Preview iframe with basic controls
// ==============================================================================

import React, { useCallback, useMemo, useState } from 'react';
import { ExternalLink, RefreshCw } from 'lucide-react';

const E2BPreviewArtifact = ({ previewUrl, sandboxStatus, sandboxSyncing, sandboxError, config = {} }) => {
  const previewCfg = config?.artifacts?.['e2b-preview'] || {};
  const [iframeKey, setIframeKey] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const url = useMemo(() => {
    if (!previewUrl || typeof previewUrl !== 'string') return null;
    const trimmed = previewUrl.trim();
    return trimmed.length ? trimmed : null;
  }, [previewUrl]);

  const refresh = useCallback(() => {
    setLoading(true);
    setError(null);
    setIframeKey((k) => k + 1);
  }, []);

  const isRestarting = sandboxSyncing || sandboxStatus === 'starting';

  if (!url) {
    return (
      <div className="rounded-xl border border-white/10 bg-black/30 p-4">
        {isRestarting ? (
          <div className="flex items-center gap-2 text-sm text-[var(--color-text-muted)]">
            <div className="h-4 w-4 border-2 border-[var(--color-primary-light)] border-t-transparent rounded-full animate-spin flex-shrink-0" />
            Restarting preview...
          </div>
        ) : (
          <>
            <div className="text-sm text-[var(--color-text-muted)]">No preview URL available.</div>
            <div className="text-xs text-[var(--color-text-muted)] mt-1">
              Preview URLs are typically available for <span className="font-mono">e2b</span> validation runs with <span className="font-mono">start_dev_server=true</span>.
            </div>
          </>
        )}
        {sandboxError && (
          <div className="mt-2 text-xs text-red-300">{sandboxError}</div>
        )}
      </div>
    );
  }

  return (
    <div className="rounded-xl overflow-hidden border border-white/10 bg-black/30">
      <div className="flex items-center justify-between px-4 py-2 bg-black/40 border-b border-white/10">
        <div className="min-w-0">
          <div className="text-sm font-semibold text-white">Preview</div>
          <div className="text-[10px] text-[var(--color-text-muted)] font-mono truncate">{url}</div>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={refresh}
            className="p-2 rounded-lg bg-white/5 hover:bg-white/10 text-[var(--color-text-secondary)] hover:text-white transition-colors"
            title="Refresh preview"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
          </button>
          <a
            href={url}
            target="_blank"
            rel="noopener noreferrer"
            className="p-2 rounded-lg bg-white/5 hover:bg-white/10 text-[var(--color-text-secondary)] hover:text-white transition-colors"
            title="Open in new tab"
          >
            <ExternalLink className="w-4 h-4" />
          </a>
        </div>
      </div>

      <div className="relative h-[520px] bg-white">
        {isRestarting && (
          <div className="absolute inset-0 z-30 flex flex-col items-center justify-center gap-2 bg-black/70">
            <div className="h-7 w-7 border-2 border-[var(--color-primary-light)] border-t-transparent rounded-full animate-spin" />
            <div className="text-xs text-[var(--color-text-muted)]">Applying patch and restarting preview...</div>
          </div>
        )}
        {sandboxError && !isRestarting && (
          <div className="absolute top-2 left-2 right-2 z-20 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-200">
            {sandboxError}
          </div>
        )}
        {error && (
          <div className="absolute inset-0 z-20 flex flex-col items-center justify-center gap-2 bg-black/60 p-6 text-center">
            <div className="text-sm font-semibold text-[var(--color-error)]">Preview failed to load</div>
            <div className="text-xs text-[var(--color-text-muted)] break-all">{String(error)}</div>
            <div className="flex gap-2 mt-2">
              <button
                type="button"
                onClick={refresh}
                className="px-3 py-1.5 rounded-lg bg-white/10 hover:bg-white/15 text-xs text-white border border-white/10"
              >
                Retry
              </button>
              <a
                href={url}
                target="_blank"
                rel="noopener noreferrer"
                className="px-3 py-1.5 rounded-lg bg-white/10 hover:bg-white/15 text-xs text-white border border-white/10"
              >
                Open
              </a>
            </div>
          </div>
        )}
        {loading && !error && previewCfg.showLoadingIndicator !== false && (
          <div className="absolute inset-0 z-10 flex items-center justify-center bg-black/40">
            <div className="h-7 w-7 border-2 border-[var(--color-primary-light)] border-t-transparent rounded-full animate-spin" />
          </div>
        )}
        <iframe
          key={iframeKey}
          src={url}
          title="App Preview"
          className="w-full h-full border-0"
          sandbox="allow-scripts allow-same-origin allow-forms allow-popups"
          onLoad={() => {
            setError(null);
            setLoading(false);
          }}
          onError={() => {
            setLoading(false);
            setError('The preview URL could not be loaded.');
          }}
        />
      </div>
    </div>
  );
};

export default E2BPreviewArtifact;
