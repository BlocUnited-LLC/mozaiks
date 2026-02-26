import React from 'react';
import { useBranding } from '../providers/BrandingProvider';

const LoadingSpinner = () => {
  // Read logo from brand.json → assets.logo; falls back to mozaik_logo.svg
  const branding = useBranding?.();
  const _logo    = branding?.assets?.logo;
  if (!_logo) {
    console.warn('⚠️ [THEME] branding.assets.logo not set — using fallback: mozaik_logo.svg');
  }
  const logoSrc  = _logo ? `/assets/${_logo}` : '/assets/mozaik_logo.svg';

  return (
    <div id="loader" className="flex flex-col items-center justify-center gap-4" role="status" aria-label="Initializing workflow">
      <div className="relative">
        <svg className="animate-spin h-14 w-14 text-[var(--color-primary-light)] drop-shadow" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z"></path>
        </svg>
        <img src={logoSrc} alt="Mozaiks" className="h-8 w-8 absolute inset-0 m-auto opacity-90" />
      </div>
      <p className="text-sm font-medium text-white/90 tracking-wide">Preparing workspace...</p>
    </div>
  );
};

export default LoadingSpinner;
