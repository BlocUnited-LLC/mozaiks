// ==============================================================================
// FILE: factory_app/workflows/AppGenerator/ui/PreviewPane.js
// DESCRIPTION: Preview iframe with basic controls
// ==============================================================================

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { ExternalLink, Play, RefreshCw } from 'lucide-react';

const PreviewPane = ({
  previewUrl,
  sandboxStatus,
  sandboxSyncing,
  sandboxError,
  artifactVersionId,
  config = {},
  onStartPreview = null,
  canStartPreview = false,
}) => {
  const previewCfg = config?.artifacts?.['e2b-preview'] || {};
  const [iframeKey, setIframeKey] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  const url = useMemo(() => {
    if (!previewUrl || typeof previewUrl !== 'string') return null;
    const trimmed = previewUrl.trim();
    return trimmed.length ? trimmed : null;
  }, [previewUrl]);

  useEffect(() => {
    setLoading(true);
    setError(null);
  }, [url]);

  const refresh = useCallback(() => {
    setLoading(true);
    setError(null);
    setIframeKey((k) => k + 1);
  }, []);

  const isRestarting = sandboxSyncing || sandboxStatus === 'starting';
  const previewIdentity = (
    <div className="min-w-0">
      <div className="flex flex-wrap items-center gap-2">
        <div className="text-sm font-semibold text-white">Draft app preview</div>
        {artifactVersionId && (
          <span className="text-[10px] text-[var(--color-text-muted)] font-mono" title={artifactVersionId}>
            Version {artifactVersionId.slice(0, 12)}
          </span>
        )}
      </div>
      <div className="mt-1 text-xs text-[var(--color-text-muted)]">Temporary preview · Changes here do not publish your app.</div>
    </div>
  );

  if (!url) {
    return (
      <div className="rounded-lg border border-white/10 bg-black/30 p-4" role="status">
        {previewIdentity}
        {isRestarting ? (
          <div className="mt-3 flex items-center gap-2 text-sm text-[var(--color-text-muted)]">
            <div className="h-4 w-4 border-2 border-[var(--color-primary-light)] border-t-transparent rounded-full animate-spin flex-shrink-0" />
            Starting draft preview...
          </div>
        ) : (
          <>
            <div className="mt-3 text-sm text-[var(--color-text-muted)]">Preview stopped</div>
            {onStartPreview && canStartPreview ? (
              <>
                <button
                  type="button"
                  onClick={onStartPreview}
                  className="mt-3 inline-flex items-center gap-2 px-3 py-1.5 rounded-lg bg-white/10 hover:bg-white/15 text-xs font-semibold text-white border border-white/10 transition-colors"
                >
                  <Play className="w-3.5 h-3.5" /> Start draft preview
                </button>
              </>
            ) : (
              <div className="text-xs text-[var(--color-text-muted)] mt-1">
                Your preview will be available once this build is saved.
              </div>
            )}
          </>
        )}
        {sandboxError && (
          <div className="mt-2 text-xs text-red-300">{sandboxError}</div>
        )}
      </div>
    );
  }

  return (
    <div className="rounded-lg overflow-hidden border border-white/10 bg-black/30">
      <div className="flex flex-wrap items-center justify-between gap-3 px-4 py-3 bg-black/40 border-b border-white/10">
        {previewIdentity}
        <div className="flex flex-wrap items-center gap-2">
          {onStartPreview && canStartPreview && (
            <button
              type="button"
              onClick={onStartPreview}
              disabled={isRestarting}
              className="p-2 rounded-lg bg-white/5 hover:bg-white/10 disabled:opacity-50 text-[var(--color-text-secondary)] hover:text-white"
              title="Restart draft preview"
              aria-label="Restart draft preview"
            >
              <Play className="w-4 h-4" />
            </button>
          )}
          <button
            type="button"
            onClick={refresh}
            className="p-2 rounded-lg bg-white/5 hover:bg-white/10 text-[var(--color-text-secondary)] hover:text-white transition-colors"
            title="Refresh preview"
            aria-label="Refresh preview"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
          </button>
          <a
            href={url}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-2 px-3 py-2 rounded-lg bg-white/5 hover:bg-white/10 text-xs text-[var(--color-text-secondary)] hover:text-white transition-colors"
            title="Open draft preview in a new tab; this workspace stays open"
          >
            <ExternalLink className="w-4 h-4" /> Open draft preview
          </a>
        </div>
      </div>

      <div className="relative h-[520px] bg-white">
        {isRestarting && (
          <div className="absolute inset-0 z-30 flex flex-col items-center justify-center gap-2 bg-black/70">
            <div className="h-7 w-7 border-2 border-[var(--color-primary-light)] border-t-transparent rounded-full animate-spin" />
            <div className="text-xs text-[var(--color-text-muted)]">Restarting preview...</div>
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
                Open draft preview
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
          title="Draft app preview"
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

export default PreviewPane;
