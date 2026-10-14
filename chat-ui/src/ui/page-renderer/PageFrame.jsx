/**
 * PageFrame — runtime-owned outer frame for persistent app pages.
 *
 * This component standardizes:
 *   - outer page padding
 *   - max width / readable measure
 *   - title treatment
 *   - transparent background so the shell owns the scene
 *
 * Agents do not configure this directly. They only choose layout + sections.
 */

import { cn } from '../lib/cn.js';
import { PageTitle } from '../primitives/PageTitle.jsx';
import { ErrorState } from '../primitives/Surface.jsx';

const FRAME_WIDTH_VARS = {
  'grid': 'var(--shell-page-max-width-grid, 1280px)',
  'sidebar': 'var(--shell-page-max-width-sidebar, 1360px)',
  'full-width': 'var(--shell-page-max-width-full-width, 1200px)',
  'split': 'var(--shell-page-max-width-split, 1280px)',
};

export function PageFrame({
  name = null,
  title = null,
  layout = 'full-width',
  children,
  className,
  bodyClassName,
}) {
  if (!Object.hasOwn(FRAME_WIDTH_VARS, layout)) {
    return <ErrorState title="Invalid page layout" message={`Unsupported layout: ${String(layout)}`} />;
  }
  const widthValue = FRAME_WIDTH_VARS[layout];
  const frameStyle = {
    maxWidth: widthValue,
    gap: 'var(--shell-page-section-gap, 2rem)',
    paddingInline: 'clamp(var(--shell-page-padding-x-base, 1rem), 3vw, var(--shell-page-padding-x-xl, 2.5rem))',
    paddingBlock: 'clamp(var(--shell-page-padding-y-base, 2rem), 4vw, var(--shell-page-padding-y-md, 2.5rem))',
  };
  const headerStyle = {
    paddingBottom: 'var(--shell-page-title-padding-bottom, 1rem)',
  };

  return (
    <div
      className={cn('min-h-full min-w-0 bg-transparent text-foreground', className)}
      data-page={name || undefined}
    >
      <div className="mx-auto flex w-full flex-col" style={frameStyle}>
        {title && (
          <header style={headerStyle}>
            <PageTitle>{title}</PageTitle>
          </header>
        )}

        <div className={cn('min-h-0 min-w-0', bodyClassName)}>
          {children}
        </div>
      </div>
    </div>
  );
}

export default PageFrame;
