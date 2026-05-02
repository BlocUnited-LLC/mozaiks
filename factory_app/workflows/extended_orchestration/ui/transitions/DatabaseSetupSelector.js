import React from 'react';

const OPTION_VIEW = {
  local_mongodb: {
    label: 'Use Local MongoDB',
    description:
      'Design for the default local MongoDB developer setup and keep the initial path simple.',
    cta: 'Use Local',
  },
  mongodb_atlas: {
    label: 'Use MongoDB Atlas',
    description:
      'Plan for a managed Atlas connection and treat credentials as platform-managed secrets.',
    cta: 'Use Atlas',
  },
  existing_uri: {
    label: 'Use Existing Mongo URI',
    description:
      'Keep the app on MongoDB, but assume the connection will be supplied from an existing environment secret.',
    cta: 'Use Existing',
  },
  skip_for_now: {
    label: 'Skip For Now',
    description:
      'Continue designing the Mongo-ready schema and app contract now, and connect the database later.',
    cta: 'Skip Setup',
  },
};

const toLabel = (value) =>
  String(value || 'continue')
    .replace(/[_-]+/g, ' ')
    .replace(/\b\w/g, (match) => match.toUpperCase());

function ChoiceCard({ option, meta, onResolve }) {
  return (
    <button
      type="button"
      onClick={() => onResolve?.(option.id)}
      className="group w-full rounded-lg border border-border/70 bg-card/85 p-5 text-left transition hover:border-primary/60 hover:bg-card"
    >
      <div className="mb-4 h-2 w-16 rounded-full bg-primary/40 transition group-hover:bg-primary/70" />
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

export default function DatabaseSetupSelector({ transition, onResolve }) {
  const options = Array.isArray(transition?.options) ? transition.options : [];

  return (
    <section className="flex min-h-full flex-1 items-center justify-center bg-background px-6 py-12">
      <div className="mx-auto w-full max-w-5xl">
        <header className="mb-8 text-center">
          <h1 className="text-3xl font-bold text-foreground">How Should We Plan Your Database?</h1>
          <p className="mx-auto mt-3 max-w-2xl text-sm text-muted-foreground">
            This step sets MongoDB setup intent for the build. Connection secrets stay outside the workflow state.
          </p>
        </header>
        <div className="grid gap-4 md:grid-cols-2">
          {options.map((option) => {
            const meta = OPTION_VIEW[option.id] || {
              label: toLabel(option.id),
              description: '',
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