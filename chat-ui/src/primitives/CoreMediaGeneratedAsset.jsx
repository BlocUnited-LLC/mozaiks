import React from 'react';
import { FiDownload, FiExternalLink, FiFile, FiImage } from 'react-icons/fi';
import ArtifactActionsBar from '../components/actions/ArtifactActionsBar';
import { getArtifactArray, getArtifactValue, normalizeActions } from './utils';

const formatBytes = (value) => {
  const bytes = Number(value || 0);
  if (!Number.isFinite(bytes) || bytes <= 0) return '';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
};

const isImageAsset = (asset) => String(asset?.media_type || '').startsWith('image/');

const AssetPreview = ({ asset }) => {
  const previewUrl = asset?.preview_url;
  if (isImageAsset(asset) && previewUrl) {
    return (
      <img
        src={previewUrl}
        alt={asset?.display_name || asset?.filename || 'Generated media'}
        className="h-full w-full object-contain"
      />
    );
  }
  const Icon = isImageAsset(asset) ? FiImage : FiFile;
  return (
    <div className="flex h-full w-full items-center justify-center text-[var(--core-primitive-muted,var(--color-text-muted))]">
      <Icon className="h-10 w-10" aria-hidden="true" />
    </div>
  );
};

const MediaLink = ({ href, label, icon: Icon, download = false }) => {
  if (!href) return null;
  return (
    <a
      href={href}
      target={download ? undefined : '_blank'}
      rel={download ? undefined : 'noreferrer'}
      download={download || undefined}
      title={label}
      className="inline-flex h-9 items-center gap-2 rounded-lg border border-[var(--core-primitive-border,var(--color-border-subtle))] bg-[var(--core-primitive-surface-alt,var(--color-surface-alt,var(--color-surface)))] px-3 text-xs font-semibold text-[var(--core-primitive-text,var(--color-text-primary))] transition hover:border-[rgba(var(--color-primary-rgb),0.5)]"
    >
      <Icon className="h-4 w-4" aria-hidden="true" />
      <span>{label}</span>
    </a>
  );
};

const CoreMediaGeneratedAsset = ({ payload, onAction, actionStatusMap, className = '' }) => {
  const title = getArtifactValue(payload, 'title') || 'Generated Media';
  const subtitle = getArtifactValue(payload, 'subtitle');
  const assets = getArtifactArray(payload, 'assets');

  return (
    <div className={`space-y-4 ${className}`}>
      <div>
        <h3 className="text-base font-semibold text-[var(--core-primitive-text,var(--color-text-primary))]">
          {title}
        </h3>
        {subtitle ? (
          <p className="mt-1 text-xs text-[var(--core-primitive-muted,var(--color-text-muted))]">
            {subtitle}
          </p>
        ) : null}
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        {assets.map((asset, index) => {
          const actions = normalizeActions(asset?.actions || []);
          const sizeLabel = formatBytes(asset?.size_bytes);
          const mediaType = asset?.media_type || 'media';
          const contextData = { artifactPayload: payload, asset, ...asset };
          return (
            <section
              key={asset?.asset_id || index}
              className="overflow-hidden rounded-lg border border-[var(--core-primitive-border,var(--color-border-subtle))] bg-[var(--core-primitive-surface,var(--color-surface))]"
            >
              <div className="flex aspect-[4/3] max-h-[420px] min-h-[220px] items-center justify-center bg-[var(--core-primitive-surface-alt,var(--color-surface-alt,var(--color-surface)))]">
                <AssetPreview asset={asset} />
              </div>
              <div className="space-y-3 p-4">
                <div className="min-w-0">
                  <div className="truncate text-sm font-semibold text-[var(--core-primitive-text,var(--color-text-primary))]">
                    {asset?.display_name || asset?.filename || asset?.asset_id || 'Generated media'}
                  </div>
                  <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-[var(--core-primitive-muted,var(--color-text-muted))]">
                    <span>{mediaType}</span>
                    {sizeLabel ? <span>{sizeLabel}</span> : null}
                    {asset?.provider ? <span>{asset.provider}</span> : null}
                  </div>
                </div>

                <div className="flex flex-wrap items-center gap-2">
                  <MediaLink href={asset?.preview_url} label="Open" icon={FiExternalLink} />
                  <MediaLink href={asset?.download_url || asset?.preview_url} label="Download" icon={FiDownload} download />
                </div>

                <ArtifactActionsBar
                  actions={actions}
                  artifactPayload={payload}
                  contextData={contextData}
                  onAction={onAction}
                  actionStatusMap={actionStatusMap}
                  dense
                  size="sm"
                />
              </div>
            </section>
          );
        })}
      </div>

      {assets.length === 0 ? (
        <div className="rounded-lg border border-[var(--core-primitive-border,var(--color-border-subtle))] bg-[var(--core-primitive-surface,var(--color-surface))] p-4 text-xs text-[var(--core-primitive-muted,var(--color-text-muted))]">
          No media assets available.
        </div>
      ) : null}
    </div>
  );
};

export default CoreMediaGeneratedAsset;
