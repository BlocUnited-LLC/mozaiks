import brownfieldImage from '../../../../app/brand/assets/brownfield.jpg';
import greenfieldImage from '../../../../app/brand/assets/greenfield.jpg';
import {
  TransitionChoiceCard,
  TransitionChoicePanel,
  useTransitionChoiceMotion,
} from '@mozaiks/chat-ui/platform';

const OPTION_VIEW = {
  greenfield_app: {
    label: 'Build Something New',
    description:
      'Start with a fresh concept and let Mozaiks guide it into a build-ready app plan.',
    image: greenfieldImage,
    cta: 'Start Build',
  },
  brownfield_app: {
    label: 'Existing App',
    description:
      'Bring an existing product into Mozaiks for augmentation, workflows, and generated surfaces.',
    image: brownfieldImage,
    cta: 'Coming Soon',
    badge: 'Coming Soon',
    disabled: true,
    helperText:
      'Existing-app onboarding is part of our roadmap but will be available soon.',
  },
};

const toLabel = (value) =>
  String(value || 'continue')
    .replace(/[_-]+/g, ' ')
    .replace(/\b\w/g, (m) => m.toUpperCase());

export default function AppTypeSelector({ transition, onResolve, overlayTitleId, overlayDescriptionId }) {
  const options = Array.isArray(transition?.options) ? transition.options : [];
  const motion = useTransitionChoiceMotion();

  return (
    <TransitionChoicePanel
      eyebrow="Start Here"
      title="Choose Your App Journey"
      subtitle="Start a fresh build now, or preview where existing-product onboarding is heading next."
      overlayTitleId={overlayTitleId}
      overlayDescriptionId={overlayDescriptionId}
      entered={motion.entered}
      prefersReducedMotion={motion.prefersReducedMotion}
    >
      {options.map((option, index) => {
            const meta = OPTION_VIEW[option.id] || {
              label: toLabel(option.id),
              description: '',
              image: null,
              cta: 'Continue',
            };
            return (
              <TransitionChoiceCard
                key={option.id}
                optionId={option.id}
                label={meta.label}
                description={meta.description}
                image={meta.image}
                cta={meta.cta || 'Continue'}
                badge={meta.badge || ''}
                helperText={meta.helperText || ''}
                disabled={meta.disabled === true}
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
