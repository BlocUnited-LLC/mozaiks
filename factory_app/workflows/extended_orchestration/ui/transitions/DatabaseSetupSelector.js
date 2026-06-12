import {
  TransitionChoiceCard,
  TransitionChoicePanel,
  useTransitionMotion,
} from '@mozaiks/chat-ui/platform';

const OPTION_VIEW = {
  local_mongodb: {
    label: 'Use Local MongoDB',
    description:
      'Design for the default local MongoDB developer setup and keep the initial path simple.',
    cta: 'Use Local',
    badge: 'Recommended',
  },
  mongodb_atlas: {
    label: 'Use MongoDB Atlas',
    description:
      'Plan for a managed Atlas connection and treat credentials as platform-managed secrets.',
    cta: 'Use Atlas',
    badge: 'Managed cloud',
  },
  existing_uri: {
    label: 'Use Existing Mongo URI',
    description:
      'Keep the app on MongoDB, but assume the connection will be supplied from an existing environment secret.',
    cta: 'Use Existing',
    badge: 'Bring your own secret',
  },
  skip_for_now: {
    label: 'Skip For Now',
    description:
      'Continue designing the Mongo-ready schema and app contract now, and connect the database later.',
    cta: 'Skip Setup',
    badge: 'Decide later',
  },
};

const toLabel = (value) =>
  String(value || 'continue')
    .replace(/[_-]+/g, ' ')
    .replace(/\b\w/g, (match) => match.toUpperCase());

export default function DatabaseSetupSelector({ transition, onResolve, overlayTitleId, overlayDescriptionId }) {
  const options = Array.isArray(transition?.options) ? transition.options : [];
  const motion = useTransitionMotion();

  return (
    <TransitionChoicePanel
      eyebrow="Data Layer"
      title="How Should We Plan Your Database?"
      subtitle="Set MongoDB setup intent for the build. Connection secrets stay outside workflow state."
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
                delayMs={100 + index * 60}
              />
            );
          })}
    </TransitionChoicePanel>
  );
}