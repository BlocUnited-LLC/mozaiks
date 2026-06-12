import { useState } from 'react';
import {
  TransitionActionPanel,
  TransitionActionButton,
  useTransitionMotion,
} from '@mozaiks/chat-ui/platform';

const STARS = [1, 2, 3, 4, 5];

const STAR_LABELS = {
  1: 'Needs work',
  2: 'Fair',
  3: 'Good',
  4: 'Great',
  5: 'Excellent',
};

export default function BuildSatisfactionRating({ transition, onResolve, overlayTitleId, overlayDescriptionId }) {
  const [hovered, setHovered] = useState(null);
  const [selected, setSelected] = useState(null);
  const [submitting, setSubmitting] = useState(false);
  const motion = useTransitionMotion();

  const activeRating = hovered !== null ? hovered : selected;
  // build_registry_id is available in merged context variables when the rating
  // screen is reached through a build sequence.
  const buildRegistryId = transition?.context?.build_registry_id || '';

  const handleSubmit = async () => {
    if (submitting || selected === null) return;
    setSubmitting(true);
    // Fire-and-forget POST to the platform. If build_intelligence is not
    // installed or the platform is unavailable this silently no-ops.
    try {
      fetch('/api/modules/build_intelligence/record_build_satisfaction', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ rating: selected, build_registry_id: buildRegistryId }),
      }).catch(() => {});
    } catch (_) {}
    onResolve('rated', { satisfaction_rating: selected });
  };

  const handleSkip = () => {
    onResolve('skip', {});
  };

  return (
    <TransitionActionPanel
      eyebrow="Before You Go"
      title="How did this build go?"
      body="Your feedback helps us improve the Mozaiks build experience. This is completely optional and anonymous."
      icon="success"
      overlayTitleId={overlayTitleId}
      overlayDescriptionId={overlayDescriptionId}
      entered={motion.entered}
      prefersReducedMotion={motion.prefersReducedMotion}
    >
      {/* Star rating row */}
      <div
        className="flex items-center justify-center gap-1"
        role="group"
        aria-label="Rate this build from 1 to 5 stars"
        onMouseLeave={() => setHovered(null)}
      >
        {STARS.map((star) => {
          const filled = activeRating !== null && star <= activeRating;
          return (
            <button
              key={star}
              type="button"
              aria-label={`${star} — ${STAR_LABELS[star]}`}
              aria-pressed={selected === star}
              onClick={() => setSelected(star)}
              onMouseEnter={() => setHovered(star)}
              className={[
                'rounded-full p-1.5 text-4xl leading-none transition-transform focus:outline-none focus:ring-2 focus:ring-primary/50 focus:ring-offset-2 focus:ring-offset-background',
                filled
                  ? 'scale-110 text-warning'
                  : 'text-muted-foreground/30 hover:scale-105 hover:text-warning/60',
              ].join(' ')}
            >
              ★
            </button>
          );
        })}
      </div>

      {/* Label beneath stars */}
      <p
        className="mt-2 text-sm text-muted-foreground"
        aria-live="polite"
        style={{ minHeight: '1.5rem' }}
      >
        {activeRating !== null ? STAR_LABELS[activeRating] : ' '}
      </p>

      {/* Action buttons */}
      <div className="mt-6 flex flex-col-reverse items-center gap-3 sm:flex-row sm:justify-center">
        <TransitionActionButton
          label="Skip"
          variant="secondary"
          onClick={handleSkip}
          disabled={submitting}
        />
        <TransitionActionButton
          label={submitting ? 'Sending…' : 'Submit Rating'}
          variant="primary"
          onClick={handleSubmit}
          disabled={selected === null || submitting}
        />
      </div>
    </TransitionActionPanel>
  );
}
