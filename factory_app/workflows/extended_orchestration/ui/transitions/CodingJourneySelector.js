import {
  TransitionChoiceCard,
  TransitionChoicePanel,
  useTransitionChoiceMotion,
} from '@mozaiks/chat-ui/platform';

const OPTION_VIEW = {
  autonomous: {
    label: 'Autonomous Build',
    description:
      'Let Mozaiks generate the design docs and app bundle with minimal interruption.',
    cta: 'Choose Autonomous',
    badge: 'Fastest path',
  },
  guided: {
    label: 'Guided Build',
    description:
      'Review key design choices before generation continues.',
    cta: 'Choose Guided',
    badge: 'Review checkpoints',
  },
};

const toLabel = (value) =>
  String(value || 'continue')
    .replace(/[_-]+/g, ' ')
    .replace(/\b\w/g, (m) => m.toUpperCase());

export default function CodingJourneySelector({ transition, onResolve, overlayTitleId, overlayDescriptionId }) {
  const options = Array.isArray(transition?.options) ? transition.options : [];
  const motion = useTransitionChoiceMotion();

  return (
    <TransitionChoicePanel
      eyebrow="Build Mode"
      title="Choose Your Build Path"
      subtitle="Decide how much review you want before generation continues into design and code." 
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
