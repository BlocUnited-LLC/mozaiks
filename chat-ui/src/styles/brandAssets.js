import defaultBrandLogo from '../assets/mozaik.png';

const DEFAULT_BRAND_LOGO_SRC = defaultBrandLogo;
const DEFAULT_BRAND_LOGO_FALLBACK_SRC = defaultBrandLogo;

const isNonEmptyString = (value) => typeof value === 'string' && value.trim().length > 0;

export function getBrandLogoSrc(theme) {
  return isNonEmptyString(theme?.branding?.logo) ? theme.branding.logo : DEFAULT_BRAND_LOGO_SRC;
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
