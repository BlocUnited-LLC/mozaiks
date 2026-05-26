import {
  TransitionChoiceCard,
  TransitionChoicePanel,
  useTransitionChoiceMotion,
} from '@mozaiks/chat-ui/platform';

const OPTION_VIEW = {
  light_integration: {
    label: 'Light Integration',
    description:
      'Add AI workflows and generated surfaces alongside your existing app without changing its architecture. Best for embed and bridge adoption.',
    cta: 'Choose Light Integration',
    badge: 'Fastest path',
  },
  full_migration: {
    label: 'Full Migration',
    description:
      'Rebuild as canonical Mozaiks modules with full design, workflow, and app generation. Best for native migration and ecosystem adoption.',
    cta: 'Choose Full Migration',
    badge: 'Recommended for Mozaiks apps',
  },
};

const toLabel = (value) =>
  String(value || 'continue')
    .replace(/[_-]+/g, ' ')
    .replace(/\b\w/g, (m) => m.toUpperCase());

export default function BrownfieldPathSelector({ transition, onResolve, overlayTitleId, overlayDescriptionId }) {
  const options = Array.isArray(transition?.options) ? transition.options : [];
  const motion = useTransitionChoiceMotion();

  return (
    <TransitionChoicePanel
      eyebrow="Build Path"
      title="Choose Your Integration Path"
      subtitle="Select how deeply to integrate your existing app based on the adoption level your discovery session recommended."
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
