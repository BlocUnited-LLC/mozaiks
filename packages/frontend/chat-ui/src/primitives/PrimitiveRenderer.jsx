import React from 'react';
import CoreCard from './CoreCard';
import CoreList from './CoreList';
import CoreTable from './CoreTable';
import CoreForm from './CoreForm';
import CoreComposite from './CoreComposite';
import CoreMarkdown from './CoreMarkdown';
import { resolveArtifactType } from './utils';

const PrimitiveRenderer = ({ payload, onAction, actionStatusMap, className = '' }) => {
  const type = resolveArtifactType(payload);
  const sharedProps = { payload, onAction, actionStatusMap, className };

  if (!type) {
    return (
      <div className={`rounded-lg border border-[var(--core-primitive-border,var(--color-border-subtle))] bg-[var(--core-primitive-surface,var(--color-surface))] p-3 text-xs text-[var(--core-primitive-muted,var(--color-text-muted))] ${className}`}>
        Unknown core artifact.
      </div>
    );
  }

  switch (type) {
    case 'core.card':
      return <CoreCard {...sharedProps} />;
    case 'core.list':
      return <CoreList {...sharedProps} />;
    case 'core.table':
      return <CoreTable {...sharedProps} />;
    case 'core.form':
      return <CoreForm {...sharedProps} />;
    case 'core.composite':
      return (
        <CoreComposite
          {...sharedProps}
          renderChild={(child) => (
            <PrimitiveRenderer payload={child} onAction={onAction} actionStatusMap={actionStatusMap} />
          )}
        />
      );
    case 'core.markdown':
      return <CoreMarkdown {...sharedProps} />;
    default:
      return (
        <div className={`rounded-lg border border-[var(--core-primitive-border,var(--color-border-subtle))] bg-[var(--core-primitive-surface,var(--color-surface))] p-3 text-xs text-[var(--core-primitive-muted,var(--color-text-muted))] ${className}`}>
          Unsupported core artifact type: {type}
        </div>
      );
  }
};

export default PrimitiveRenderer;
