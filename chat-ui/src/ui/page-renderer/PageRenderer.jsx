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
import { cn } from '../lib/cn.js';

// ---------------------------------------------------------------------------
// Layout wrappers
// ---------------------------------------------------------------------------

const LAYOUT_CLASSES = {
  'grid':       'grid gap-6 md:grid-cols-2 xl:grid-cols-3',
  'sidebar':    'flex gap-6',
  'full-width': 'flex flex-col gap-6',
  'split':      'grid gap-6 md:grid-cols-2',
};

function SidebarLayout({ sections, sectionData, refetch, onNavigate }) {
  // First section goes in the sidebar; the rest fill the main area.
  const [sidebar, ...main] = sections;
  return (
    <>
      {sidebar && (
        <aside className="w-64 shrink-0">
          <SectionRenderer
            section={sidebar}
            pageData={sectionData}
            onRefetch={refetch}
            onNavigate={onNavigate}
          />
        </aside>
      )}
      <main className="min-w-0 flex-1 flex flex-col gap-6">
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
    <div className={layoutClass}>
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
export function PageRenderer({ schema, className, onNavigate }) {
  const { name, title, layout = 'full-width', sections: rawSections = [] } = schema;

  const sections = useMemo(() => normalizeSections(rawSections), [rawSections]);
  const frameTitle = sections[0]?.primitive === 'PageHeader' ? null : title;

  const { sectionData, refetch } = usePageData(sections);

  const handleRefetch = useCallback((sectionId) => {
    refetch(sectionId);
  }, [refetch]);

  const layoutClass = LAYOUT_CLASSES[layout] ?? LAYOUT_CLASSES['full-width'];

  return (
    <PageFrame
      name={name}
      title={frameTitle}
      layout={layout}
      className={className}
    >
      {layout === 'sidebar' ? (
        <div className="flex gap-6">
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
