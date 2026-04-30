import { useCallback, useMemo } from 'react'

import { SectionRenderer, usePageData } from '../../ui/page-renderer/index.js'
import { normalizeSections } from '../../ui/page-renderer/schemaUtils.js'


const LAYOUT_CLASSES = {
  grid: 'grid gap-6 md:grid-cols-2 xl:grid-cols-3',
  sidebar: 'flex gap-6',
  'full-width': 'flex flex-col gap-6',
  split: 'grid gap-6 md:grid-cols-2',
}


function SidebarLayout({ sections, sectionData, refetch }) {
  const [sidebar, ...main] = sections
  return (
    <>
      {sidebar ? (
        <aside className="w-64 shrink-0">
          <SectionRenderer section={sidebar} pageData={sectionData} onRefetch={refetch} />
        </aside>
      ) : null}
      <div className="min-w-0 flex-1 flex flex-col gap-6">
        {main.map((section) => (
          <SectionRenderer key={section.id} section={section} pageData={sectionData} onRefetch={refetch} />
        ))}
      </div>
    </>
  )
}


export function AdminSchemaPanel({ panel }) {
  const sections = useMemo(
    () => normalizeSections(Array.isArray(panel?.sections) ? panel.sections : []),
    [panel],
  )
  const { sectionData, refetch } = usePageData(sections)
  const layout = panel?.layout || 'full-width'
  const layoutClass = LAYOUT_CLASSES[layout] || LAYOUT_CLASSES['full-width']

  const handleRefetch = useCallback((sectionId) => {
    refetch(sectionId)
  }, [refetch])

  const errors = Object.values(sectionData)
    .map((state) => state?.error)
    .filter((value) => typeof value === 'string' && value.trim())

  if (sections.length === 0) {
    return (
      <div className="rounded-lg border border-dashed border-border bg-background p-4 text-sm text-muted-foreground">
        No declarative admin sections are configured for this panel.
      </div>
    )
  }

  return (
    <div className="space-y-4">
      {panel?.description ? (
        <p className="text-sm text-muted-foreground">{panel.description}</p>
      ) : null}

      {errors.length > 0 ? (
        <div className="rounded-lg border border-destructive/40 bg-destructive/10 px-4 py-3 text-sm text-destructive">
          {errors[0]}
        </div>
      ) : null}

      {layout === 'sidebar' ? (
        <div className="flex gap-6">
          <SidebarLayout sections={sections} sectionData={sectionData} refetch={handleRefetch} />
        </div>
      ) : (
        <div className={layoutClass}>
          {sections.map((section) => (
            <SectionRenderer
              key={section.id}
              section={section}
              pageData={sectionData}
              onRefetch={handleRefetch}
            />
          ))}
        </div>
      )}
    </div>
  )
}


export default AdminSchemaPanel
