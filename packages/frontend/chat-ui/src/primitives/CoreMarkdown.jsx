import React from 'react';
import { marked } from 'marked';
import DOMPurify from 'dompurify';
import ArtifactActionsBar from '../components/actions/ArtifactActionsBar';
import { getArtifactValue, normalizeActions } from './utils';

const renderMarkdown = (value) => {
  try {
    const html = marked.parse(String(value || ''), { breaks: true });
    return { __html: DOMPurify.sanitize(html) };
  } catch {
    return { __html: DOMPurify.sanitize(String(value || '')) };
  }
};

const CoreMarkdown = ({ payload, onAction, actionStatusMap, className = '' }) => {
  const title = getArtifactValue(payload, 'title');
  const body =
    getArtifactValue(payload, 'body') ??
    getArtifactValue(payload, 'markdown') ??
    getArtifactValue(payload, 'content') ??
    '';
  const actions = normalizeActions(getArtifactValue(payload, 'actions', []));

  return (
    <div className={`rounded-[var(--core-primitive-radius,16px)] border border-[var(--core-primitive-border,var(--color-border-subtle))] bg-[var(--core-primitive-surface,var(--color-surface))] p-4 ${className}`}>
      {title && (
        <h3 className="text-sm font-semibold text-[var(--core-primitive-text,var(--color-text-primary))] mb-2">
          {title}
        </h3>
      )}
      <div
        className="text-sm leading-relaxed text-[var(--core-primitive-text,var(--color-text-primary))]"
        dangerouslySetInnerHTML={renderMarkdown(body)}
      />
      <ArtifactActionsBar
        actions={actions}
        artifactPayload={payload}
        onAction={onAction}
        actionStatusMap={actionStatusMap}
      />
    </div>
  );
};

export default CoreMarkdown;
