// Internal title treatment shared by framed pages and the PageHeader primitive.
export function PageTitle({ children, title_font = null }) {
  if (title_font !== null && title_font !== 'body' && title_font !== 'heading') {
    throw new Error(`Unsupported page title font: ${title_font}`);
  }
  return (
    <h1
      className="min-w-0 break-words text-foreground"
      style={{
        fontFamily: title_font
          ? `var(--font-${title_font})`
          : 'var(--shell-page-title-font-family, var(--font-heading))',
        fontSize: 'var(--shell-page-title-font-size, 2.25rem)',
        fontWeight: 'var(--shell-page-title-font-weight, 600)',
        lineHeight: 'var(--shell-page-title-line-height, 1.15)',
        letterSpacing: 'var(--shell-page-title-letter-spacing, normal)',
        textTransform: 'var(--shell-page-title-text-transform, none)',
        overflowWrap: 'anywhere',
      }}
    >
      {children}
    </h1>
  );
}
