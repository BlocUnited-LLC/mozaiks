import {
  TransitionChoiceCard,
  TransitionChoicePanel,
  useTransitionMotion,
} from '@mozaiks/chat-ui/platform';

const OPTION_VIEW = {
  light_integration: {
    label: 'Light Integration',
    description:
      'Keep your existing app running as-is. Mozaiks adds AI workflows, chat surfaces, and generated capabilities on top — without changing your backend architecture or codebase. Your app owns the data and business logic; Mozaiks augments from the outside.',
    cta: 'Choose Light Integration',
    badge: 'Fastest path',
  },
  full_migration: {
    label: 'Full Migration',
    description:
      'Rebuild as a native Mozaiks app. Your backend becomes Mozaiks modules, your frontend uses the Mozaiks app shell, and you go through the full generation pipeline — design docs, workflows, and app bundle. Best when you want Mozaiks to fully own the stack.',
    cta: 'Choose Full Migration',
    badge: 'Full Mozaiks app',
  },
};

const toLabel = (value) =>
  String(value || 'continue')
    .replace(/[_-]+/g, ' ')
    .replace(/\b\w/g, (m) => m.toUpperCase());

export default function BrownfieldPathSelector({ transition, onResolve, overlayTitleId, overlayDescriptionId }) {
  const options = Array.isArray(transition?.options) ? transition.options : [];
  const motion = useTransitionMotion();

  return (
    <TransitionChoicePanel
      eyebrow="Build Path"
      title="Choose Your Integration Path"
      subtitle="Based on what your discovery session found, choose how deeply to integrate with Mozaiks."
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
