import defaultBrandLogo from '../assets/mozaik.png';

const DEFAULT_BRAND_LOGO_SRC = defaultBrandLogo;
const DEFAULT_BRAND_LOGO_FALLBACK_SRC = defaultBrandLogo;

const isNonEmptyString = (value) => typeof value === 'string' && value.trim().length > 0;

export function getBrandLogoSrc(theme) {
  if (isNonEmptyString(theme?.branding?.logo)) return theme.branding.logo;
  const name = theme?.branding?.name;
  if (isNonEmptyString(name)) {
    const initials = Array.from(name.trim()).slice(0, 2).join('').toUpperCase();
    const entities = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&apos;' };
    const escaped = initials.replace(/[&<>"']/g, character => entities[character]);
    const svg = [
      '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48">',
      '<text x="24" y="32" text-anchor="middle" font-family="sans-serif"',
      ' font-size="26" fill="#14b8a6" stroke="#071113" stroke-width="0.5" paint-order="stroke">',
      escaped,
      '</text></svg>',
    ].join('');
    return `data:image/svg+xml,${encodeURIComponent(svg)}`;
  }
  return DEFAULT_BRAND_LOGO_SRC;
}

export function getBrandLoadingIconSrc(theme) {
  if (isNonEmptyString(theme?.branding?.loadingIcon)) {
    return theme.branding.loadingIcon;
  }
  return getBrandLogoSrc(theme);
}

export function getChatBackgroundSrc(theme) {
  return isNonEmptyString(theme?.branding?.chatbackgroundImage)
    ? theme.branding.chatbackgroundImage
    : null;
}

export function applyBrandImageFallback(event, fallbackSrc = DEFAULT_BRAND_LOGO_FALLBACK_SRC) {
  const target = event?.currentTarget || event?.target;
  if (!target) {
    return;
  }

  target.onerror = null;
  if (isNonEmptyString(fallbackSrc)) {
    target.src = fallbackSrc;
  }
}

export {
  DEFAULT_BRAND_LOGO_SRC,
  DEFAULT_BRAND_LOGO_FALLBACK_SRC,
};
