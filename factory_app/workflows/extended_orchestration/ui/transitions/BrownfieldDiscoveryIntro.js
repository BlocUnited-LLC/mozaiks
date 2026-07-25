import {
  TransitionChoicePanel,
  useTransitionMotion,
} from '@mozaiks/chat-ui/platform';

const DISCOVERY_STEPS = [
  {
    label: 'App identity',
    description: "Confirm what your app is called, what it does, and how it's built.",
  },
  {
    label: 'Capability map',
    description: 'Map the features, APIs, and services your app already owns.',
  },
  {
    label: 'Integration plan',
    description: 'Recommend how deeply Mozaiks should augment your app based on what we find.',
  },
];

export default function BrownfieldDiscoveryIntro({ transition, onResolve, overlayTitleId, overlayDescriptionId }) {
  const option = Array.isArray(transition?.options) ? transition.options[0] : null;
  const motion = useTransitionMotion();

  return (
    <TransitionChoicePanel
      eyebrow="Existing App"
      title="Let's explore your app"
      subtitle="We'll have a short conversation to understand what your app already does. At the end, you'll choose how deeply to integrate with Mozaiks."
      overlayTitleId={overlayTitleId}
      overlayDescriptionId={overlayDescriptionId}
      entered={motion.entered}
      prefersReducedMotion={motion.prefersReducedMotion}
    >
      <div className="mx-auto flex w-full max-w-3xl flex-col gap-2">
        {DISCOVERY_STEPS.map(({ label, description }) => (
          <div
            key={label}
            className="flex items-start gap-3 rounded-xl border border-border/60 bg-card/60 px-4 py-3"
          >
            <span className="mt-1.5 h-2 w-2 shrink-0 rounded-full bg-primary/70" />
            <div>
              <p className="text-sm font-semibold text-foreground">{label}</p>
              <p className="mt-0.5 text-xs leading-5 text-muted-foreground">{description}</p>
            </div>
          </div>
        ))}
      </div>
      {option && (
        <div className="mx-auto w-full max-w-3xl">
          <button
            type="button"
            onClick={() => onResolve(option.id)}
            className="w-full rounded-xl bg-primary px-6 py-3.5 text-sm font-semibold text-primary-foreground shadow-sm transition-opacity hover:opacity-90 focus:outline-none focus:ring-2 focus:ring-primary/60 focus:ring-offset-2 focus:ring-offset-background"
          >
            Start Discovery
          </button>
        </div>
      )}
    </TransitionChoicePanel>
  );
}
