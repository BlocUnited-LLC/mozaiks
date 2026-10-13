/**
 * PageRenderer — renders a full AppPageSchema as a live React page.
 *
 * Takes an AppPageSchema (from pages/{name}.yaml, already parsed to JSON)
 * and renders it using:
 *   - Layout wrapper (grid | sidebar | full-width | split)
 *   - SectionRenderer for each declared section
 *   - usePageData for api_endpoint data binding
 *
 * This is the only place that knows about layout. SectionRenderer and
 * PrimitiveRegistry are layout-agnostic.
 *
 * Agents never touch this file. It is purely a renderer contract.
 */

import { useCallback, useMemo } from 'react';
import { SectionRenderer } from './SectionRenderer.jsx';
import { PageFrame } from './PageFrame.jsx';
import { usePageData } from './usePageData.js';
import { normalizeSections } from './schemaUtils.js';
import { ErrorState } from '../primitives/Surface.jsx';

// ---------------------------------------------------------------------------
// Layout wrappers
// ---------------------------------------------------------------------------

const LAYOUT_CLASSES = {
  'grid':       'grid md:grid-cols-2 xl:grid-cols-3',
  'sidebar':    'flex flex-col md:flex-row',
  'full-width': 'flex flex-col',
  'split':      'grid md:grid-cols-2',
};

const SECTION_SPACING = { gap: 'var(--shell-page-section-gap, 2rem)' };

function SidebarLayout({ sections, sectionData, refetch, onNavigate }) {
  // First section goes in the sidebar; the rest fill the main area.
  const [sidebar, ...main] = sections;
  return (
    <>
      {sidebar && (
        <aside className="min-w-0 w-full md:w-64 md:shrink-0">
          <SectionRenderer
            section={sidebar}
            pageData={sectionData}
            onRefetch={refetch}
            onNavigate={onNavigate}
          />
        </aside>
      )}
      <main className="min-w-0 flex-1 flex flex-col" style={SECTION_SPACING}>
        {main.map((section) => (
          <SectionRenderer
            key={section.id}
            section={section}
            pageData={sectionData}
            onRefetch={refetch}
            onNavigate={onNavigate}
          />
        ))}
      </main>
    </>
  );
}

function DefaultLayout({ sections, sectionData, refetch, layoutClass, onNavigate }) {
  return (
    <div className={layoutClass} style={SECTION_SPACING}>
      {sections.map((section) => (
        <SectionRenderer
          key={section.id}
          section={section}
          pageData={sectionData}
          onRefetch={refetch}
          onNavigate={onNavigate}
        />
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// PageRenderer
// ---------------------------------------------------------------------------

/**
 * @param {object}         props
 * @param {AppPageSchema}  props.schema     - Parsed AppPageSchema
 * @param {string}         [props.className]
 * @param {(path:string)=>void} [props.onNavigate]
 */
function ValidPageRenderer({ schema, className, onNavigate }) {
  const { name, title, layout = 'full-width', sections: rawSections = [] } = schema;

  const sections = useMemo(() => normalizeSections(rawSections), [rawSections]);
  const frameTitle = sections.some(section => section.primitive === 'PageHeader') ? null : title;

  const { sectionData, refetch } = usePageData(sections);

  const handleRefetch = useCallback((sectionId, query) => {
    return refetch(sectionId, query);
  }, [refetch]);

  const layoutClass = LAYOUT_CLASSES[layout];

  return (
    <PageFrame
      name={name}
      title={frameTitle}
      layout={layout}
      className={className}
    >
      {layout === 'sidebar' ? (
        <div className={layoutClass} style={SECTION_SPACING}>
          <SidebarLayout
            sections={sections}
            sectionData={sectionData}
            refetch={handleRefetch}
            onNavigate={onNavigate}
          />
        </div>
      ) : (
        <DefaultLayout
          sections={sections}
          sectionData={sectionData}
          refetch={handleRefetch}
          layoutClass={layoutClass}
          onNavigate={onNavigate}
        />
      )}
    </PageFrame>
  );
}

export function PageRenderer(props) {
  const layout = props.schema?.layout === undefined ? 'full-width' : props.schema.layout;
  if (!Object.hasOwn(LAYOUT_CLASSES, layout)) {
    return <ErrorState title="Invalid page layout" message={`Unsupported layout: ${String(layout)}`} />;
  }
  return <ValidPageRenderer {...props} />;
}
