import {
  SectionFrame,
  Badge,
  EmptyState,
  AdminExtensionPanels,
} from '../components/AdminPrimitives.jsx'

const SECTION_VARIANT = {
  users:        'primary',
  billing:      'warning',
  usage:        'success',
  operations:   'default',
  settings:     'default',
  support:      'default',
  integrations: 'default',
}

function IntegrationCard({ panel }) {
  const id = typeof panel === 'string' ? panel : panel?.id
  const label = typeof panel === 'object' ? panel?.label || id : id
  const description = typeof panel === 'object' ? panel?.description || panel?.summary : null
  const section = typeof panel === 'object' ? panel?.section : null
  const variant = SECTION_VARIANT[section] || 'default'

  return (
    <div className="rounded-lg border border-border bg-card p-4 flex flex-col gap-2">
      <div className="flex items-start justify-between gap-2">
        <span className="text-sm font-semibold text-foreground">{label}</span>
        {section && <Badge variant={variant}>{section}</Badge>}
      </div>
      {description && (
        <p className="text-xs text-muted-foreground">{description}</p>
      )}
      <div className="mt-auto pt-2">
        <span className="font-mono text-[10px] text-muted-foreground">{id}</span>
      </div>
    </div>
  )
}

export function IntegrationsSection({ allExtensionPanels, extensionPanels }) {
  // Show all registered extension panels as an integration inventory.
  // Falls back to section-filtered panels if allExtensionPanels is not provided.
  const panels = allExtensionPanels ?? extensionPanels ?? []

  return (
    <SectionFrame
      title="Integrations"
      description="Connected modules and features, with their admin panel surfaces."
    >
      {panels.length === 0 ? (
        <EmptyState>
          No integrations configured. Connect an app backend and declare admin panels in your
          module's <code className="font-mono text-xs">admin.yaml</code> to see them listed here.
        </EmptyState>
      ) : (
        <>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 mb-4">
            {panels.map((panel) => {
              const id = typeof panel === 'string' ? panel : panel?.id
              return <IntegrationCard key={id} panel={panel} />
            })}
          </div>
          {/* Render any integrations-section panels inline */}
          <AdminExtensionPanels panels={extensionPanels} />
        </>
      )}
    </SectionFrame>
  )
}
