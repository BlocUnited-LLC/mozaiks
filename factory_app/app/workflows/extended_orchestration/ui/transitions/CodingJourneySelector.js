import React from 'react';

const OPTION_VIEW = {
  autonomous: {
    label: 'Autonomous Build',
    description:
      'Let Mozaiks generate the design docs and app bundle with minimal interruption.',
    image: null,
    cta: 'Continue',
  },
  guided: {
    label: 'Guided Build',
    description:
      'Review key design choices before generation continues.',
    image: null,
    cta: 'Continue',
  },
};

const toLabel = (value) =>
  String(value || 'continue')
    .replace(/[_-]+/g, ' ')
    .replace(/\b\w/g, (m) => m.toUpperCase());

function ChoiceCard({ option, meta, onResolve }) {
  return (
    <button
      type="button"
      onClick={() => onResolve?.(option.id)}
      className="group w-full rounded-lg border border-border/70 bg-card/85 p-5 text-left transition hover:border-primary/60 hover:bg-card"
    >
      {meta.image ? (
        <img
          src={meta.image}
          alt=""
          aria-hidden="true"
          className="mb-4 h-24 w-full rounded-md border border-border/70 object-cover"
        />
      ) : (
        <div className="mb-4 h-2 w-16 rounded-full bg-primary/40 transition group-hover:bg-primary/70" />
      )}
      <h2 className="text-base font-semibold text-foreground">{meta.label}</h2>
      {meta.description ? (
        <p className="mt-2 text-sm text-muted-foreground">{meta.description}</p>
      ) : null}
      <div className="mt-4 inline-flex rounded-md bg-primary/20 px-3 py-1 text-xs font-semibold uppercase text-primary">
        {meta.cta || 'Continue'}
      </div>
    </button>
  );
}

export default function CodingJourneySelector({ transition, onResolve }) {
  const options = Array.isArray(transition?.options) ? transition.options : [];

  return (
    <section className="flex min-h-full flex-1 items-center justify-center bg-background px-6 py-12">
      <div className="mx-auto w-full max-w-5xl">
        <header className="mb-8 text-center">
          <h1 className="text-3xl font-bold text-foreground">Choose Your Build Path</h1>
        </header>
        <div className="grid gap-4 md:grid-cols-2">
          {options.map((option) => {
            const meta = OPTION_VIEW[option.id] || {
              label: toLabel(option.id),
              description: '',
              image: null,
              cta: 'Continue',
            };
            return (
              <ChoiceCard
                key={option.id}
                option={option}
                meta={meta}
                onResolve={onResolve}
              />
            );
          })}
        </div>
      </div>
    </section>
  );
}
