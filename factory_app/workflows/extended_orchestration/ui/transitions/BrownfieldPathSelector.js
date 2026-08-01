import {
  TransitionChoiceCard,
  TransitionChoicePanel,
  useTransitionMotion,
} from '@mozaiks/chat-ui/platform';

const OPTION_VIEW = {
  light_integration: {
    label: 'Add AI Workflows',
    description:
      'Add app-aware AI workflows and chat surfaces around the product you already have. Your app stays the source of truth.',
    helperText: 'Best first move for most existing apps.',
    cta: 'Add AI Workflows',
    badge: 'Recommended first',
  },
  full_migration: {
    label: 'Build App Features',
    description:
      'Build approved Mozaiks modules, pages, workflows, and data contracts for deeper app functionality beyond AI chat.',
    helperText: 'Use this when you want Mozaiks to own selected product areas.',
    cta: 'Build App Features',
    badge: 'Advanced build',
  },
};

const PATH_GUIDE = [
  {
    label: 'Existing code',
    light: 'Stays intact and keeps owning the product.',
    full: 'Only approved areas move into Mozaiks.',
  },
  {
    label: 'First build',
    light: 'App-aware AI workflows and chat surfaces.',
    full: 'Modules, pages, workflows, and data contracts.',
  },
  {
    label: 'Best for',
    light: 'Adding useful AI without changing the app.',
    full: 'Extending the product with Mozaiks-owned functionality.',
  },
];

function getGuideStyle(entered, prefersReducedMotion) {
  if (prefersReducedMotion) return undefined;
  return {
    opacity: entered ? 1 : 0,
    transform: entered ? 'translateY(0px)' : 'translateY(10px)',
    transition: 'opacity 420ms cubic-bezier(0.22, 1, 0.36, 1), transform 420ms cubic-bezier(0.22, 1, 0.36, 1)',
    transitionDelay: '40ms',
  };
}

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
      title="Choose Your App Upgrade Path"
      subtitle="Add AI workflows first, or build approved app features with Mozaiks when you are ready."
      overlayTitleId={overlayTitleId}
      overlayDescriptionId={overlayDescriptionId}
      entered={motion.entered}
      prefersReducedMotion={motion.prefersReducedMotion}
    >
      <div
        className="w-full max-w-5xl rounded-lg border border-border/70 bg-card/75 p-4 text-left shadow-xl shadow-black/10"
        style={getGuideStyle(motion.entered, motion.prefersReducedMotion)}
      >
        <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <p className="text-xs font-semibold uppercase tracking-wide text-primary/80">
              Quick difference
            </p>
            <h2 className="mt-1 text-base font-semibold text-foreground">
              AI workflows keep your app intact. Modules add deeper product capability.
            </h2>
          </div>
          <span className="w-fit rounded-full border border-success/35 bg-success/10 px-3 py-1 text-xs font-semibold text-success">
            AI workflows first
          </span>
        </div>

        <div className="mt-4 grid gap-3 md:grid-cols-3">
          {PATH_GUIDE.map((item) => (
            <div key={item.label} className="rounded-md border border-border/55 bg-background/35 p-3">
              <p className="text-sm font-semibold text-foreground">{item.label}</p>
              <div className="mt-3 space-y-2 text-xs leading-5 text-muted-foreground">
                <p>
                  <span className="font-semibold text-success">AI workflows:</span> {item.light}
                </p>
                <p>
                  <span className="font-semibold text-primary">Modules:</span> {item.full}
                </p>
              </div>
            </div>
          ))}
        </div>
      </div>

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
            helperText={meta.helperText || ''}
            badge={meta.badge || ''}
            onResolve={onResolve}
            entered={motion.entered}
            prefersReducedMotion={motion.prefersReducedMotion}
            delayMs={160 + index * 80}
          />
        );
      })}
    </TransitionChoicePanel>
  );
}
