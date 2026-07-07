import { useState } from 'react';
import brownfieldImage from '../../../../app/brand/assets/brownfield.jpg';
import greenfieldImage from '../../../../app/brand/assets/greenfield.jpg';
import {
  TransitionChoiceCard,
  TransitionChoicePanel,
  useTransitionMotion,
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
    cta: 'Start Discovery',
  },
};

const toLabel = (value) =>
  String(value || 'continue')
    .replace(/[_-]+/g, ' ')
    .replace(/\b\w/g, (m) => m.toUpperCase());

const MONETIZATION_SELECTION = {
  intent_id: 'monetization',
  mode: 'auto',
  revenue_model: 'auto',
  allowed_revenue_models: [
    'subscriptions',
    'usage_based',
    'transactional',
    'marketplace',
    'sponsored',
    'donations',
    'community_funded',
    'hybrid',
  ],
  surfaces: ['pricing', 'checkout', 'billing', 'usage', 'marketplace'],
  source: 'factory_app_transition',
};

function buildResolveContext(monetizationSelected) {
  if (!monetizationSelected) return {};
  return {
    monetization_enabled: true,
    builder_options: {
      monetization: {
        enabled: true,
        mode: 'auto',
        revenue_model: 'auto',
      },
      provider_backed_capabilities: [MONETIZATION_SELECTION],
    },
  };
}

export default function AppTypeSelector({ transition, onResolve, overlayTitleId, overlayDescriptionId }) {
  const options = Array.isArray(transition?.options) ? transition.options : [];
  const [monetizationSelected, setMonetizationSelected] = useState(false);
  const motion = useTransitionMotion();
  const resolveContext = buildResolveContext(monetizationSelected);

  return (
    <TransitionChoicePanel
      eyebrow="Start Here"
      title="Choose Your App Journey"
      subtitle="Start a fresh build or bring an existing app into Mozaiks for augmentation and generation."
      overlayTitleId={overlayTitleId}
      overlayDescriptionId={overlayDescriptionId}
      entered={motion.entered}
      prefersReducedMotion={motion.prefersReducedMotion}
    >
      <div
        className="mx-auto flex w-full max-w-3xl items-center justify-between gap-4 rounded-xl border border-border/70 bg-card/80 px-4 py-3 text-left"
        style={{ flexBasis: '100%' }}
      >
        <div>
          <p className="text-sm font-semibold text-foreground">Build with monetization</p>
          <p className="mt-1 text-xs leading-5 text-muted-foreground">
            Include checkout and billing surfaces when this app is generated.
          </p>
        </div>
        <button
          type="button"
          role="switch"
          aria-checked={monetizationSelected}
          onClick={() => setMonetizationSelected((value) => !value)}
          className={[
            'relative inline-flex h-8 w-14 shrink-0 items-center rounded-full border transition-colors focus:outline-none focus:ring-2 focus:ring-primary/60 focus:ring-offset-2 focus:ring-offset-background',
            monetizationSelected
              ? 'border-primary bg-primary'
              : 'border-border bg-muted',
          ].join(' ')}
        >
          <span
            className={[
              'inline-block h-6 w-6 rounded-full bg-background shadow transition-transform',
              monetizationSelected ? 'translate-x-6' : 'translate-x-1',
            ].join(' ')}
          />
        </button>
      </div>
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
                onResolve={(optionId) => onResolve(optionId, resolveContext)}
                entered={motion.entered}
                prefersReducedMotion={motion.prefersReducedMotion}
                delayMs={120 + index * 80}
              />
            );
          })}
    </TransitionChoicePanel>
  );
}
