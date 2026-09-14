import {
  TransitionChoiceCard,
  TransitionChoicePanel,
  useTransitionMotion,
} from '@mozaiks/chat-ui/platform';

// The person answering this has just described an app idea. "Autonomous" and
// "Guided" name our process; they say nothing about what the chooser gets.
// These name the outcome instead, and avoid build vocabulary - "design docs",
// "app bundle" - that means nothing to someone who has never shipped software.
const OPTION_VIEW = {
  autonomous: {
    label: 'Build it for me',
    description:
      'I make the remaining design calls myself and bring you the finished app to look at.',
    cta: 'Build it for me',
    badge: 'Fastest',
  },
  guided: {
    label: 'Build it with me',
    description:
      'I stop and check with you on the decisions that change how your app looks and works.',
    cta: 'Build it with me',
    badge: 'You review',
  },
};

const toLabel = (value) =>
  String(value || 'continue')
    .replace(/[_-]+/g, ' ')
    .replace(/\b\w/g, (m) => m.toUpperCase());

export default function CodingJourneySelector({ transition, onResolve, overlayTitleId, overlayDescriptionId }) {
  const options = Array.isArray(transition?.options) ? transition.options : [];
  const motion = useTransitionMotion();

  return (
    <TransitionChoicePanel
      eyebrow="Build Mode"
      title="How involved do you want to be?"
      subtitle="Both paths build the same app. This only sets how often I stop to check with you." 
      overlayTitleId={overlayTitleId}
      overlayDescriptionId={overlayDescriptionId}
      entered={motion.entered}
      prefersReducedMotion={motion.prefersReducedMotion}
    >
      {options.map((option, index) => {
            const meta = OPTION_VIEW[option.id] || {
              label: toLabel(option.id),
              description: '',
              cta: 'Continue',
            };
            return (
              <TransitionChoiceCard
                key={option.id}
                optionId={option.id}
                label={meta.label}
                description={meta.description}
                cta={meta.cta || 'Continue'}
                badge={meta.badge || ''}
                onResolve={onResolve}
                entered={motion.entered}
                prefersReducedMotion={motion.prefersReducedMotion}
                delayMs={120 + index * 80}
              />
            );
          })}
    </TransitionChoicePanel>
  );
}
