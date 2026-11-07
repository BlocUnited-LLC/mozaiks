/**
 * Image primitive — renders an app asset declared in config/asset_manifest.json.
 *
 * Schema properties:
 *   asset_id {string}  — asset_manifest entry to show (REQUIRED)
 *   alt      {string}  — alt text; falls back to an empty decorative alt
 *   caption  {string}  — optional caption under the image
 *   variant  {string}  — "banner" (full-width, wide crop) | "inline" (natural shape)
 *
 * The host serves the asset at /api/app-assets/{asset_id}. A planned asset the
 * media step has not produced yet answers 404, and the primitive shows a
 * neutral placeholder instead of a broken image.
 */

import { useEffect, useState } from 'react';
import platform from '../../platform/index.js';
import { cn } from '../lib/cn.js';

export function Image({ asset_id: assetId, alt, caption, variant = 'inline', className }) {
  const [failed, setFailed] = useState(false);
  const configuredBaseUrl = platform.getConfigOverrides()?.api?.baseUrl;
  const baseUrl = configuredBaseUrl === '' ? '' : platform.resolveHttpUrl({ explicitUrl: configuredBaseUrl });
  const src = `${baseUrl || ''}/api/app-assets/${encodeURIComponent(assetId || '')}`;
  useEffect(() => setFailed(false), [src]);
  const banner = variant === 'banner';
  const frame = cn(
    'w-full overflow-hidden rounded-xl border border-border bg-muted',
    banner ? 'aspect-[21/9] max-h-[420px]' : null,
  );

  return (
    <figure className={cn('w-full', className)} data-asset-id={assetId}>
      {failed || !assetId ? (
        <div
          className={cn(frame, 'flex min-h-[160px] items-center justify-center text-sm text-muted-foreground')}
          role="img"
          aria-label={alt || 'Image coming soon'}
        >
          Image coming soon
        </div>
      ) : (
        <div className={frame}>
          <img
            src={src}
            alt={alt || ''}
            loading="lazy"
            onError={() => setFailed(true)}
            className={cn('block w-full', banner ? 'h-full object-cover' : 'h-auto')}
          />
        </div>
      )}
      {caption && <figcaption className="mt-2 text-sm text-muted-foreground">{caption}</figcaption>}
    </figure>
  );
}
