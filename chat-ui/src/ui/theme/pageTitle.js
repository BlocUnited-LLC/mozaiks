/** Finite app-owned page-title role; font names resolve through existing slots. */
export const PAGE_TITLE_TOKENS = Object.freeze({
  font: { body: 'var(--font-body)', heading: 'var(--font-heading)' },
  weight: { regular: '400', medium: '500', semibold: '600', bold: '700' },
  size: { sm: '1.5rem', md: '1.875rem', lg: '2.25rem', xl: '3rem' },
  lineHeight: { tight: '1.15', normal: '1.3', relaxed: '1.5' },
  letterSpacing: { tight: '-0.025em', normal: 'normal', wide: '0.05em' },
  textTransform: { none: 'none', uppercase: 'uppercase', lowercase: 'lowercase', capitalize: 'capitalize' },
});

export const PAGE_TITLE_DEFAULTS = Object.freeze({
  font: 'heading', weight: 'semibold', size: 'lg',
  lineHeight: 'tight', letterSpacing: 'normal', textTransform: 'none',
});

export class PageTitleStyleError extends TypeError {}

export function pageTitleStyleErrors(title) {
  if (title === undefined) return [];
  if (!title || typeof title !== 'object' || Array.isArray(title)) {
    return ['ui.page.title must be an object.'];
  }
  return Object.entries(title).flatMap(([field, value]) => {
    const tokens = PAGE_TITLE_TOKENS[field];
    if (!Object.hasOwn(PAGE_TITLE_TOKENS, field)) return [`ui.page.title.${field} is not supported.`];
    return typeof value === 'string' && Object.hasOwn(tokens, value)
      ? [] : [`ui.page.title.${field} must use one of ${Object.keys(tokens).join(', ')}.`];
  });
}

export function resolvePageTitleStyle(title) {
  const errors = pageTitleStyleErrors(title);
  if (errors.length) throw new PageTitleStyleError(errors.join(' '));
  return { ...PAGE_TITLE_DEFAULTS, ...title };
}
