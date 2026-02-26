import React from 'react';
import { marked } from 'marked';
import DOMPurify from 'dompurify';
import ArtifactActionsBar from '../components/actions/ArtifactActionsBar';
import { getArtifactArray, getArtifactValue, normalizeActions } from './utils';

const renderMarkdown = (value) => {
  try {
    const html = marked.parse(String(value || ''), { breaks: true });
    return { __html: DOMPurify.sanitize(html) };
  } catch {
    return { __html: DOMPurify.sanitize(String(value || '')) };
  }
};

const CoreCard = ({ payload, onAction, actionStatusMap, className = '' }) => {
  const title = getArtifactValue(payload, 'title');
  const subtitle = getArtifactValue(payload, 'subtitle');
  const body =
    getArtifactValue(payload, 'body') ??
    getArtifactValue(payload, 'markdown') ??
    getArtifactValue(payload, 'content') ??
    '';
  const image = getArtifactValue(payload, 'image');
  const metadata = getArtifactArray(payload, 'metadata');
  const actions = normalizeActions(getArtifactValue(payload, 'actions', []));

  return (
    <div className={`rounded-[var(--core-primitive-radius,16px)] border border-[var(--core-primitive-border,var(--color-border-subtle))] bg-[var(--core-primitive-surface,var(--color-surface))] p-4 space-y-3 ${className}`}>
      {image && typeof image === 'string' && (
        <img
          src={image}
          alt={title || 'Card image'}
          className="w-full h-40 object-cover rounded-[var(--core-primitive-radius,16px)] border border-[var(--core-primitive-border,var(--color-border-subtle))]"
        />
      )}
      {title && (
        <div>
          <h3 className="text-base font-semibold text-[var(--core-primitive-text,var(--color-text-primary))]">{title}</h3>
          {subtitle && (
            <p className="text-xs text-[var(--core-primitive-muted,var(--color-text-muted))]">{subtitle}</p>
          )}
        </div>
      )}
      {body !== null && body !== undefined && (
        typeof body === 'string' ? (
          <div
            className="text-sm leading-relaxed text-[var(--core-primitive-text,var(--color-text-primary))]"
            dangerouslySetInnerHTML={renderMarkdown(body)}
          />
        ) : (
          <pre className="text-xs text-[var(--core-primitive-text,var(--color-text-primary))] whitespace-pre-wrap">
            {JSON.stringify(body, null, 2)}
          </pre>
        )
      )}
      {metadata.length > 0 && (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
          {metadata.map((item, idx) => (
            <div
              key={`${item?.label || 'meta'}-${idx}`}
              className="rounded-lg border border-[var(--core-primitive-border,var(--color-border-subtle))] bg-[var(--core-primitive-surface-alt,var(--color-surface-alt,var(--color-surface)))] px-3 py-2"
            >
              <div className="text-[10px] uppercase tracking-wide text-[var(--core-primitive-muted,var(--color-text-muted))]">
                {item?.label || 'Info'}
              </div>
              <div className="text-sm text-[var(--core-primitive-text,var(--color-text-primary))]">
                {item?.value ?? ''}
              </div>
            </div>
          ))}
        </div>
      )}
      <ArtifactActionsBar
        actions={actions}
        artifactPayload={payload}
        onAction={onAction}
        actionStatusMap={actionStatusMap}
      />
    </div>
  );
};

export default CoreCard;
