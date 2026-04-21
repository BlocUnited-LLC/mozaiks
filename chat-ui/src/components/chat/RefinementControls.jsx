/**
 * RefinementControls - Change-class picker for refining a completed workflow artifact.
 *
 * Props:
 *   artifactKind        — "app_bundle" | "workflow_bundle" | "design_docs" | "concept"
 *   artifactVersionId   — version id of the artifact being refined (optional but recommended)
 *   onDismiss           — called when the user dismisses without submitting
 *
 * The component lets the user select a change class (patch / design / feature / core),
 * enter a refinement request, and submit. The backend router resolves the correct
 * re-entry workflow — this component never hard-codes workflow names.
 */

import { useState } from 'react';
import { useWorkflowStart } from '../../hooks/useWorkflowStart';

const CHANGE_CLASSES = [
  {
    id: 'patch',
    label: 'Patch',
    icon: '🔧',
    description: 'Fix a specific issue or tweak a targeted file. Fastest path — stays within scoped files only.',
  },
  {
    id: 'design',
    label: 'Design',
    icon: '🎨',
    description: 'Revise the visual direction, layout, or schema design without changing core logic.',
  },
  {
    id: 'feature',
    label: 'Feature',
    icon: '✨',
    description: 'Add a new capability. Extends the existing plan while preserving what was built.',
  },
  {
    id: 'core',
    label: 'Core change',
    icon: '🔄',
    description: 'Fundamental change to the concept or goal. Restarts from the planning phase.',
    isDestructive: true,
  },
];

export function RefinementControls({ artifactKind, artifactVersionId, onDismiss }) {
  const [selectedClass, setSelectedClass] = useState(null);
  const [request, setRequest] = useState('');
  const { startWorkflow, starting, error } = useWorkflowStart();

  const canSubmit = selectedClass && request.trim().length > 0 && !starting;

  const handleSubmit = () => {
    if (!canSubmit) return;
    startWorkflow(
      null, // workflow_id resolved by backend router from (change_class, artifact_kind)
      { artifact_version_id: artifactVersionId ?? null },
      {
        trigger_source: 'refinement',
        change_class: selectedClass,
        artifact_kind: artifactKind,
        artifact_version_id: artifactVersionId ?? null,
        raw_user_request: request.trim(),
      }
    );
  };

  return (
    <div className="w-full max-w-2xl mx-auto p-4">
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <div>
          <h3 className="text-base font-semibold text-foreground">Refine this artifact</h3>
          <p className="text-xs text-muted-foreground mt-0.5">
            Choose how much to change, then describe what you want.
          </p>
        </div>
        {onDismiss && (
          <button
            onClick={onDismiss}
            className="text-muted-foreground hover:text-foreground transition-colors p-1"
            aria-label="Dismiss"
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        )}
      </div>

      {/* Change class picker */}
      <div className="grid grid-cols-2 gap-2 mb-4">
        {CHANGE_CLASSES.map((cc) => {
          const isSelected = selectedClass === cc.id;
          return (
            <button
              key={cc.id}
              onClick={() => setSelectedClass(cc.id)}
              className={[
                'text-left p-3 rounded-lg border transition-all duration-150',
                isSelected && !cc.isDestructive
                  ? 'border-primary bg-primary/10 text-foreground'
                  : isSelected && cc.isDestructive
                  ? 'border-destructive bg-destructive/10 text-foreground'
                  : 'border-border bg-card text-foreground hover:border-primary/50 hover:bg-muted',
              ].join(' ')}
            >
              <div className="flex items-center gap-1.5 mb-1">
                <span className="text-base leading-none">{cc.icon}</span>
                <span
                  className={[
                    'text-sm font-semibold',
                    isSelected && cc.isDestructive
                      ? 'text-destructive'
                      : isSelected
                      ? 'text-primary'
                      : 'text-foreground',
                  ].join(' ')}
                >
                  {cc.label}
                </span>
              </div>
              <p className="text-xs text-muted-foreground leading-snug">{cc.description}</p>
            </button>
          );
        })}
      </div>

      {/* Request input */}
      <textarea
        value={request}
        onChange={(e) => setRequest(e.target.value)}
        placeholder={
          selectedClass === 'patch'
            ? 'Describe the specific fix (e.g. "The login button doesn\'t redirect correctly")'
            : selectedClass === 'design'
            ? 'Describe the design change (e.g. "Make the dashboard feel more minimal and dark")'
            : selectedClass === 'feature'
            ? 'Describe the new capability (e.g. "Add a notifications panel for admin users")'
            : selectedClass === 'core'
            ? 'Describe what needs to fundamentally change about this product'
            : 'Select a change type above, then describe what you want...'
        }
        rows={3}
        className="w-full bg-muted border border-border rounded-lg px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground resize-none focus:outline-none focus:border-primary/60 transition-colors mb-3"
      />

      {/* Error */}
      {error && (
        <div className="mb-3 px-3 py-2 rounded-lg bg-destructive/10 border border-destructive/30 text-destructive text-xs">
          {error}
        </div>
      )}

      {/* Actions */}
      <div className="flex items-center justify-end gap-2">
        {onDismiss && (
          <button
            onClick={onDismiss}
            className="px-3 py-1.5 text-sm text-muted-foreground hover:text-foreground border border-border rounded-lg hover:border-primary/40 transition-colors"
          >
            Cancel
          </button>
        )}
        <button
          onClick={handleSubmit}
          disabled={!canSubmit}
          className={[
            'px-4 py-1.5 text-sm font-medium rounded-lg transition-all duration-150',
            canSubmit
              ? 'bg-primary text-primary-foreground hover:bg-primary/90'
              : 'bg-muted text-muted-foreground cursor-not-allowed',
          ].join(' ')}
        >
          {starting ? 'Starting…' : 'Apply refinement'}
        </button>
      </div>
    </div>
  );
}

export default RefinementControls;
