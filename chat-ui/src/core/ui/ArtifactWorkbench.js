import { useMemo, useState } from 'react';
import { Badge, Button, Card } from '../../ui/primitives/index.js';
import { normalizePrimitiveActions, sendPrimitiveResponse } from './workflowPrimitiveUtils.js';

const fallbackActions = [
  { id: 'approve', label: 'Approve', variant: 'primary', approved: true },
  { id: 'request_changes', label: 'Request Changes', variant: 'secondary', approved: false },
];

export default function ArtifactWorkbench({ payload = {}, onResponse, onCancel }) {
  const sections = useMemo(
    () => (Array.isArray(payload.sections) ? payload.sections.filter((section) => section && typeof section === 'object') : []),
    [payload.sections],
  );
  const [activeSectionId, setActiveSectionId] = useState(sections[0]?.id || '');
  const actions = normalizePrimitiveActions(payload, fallbackActions);
  const activeSection = sections.find((section) => String(section.id) === String(activeSectionId)) || sections[0] || null;

  return (
    <Card
      title={payload.title || 'Artifact workbench'}
      subtitle={payload.summary || 'Review the generated artifact sections and choose how to proceed.'}
      className="border-border/80 bg-card/95 shadow-sm"
    >
      <div className="space-y-4">
        <div className="flex flex-wrap items-center gap-2">
          <Badge label="artifact_workbench" variant="secondary" />
          <Badge label={`${sections.length} section${sections.length === 1 ? '' : 's'}`} variant="outline" />
        </div>

        {sections.length > 0 ? (
          <>
            <div className="flex flex-wrap gap-2">
              {sections.map((section) => (
                <Button
                  key={section.id}
                  label={section.title || section.label || String(section.id)}
                  variant={String(section.id) === String(activeSection?.id) ? 'primary' : 'secondary'}
                  onClick={() => setActiveSectionId(section.id)}
                />
              ))}
            </div>

            {activeSection ? (
              <div className="rounded-md border border-border/60 bg-muted/30 px-4 py-4">
                <p className="text-sm font-medium text-foreground">{activeSection.title || activeSection.label || 'Section'}</p>
                {activeSection.summary ? (
                  <p className="mt-1 text-sm text-muted-foreground">{String(activeSection.summary)}</p>
                ) : null}
                {activeSection.content ? (
                  <pre className="mt-3 overflow-x-auto rounded-md border border-border/60 bg-background p-3 text-xs leading-6 text-foreground">
                    {String(activeSection.content)}
                  </pre>
                ) : null}
              </div>
            ) : null}
          </>
        ) : null}

        <div className="flex flex-wrap gap-3">
          {actions.map((action) => (
            <Button
              key={action.id}
              label={action.label}
              variant={action.variant}
              onClick={() => sendPrimitiveResponse(onResponse, action, { active_section_id: activeSection?.id || null })}
            />
          ))}
          {onCancel ? (
            <Button label="Cancel" variant="ghost" onClick={() => onCancel({ status: 'cancelled', action: 'cancel' })} />
          ) : null}
        </div>
      </div>
    </Card>
  );
}
