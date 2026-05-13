import { useMemo, useState } from 'react';
import { Alert, Button, StatusPill, SurfaceCard } from '../../ui/primitives/index.js';
import { normalizePrimitiveActions, sendPrimitiveResponse } from './workflowPrimitiveUtils.js';

const fallbackActions = [
  { id: 'download_complete', label: 'Download Bundle', variant: 'primary', approved: true },
  { id: 'close', label: 'Close', variant: 'secondary' },
];

function formatFileSize(value) {
  const size = Number(value);
  if (!Number.isFinite(size) || size <= 0) {
    return null;
  }
  if (size >= 1024 * 1024) {
    return `${(size / (1024 * 1024)).toFixed(1)} MB`;
  }
  if (size >= 1024) {
    return `${(size / 1024).toFixed(1)} KB`;
  }
  return `${size} B`;
}

function resolveBackendOrigin() {
  if (typeof window === 'undefined') {
    return '';
  }
  const override = window.__MOZAIKS_RUNTIME_API_BASE__ || window.__MOZAIKS_BACKEND_ORIGIN__;
  if (typeof override === 'string' && override.trim()) {
    return override.replace(/\/$/, '');
  }
  const { protocol, hostname, port } = window.location;
  if (port === '3000') {
    return `${protocol}//${hostname}:8000`;
  }
  return `${protocol}//${hostname}${port ? `:${port}` : ''}`;
}

function buildDownloadUrl(backendOrigin, filePath) {
  if (!backendOrigin) {
    return `/api/download/workflow-file?file_path=${encodeURIComponent(filePath)}`;
  }
  const url = new URL('/api/download/workflow-file', backendOrigin);
  url.searchParams.set('file_path', filePath);
  return url.href;
}

export default function DownloadCenter({ payload = {}, onResponse, onCancel }) {
  const files = Array.isArray(payload.files) ? payload.files : [];
  const actions = normalizePrimitiveActions(payload, onResponse ? fallbackActions : []);
  const exportAction = actions.find((action) => action.id === 'export_to_github') || null;
  const primaryActions = actions.filter((action) => action.id !== 'export_to_github');
  const [repoName, setRepoName] = useState(String(payload.repo_name || payload.repoName || ''));
  const [commitMessage, setCommitMessage] = useState(
    String(payload.commit_message || payload.commitMessage || 'Initial code generation from Mozaiks AI'),
  );
  const [isDownloading, setIsDownloading] = useState(false);
  const [downloadErrors, setDownloadErrors] = useState([]);
  const backendOrigin = useMemo(() => resolveBackendOrigin(), []);

  async function triggerBrowserDownloads() {
    const downloadableFiles = files.filter((file) => file && typeof file.path === 'string' && file.path.trim());
    if (downloadableFiles.length === 0 || typeof window === 'undefined') {
      return [];
    }

    const errors = [];
    setIsDownloading(true);
    try {
      for (const file of downloadableFiles) {
        try {
          const response = await fetch(buildDownloadUrl(backendOrigin, file.path), {
            method: 'GET',
            credentials: 'include',
          });
          if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
          }
          const blob = await response.blob();
          const blobUrl = URL.createObjectURL(blob);
          const link = document.createElement('a');
          link.href = blobUrl;
          link.download = file.name || 'download';
          link.style.display = 'none';
          document.body.appendChild(link);
          link.click();
          document.body.removeChild(link);
          URL.revokeObjectURL(blobUrl);
        } catch (error) {
          errors.push({
            name: file?.name || 'download',
            error: error instanceof Error ? error.message : String(error),
          });
        }
      }
    } finally {
      setIsDownloading(false);
    }
    return errors;
  }

  async function handleAction(action) {
    if (!onResponse) {
      return;
    }

    if (action.id === 'download_complete') {
      const errors = await triggerBrowserDownloads();
      setDownloadErrors(errors);
      await sendPrimitiveResponse(onResponse, action, {
        files,
        download_accepted: errors.length === 0,
        errors,
      });
      return;
    }

    if (action.id === 'export_to_github') {
      await sendPrimitiveResponse(onResponse, action, {
        files,
        repo_name: repoName.trim() || null,
        commit_message: commitMessage.trim() || 'Initial code generation from Mozaiks AI',
      });
      return;
    }

    await sendPrimitiveResponse(onResponse, action, { files });
  }

  return (
    <SurfaceCard
      title={payload.title || 'Download center'}
      subtitle={payload.summary || 'Review the generated files and finish the workflow when ready.'}
      headerAction={<StatusPill label={`${files.length} file${files.length === 1 ? '' : 's'}`} tone="default" />}
    >
      <div className="space-y-4">
        {downloadErrors.length > 0 ? (
          <Alert
            message={`Some files failed to download: ${downloadErrors.map((entry) => entry.name).join(', ')}`}
            variant="warning"
          />
        ) : null}

        <div className="space-y-2">
          {files.map((file, index) => (
            <div key={`${file?.name || index}`} className="rounded-md border border-border/60 bg-muted/30 px-3 py-3">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <p className="text-sm font-medium text-foreground">{file?.name || `File ${index + 1}`}</p>
                  {file?.description ? (
                    <p className="text-sm text-muted-foreground">{String(file.description)}</p>
                  ) : null}
                </div>
                <div className="text-right text-xs text-muted-foreground">
                  {file?.status ? <p>{String(file.status)}</p> : null}
                  {formatFileSize(file?.size) ? <p>{formatFileSize(file?.size)}</p> : null}
                  {file?.path ? (
                    <p className="truncate text-[11px]" title={String(file.path)}>
                      {String(file.path)}
                    </p>
                  ) : null}
                </div>
              </div>
            </div>
          ))}
        </div>

        {exportAction ? (
          <div className="rounded-md border border-border/60 bg-background px-3 py-3 space-y-3">
            <p className="text-sm font-medium text-foreground">GitHub Export</p>
            <div className="grid gap-3 sm:grid-cols-2">
              <label className="space-y-1 text-sm text-foreground">
                <span className="text-xs uppercase tracking-wide text-muted-foreground">Repository Name</span>
                <input
                  value={repoName}
                  onChange={(event) => setRepoName(event.target.value)}
                  className="w-full rounded-md border border-border/70 bg-background px-3 py-2 text-sm text-foreground"
                  placeholder="my-generated-workflow"
                />
              </label>
              <label className="space-y-1 text-sm text-foreground">
                <span className="text-xs uppercase tracking-wide text-muted-foreground">Commit Message</span>
                <input
                  value={commitMessage}
                  onChange={(event) => setCommitMessage(event.target.value)}
                  className="w-full rounded-md border border-border/70 bg-background px-3 py-2 text-sm text-foreground"
                  placeholder="Initial code generation from Mozaiks AI"
                />
              </label>
            </div>
          </div>
        ) : null}

        <div className="flex flex-wrap gap-3">
          {primaryActions.map((action) => (
            <Button
              key={action.id}
              label={isDownloading && action.id === 'download_complete' ? 'Downloading…' : action.label}
              variant={action.variant}
              disabled={isDownloading && action.id === 'download_complete'}
              onClick={() => handleAction(action)}
            />
          ))}
          {exportAction ? (
            <Button
              label={exportAction.label}
              variant={exportAction.variant}
              onClick={() => handleAction(exportAction)}
            />
          ) : null}
          {onCancel ? (
            <Button label="Cancel" variant="ghost" onClick={() => onCancel({ status: 'cancelled', action: 'cancel' })} />
          ) : null}
        </div>
      </div>
    </SurfaceCard>
  );
}
