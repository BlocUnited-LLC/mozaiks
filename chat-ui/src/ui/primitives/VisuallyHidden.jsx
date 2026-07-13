/**
 * VisuallyHidden — renders children only for screen readers.
 *
 * Use this when you need to provide context for assistive technology
 * that is redundant or obvious visually, but required for a screen reader
 * to convey full meaning.
 *
 * Common uses:
 *   - Icon-only buttons: <Button><VisuallyHidden>Close</VisuallyHidden><CloseIcon /></Button>
 *   - Form field associations: <label><VisuallyHidden>Search</VisuallyHidden> ... </label>
 *   - Live region announcements: <VisuallyHidden aria-live="polite">{status}</VisuallyHidden>
 *
 * Props:
 *   children  {ReactNode}  — content visible only to assistive technology
 *   as        {string}     — element tag to render (default: "span")
 *   ...props               — passed through to the rendered element (e.g. aria-live)
 */
export function VisuallyHidden({ children, as: Tag = 'span', ...props }) {
  return (
    <Tag
      style={{
        position: 'absolute',
        width: '1px',
        height: '1px',
        padding: 0,
        margin: '-1px',
        overflow: 'hidden',
        clip: 'rect(0, 0, 0, 0)',
        whiteSpace: 'nowrap',
        borderWidth: 0,
      }}
      {...props}
    >
      {children}
    </Tag>
  );
}
