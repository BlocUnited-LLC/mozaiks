// ==============================================================================
// FILE: ChatUI/src/workflows/AgentGenerator/components/FileDownloadCenter.js
// DESCRIPTION: AgentGenerator workflow component for file downloads with AG2 integration
// ==============================================================================

import React, { useMemo, useState } from 'react';
import { createToolsLogger } from '../../../../../chat-ui/src/platform/workflowSurfaceRuntime.js';
import {
  colors as designColors,
  components as designComponents,
  typography as designTypography,
} from '../../../../../chat-ui/src/platform/workflowSurfaceStyles.js';

/**
 * FileDownloadCenter - Production AG2 component for file downloads
 * 
 * Handles file downloads with rich agent context feedback within the AG2 workflow system.
 * Fully integrated with canonical ui.tool.* and artifact.* event handling.
 */
const FileDownloadCenter = ({
  payload = {},
  onResponse,
  toolName,
  toolCallId,
  sourceWorkflowName,
  generatedWorkflowName,
  componentId = 'FileDownloadCenter'
}) => {
  const files = Array.isArray(payload.files) ? payload.files : [];
  const totalFiles = files.length;
  const agentMessageId = payload.agent_message_id;
  const resolvedWorkflowName = generatedWorkflowName || sourceWorkflowName || payload.generatedWorkflowName || payload.sourceWorkflowName || null;
  const defaultCommitMessage = payload.commit_message || payload.commitMessage || 'Initial code generation from Mozaiks AI';
  const config = {
    files,
    title: payload.title || 'Workflow Bundle Ready',
    description: payload.agent_message || payload.description || 'Would you like to download the generated workflow bundle now?'
  };
  const tlog = createToolsLogger({ tool: toolName || componentId, eventId: toolCallId, workflowName: resolvedWorkflowName, agentMessageId });
  const [isDownloading, setIsDownloading] = useState(false);
  const [hasDownloaded, setHasDownloaded] = useState(false);
  const [downloadErrors, setDownloadErrors] = useState([]);
  const [downloadProgress, setDownloadProgress] = useState(0);
  const [showCompletion, setShowCompletion] = useState(false);
  const [repoName, setRepoName] = useState(payload.repo_name || payload.repoName || '');
  const [commitMessage, setCommitMessage] = useState(defaultCommitMessage);

  const backendOrigin = useMemo(() => {
    if (typeof window === 'undefined') return '';
    // Allow runtime override from window or build-time env when available
    const envOverride = process.env.REACT_APP_RUNTIME_API_BASE;
    if (envOverride) return envOverride.replace(/\/$/, '');
    const windowOverride = window.__MOZAIKS_RUNTIME_API_BASE__ || window.__MOZAIKS_BACKEND_ORIGIN__;
    if (windowOverride) return String(windowOverride).replace(/\/$/, '');

    const { protocol, hostname, port } = window.location;
    if (port === '3000') {
      return `${protocol}//${hostname}:8000`;
    }
    return `${protocol}//${hostname}${port ? `:${port}` : ''}`;
  }, []);

  const buildDownloadUrl = (filePath) => {
    if (!backendOrigin) {
      return `/api/download/workflow-file?file_path=${encodeURIComponent(filePath)}`;
    }
    const url = new URL('/api/download/workflow-file', backendOrigin);
    url.searchParams.set('file_path', filePath);
    return url.href;
  };

  const containerClasses = [designComponents.panel.inline, 'file-download-center inline-file-download w-full max-w-lg mx-auto'].join(' ');
  const downloadButtonClasses = designComponents.button.primary;
  const closeButtonClasses = designComponents.button.secondary;

  const handleExportToGitHub = async () => {
    const response = {
      status: 'success',
      action: 'export_to_github',
      data: {
        tool_name: toolName,
        tool_call_id: toolCallId,
        workflowName: resolvedWorkflowName,
        repo_name: repoName || null,
        commit_message: commitMessage || null,
        files,
        fileCount: totalFiles,
      },
      agentContext: {
        export_to_github: true,
      },
    };

    try {
      tlog.event('deploy', 'export_requested', { repoName: repoName || null });
      if (onResponse) await onResponse(response);
    } catch (e) {
      tlog.error('deploy export response failed', { error: e?.message });
    }
  };

  const handleDownloadAll = async () => {
    if (isDownloading || totalFiles === 0) return;

    setIsDownloading(true);
    setHasDownloaded(false);
    setDownloadErrors([]);
    setDownloadProgress(0);
    tlog.event('download', 'start', { fileCount: totalFiles });

    const errors = [];

    await Promise.all(
      files.map(async (file) => {
        try {
          const downloadUrl = buildDownloadUrl(file.path);
          const response = await fetch(downloadUrl, {
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
          link.download = file.name;
          link.style.display = 'none';
          document.body.appendChild(link);
          link.click();
          document.body.removeChild(link);
          URL.revokeObjectURL(blobUrl);
        } catch (err) {
          errors.push({ name: file.name, error: err?.message || 'unknown' });
          console.error(`Failed to download ${file.name}:`, err);
          tlog.error('individual download failed', { fileName: file.name, error: err?.message });
        } finally {
          setDownloadProgress((prev) => {
            const next = prev + 1;
            return next > totalFiles ? totalFiles : next;
          });
        }
      })
    );

    if (errors.length) {
      setDownloadErrors(errors);
    }

    setIsDownloading(false);
    setHasDownloaded(errors.length === 0);

    // Show completion UI instead of calling onResponse immediately
    if (errors.length === 0) {
      setShowCompletion(true);
      tlog.event('download', 'complete_ui_shown', { fileCount: totalFiles });
      return;
    }

    const response = {
      status: errors.length ? 'partial_failure' : 'success',
      action: 'download_complete',
      data: {
        tool_name: toolName,
        tool_call_id: toolCallId,
        workflowName: resolvedWorkflowName,
        files,
        fileCount: totalFiles,
        errors,
      },
      agentContext: { downloaded: errors.length === 0, errors },
    };
    try {
      if (onResponse) await onResponse(response);
      tlog.event('download', 'done', { ok: errors.length === 0, fileCount: totalFiles, errors });
    } catch (e) {
      tlog.error('download failed', { error: e?.message });
    }
  };

  const buttonLabel = (() => {
    if (isDownloading) {
      const completed = Math.min(downloadProgress, totalFiles);
      if (!totalFiles) return 'Preparing downloads…';
      const remaining = totalFiles - completed;
      return remaining > 0
        ? `Preparing downloads (${completed}/${totalFiles})…`
        : 'Finalizing downloads…';
    }
    if (hasDownloaded && !downloadErrors.length) {
      return 'Download Again';
    }
    return 'Download All Files';
  })();

  const handleContinue = async () => {
    tlog.event('completion', 'continue_clicked', { fileCount: totalFiles });

    // Send analytics placeholder
    try {
      await fetch(`${backendOrigin}/api/analytics/workflow-generated`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        credentials: 'include',
        body: JSON.stringify({
          workflow_name: resolvedWorkflowName,
          file_count: totalFiles,
          event_id: toolCallId,
          tool_name: toolName,
          timestamp: new Date().toISOString(),
        }),
      });
      tlog.event('analytics', 'sent', { workflow: resolvedWorkflowName });
    } catch (err) {
      // Placeholder API - don't block on error
      tlog.warn('analytics call failed', { error: err?.message });
    }

    // Signal download acceptance and trigger workflow termination
    const response = {
      status: 'success',
      action: 'download_complete',
      download_accepted: true,
      data: {
        tool_name: toolName,
        tool_call_id: toolCallId,
        workflowName: resolvedWorkflowName,
        files,
        fileCount: totalFiles,
        errors: [],
      },
      agentContext: { downloaded: true, accepted: true, errors: [] },
    };
    try {
      if (onResponse) await onResponse(response);
      tlog.event('completion', 'accepted', { fileCount: totalFiles });
      
      // Redirect to mozaiks.ai after successful completion
      setTimeout(() => {
        window.location.href = 'https://www.mozaiks.ai/';
      }, 500); // Short delay to ensure analytics/response completes
      
    } catch (e) {
      tlog.error('completion response failed', { error: e?.message });
    }
  };

  return (
  <div
  className={containerClasses}
    data-agent-message-id={agentMessageId || undefined}
    data-display-mode="inline"
    aria-label="Workflow file downloads"
  >
      {showCompletion ? (
        // Completion state UI
        <>
          <div className="download-header mb-4 text-center">
            <div className="text-5xl mb-3">🎉</div>
            <h3 className={`${designTypography.label.lg} ${designColors.brand.primaryLight.text} mb-2`}>
              Congratulations!
            </h3>
            <p className={`${designTypography.body.md} ${designColors.text.secondary} leading-relaxed`}>
              Your workflow is ready and all files have been downloaded successfully.
            </p>
          </div>
          
          <div className="mb-3 p-4 bg-gray-800 rounded text-center">
            <div className={`${designTypography.label.md} ${designColors.text.primary} mb-1`}>
              {files.length} {files.length === 1 ? 'File' : 'Files'} Downloaded
            </div>
            <div className={`${designTypography.body.sm} ${designColors.text.muted}`}>
              {resolvedWorkflowName || 'Your workflow'} is ready to use
            </div>
          </div>

          <button 
            onClick={handleContinue}
            className={[downloadButtonClasses, 'w-full flex items-center justify-center'].filter(Boolean).join(' ')}
          >
            Continue
          </button>
        </>
      ) : (
        // Normal download UI
        <>
          <div className="download-header mb-2">
            <h3 className={`${designTypography.label.md} ${designColors.brand.primaryLight.text}`}>
              {config.title}
            </h3>
          </div>
          
          {config.description && (
            <p className={`${designTypography.body.md} ${designColors.text.secondary} mb-3 leading-relaxed`}>
              {config.description}
            </p>
          )}

          {/* File count and download button */}
          {files.length > 0 && (
            <div className="mb-3">
              <div className="mb-3 p-3 bg-gray-800 rounded">
                <div className="flex items-center justify-between">
                  <div>
                    <div className={`${designTypography.label.md} ${designColors.text.primary}`}>
                      {files.length} {files.length === 1 ? 'File' : 'Files'} Ready
                    </div>
                    <div className={`${designTypography.body.sm} ${designColors.text.muted} mt-1`}>
                      Workflow bundle package
                    </div>
                  </div>
                  <div className={`${designTypography.label.lg} ${designColors.brand.primaryLight.text}`}>
                    📦
                  </div>
                </div>
              </div>
              <div className="mb-3 p-3 bg-gray-800 rounded">
                <div className={`${designTypography.label.sm} ${designColors.text.primary} mb-2`}>Export to GitHub (optional)</div>
                <div className="space-y-2">
                  <div>
                    <label className={`${designTypography.body.sm} ${designColors.text.muted}`}>Repo name</label>
                    <input
                      value={repoName}
                      onChange={(e) => setRepoName(e.target.value)}
                      placeholder="Leave blank to use app name"
                      className="mt-1 w-full rounded bg-black/30 border border-white/10 px-3 py-2 text-sm text-white focus:outline-none focus:ring-2 focus:ring-white/20"
                      type="text"
                    />
                  </div>
                  <div>
                    <label className={`${designTypography.body.sm} ${designColors.text.muted}`}>Commit message</label>
                    <input
                      value={commitMessage}
                      onChange={(e) => setCommitMessage(e.target.value)}
                      placeholder={defaultCommitMessage}
                      className="mt-1 w-full rounded bg-black/30 border border-white/10 px-3 py-2 text-sm text-white focus:outline-none focus:ring-2 focus:ring-white/20"
                      type="text"
                    />
                  </div>
                </div>
              </div>
              <div className="flex gap-3">
                <button 
                  onClick={handleDownloadAll} 
                  disabled={isDownloading || totalFiles === 0} 
                  className={[downloadButtonClasses, 'flex flex-1 items-center justify-center'].filter(Boolean).join(' ')}
                >
                  {buttonLabel}
                </button>
                <button
                  onClick={handleExportToGitHub}
                  disabled={totalFiles === 0}
                  className={[closeButtonClasses, 'flex flex-1 items-center justify-center'].filter(Boolean).join(' ')}
                >
                  Export to GitHub
                </button>
                <button 
                  onClick={() => onResponse && onResponse({ status: 'complete', action: 'close' })} 
                  className={[closeButtonClasses, 'flex flex-1 items-center justify-center'].filter(Boolean).join(' ')}
                >
                  Close
                </button>
              </div>
              {isDownloading && totalFiles > 0 && (
                <div className="mt-3 text-sm text-gray-300">
                  Preparing downloads ({Math.min(downloadProgress, totalFiles)}/{totalFiles})…
                </div>
              )}
              {!isDownloading && hasDownloaded && !downloadErrors.length && (
                <div className="mt-3 text-sm text-emerald-300">
                  All files were sent to your browser. Large bundles can take a few moments to appear in your downloads tray.
                </div>
              )}
              {downloadErrors.length > 0 && (
                <div className="mt-3 text-sm text-red-400">
                  Some files failed to download: {downloadErrors.map((entry) => entry.name).join(', ')}. Please check the runtime logs or try again.
                </div>
              )}
            </div>
          )}
          
          {files.length === 0 && (
            <div className={`${designTypography.body.sm} ${designColors.text.muted}`}>
              No files available for download.
            </div>
          )}
        </>
      )}
    </div>
  );
};

// Add display name for better debugging
FileDownloadCenter.displayName = 'FileDownloadCenter';

// Component metadata for the dynamic UI system (MASTER_UI_TOOL_AGENT_PROMPT requirement)
export default FileDownloadCenter;
