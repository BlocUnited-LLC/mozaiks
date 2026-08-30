import React from 'react';
import { Skeleton as BaseSkeleton } from '../base/components/skeleton.jsx';
import { Progress } from '../base/components/progress.jsx';
import { cn } from '../lib/cn.js';
import {
  applyBrandImageFallback,
  getBrandLoadingIconSrc,
} from '../../styles/brandAssets';

function ArtifactLoadingState({
  chatTheme = null,
  title = 'Rendering artifact',
  message = 'Preparing the generated surface for display.',
  className = '',
}) {
  const logoSrc = getBrandLoadingIconSrc(chatTheme);

  return (
    <div
      className={cn(
        'flex min-h-[260px] w-full items-center justify-center px-4 py-8 sm:px-6',
        className,
      )}
      role="status"
      aria-live="polite"
      aria-busy="true"
    >
      <div className="w-full max-w-xl rounded-3xl border border-[rgba(var(--color-primary-light-rgb),0.22)] bg-[rgba(6,11,25,0.74)] px-5 py-6 shadow-[0_24px_60px_rgba(2,6,23,0.48)] backdrop-blur-xl sm:px-7 sm:py-7">
        <div className="flex items-center gap-4">
          <div className="relative h-16 w-16 shrink-0">
            <div className="absolute inset-0 animate-spin rounded-[1.25rem] border-2 border-[rgba(var(--color-primary-light-rgb),0.34)] border-t-transparent" />
            <div className="absolute inset-[0.4rem] flex items-center justify-center overflow-hidden rounded-[1rem] border border-[rgba(var(--color-primary-light-rgb),0.24)] bg-[rgba(255,255,255,0.04)]">
              <img
                src={logoSrc}
                alt="Artifact loading"
                className="h-10 w-10 object-contain"
                onError={applyBrandImageFallback}
              />
            </div>
          </div>

          <div className="min-w-0 flex-1">
            <div className="text-[11px] uppercase tracking-[0.24em] text-[rgba(var(--color-primary-light-rgb),0.72)] heading-font">
              {title}
            </div>
            <div className="mt-1 text-sm leading-6 text-[rgba(226,232,240,0.84)]">
              {message}
            </div>
          </div>
        </div>

        <div className="mt-6 space-y-3">
          <BaseSkeleton className="h-4 w-3/4" />
          <BaseSkeleton className="h-4 w-full" />
          <BaseSkeleton className="h-4 w-1/2" />
          <Progress value={68} className="mt-4 h-2" />
        </div>
      </div>
    </div>
  );
}

export default ArtifactLoadingState;
