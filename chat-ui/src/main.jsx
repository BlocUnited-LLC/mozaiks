/**
 * Minimal Demo App for mozaiks chat-ui
 *
 * This is a development/testing entry point that wires up @mozaiks/chat-ui
 * primitives with mock adapters. No platform dependencies (auth, subscription,
 * profile, notifications).
 *
 * For a full product app, wire these primitives into your app shell and auth/runtime adapters.
 */
import React from 'react';
import ReactDOM from 'react-dom/client';
import OnboardingTour from './ui/components/OnboardingTour.jsx';

const onboardingDemoConfig = {
  version: 1,
  steps: [
    {
      id: 'create_app',
      selector: 'a[href="/demo/onboarding/create"]',
      title: 'Create your first app',
      description: 'Use this anchor as the stable tour target for the create-app entry point.',
      placement: 'bottom',
    },
    {
      id: 'explore_marketplace',
      selector: 'a[href="/demo/onboarding/marketplace"]',
      title: 'Explore the marketplace',
      description: 'The tour can point at any stable anchor in the layout, not just navigation chrome.',
      placement: 'bottom',
    },
    {
      id: 'choose_plan',
      selector: 'a[href="/demo/onboarding/pricing"]',
      title: 'Review pricing',
      description: 'The component is driven by declarative step data, so this demo can be reused in browser checks.',
      placement: 'bottom',
    },
  ],
};

async function loadDemoOnboardingStatus() {
  return {
    seen_welcome: true,
    dismissed: false,
    steps: {},
    progress: 0,
  };
}

async function completeDemoStep() {
  return { success: true };
}

async function dismissDemoTour() {
  return { success: true };
}

function DemoOnboardingPage() {
  return (
    <div className="min-h-screen bg-background px-6 py-8 text-foreground">
      <div className="mx-auto flex max-w-5xl flex-col gap-6">
        <header className="space-y-2">
          <p className="text-xs font-semibold uppercase tracking-[0.28em] text-muted-foreground">
            Chat UI demo
          </p>
          <h1 className="text-3xl font-semibold">Onboarding tour demo</h1>
          <p className="max-w-2xl text-sm leading-6 text-muted-foreground">
            This page exercises the shared OSS onboarding overlay with declarative step data and stable anchors.
          </p>
        </header>

        <nav className="flex flex-wrap gap-3 rounded-2xl border border-border bg-card p-4 shadow-sm">
          <a
            href="/demo/onboarding/create"
            className="rounded-full border border-border px-4 py-2 text-sm font-medium hover:bg-accent hover:text-accent-foreground"
          >
            Create App
          </a>
          <a
            href="/demo/onboarding/marketplace"
            className="rounded-full border border-border px-4 py-2 text-sm font-medium hover:bg-accent hover:text-accent-foreground"
          >
            Marketplace
          </a>
          <a
            href="/demo/onboarding/pricing"
            className="rounded-full border border-border px-4 py-2 text-sm font-medium hover:bg-accent hover:text-accent-foreground"
          >
            Pricing
          </a>
        </nav>

        <section className="grid gap-4 md:grid-cols-3">
          <div className="rounded-2xl border border-border bg-card p-5 shadow-sm">
            <h2 className="text-sm font-semibold">Purpose</h2>
            <p className="mt-2 text-sm leading-6 text-muted-foreground">
              Verify the shared overlay, copy, and theme tokens without relying on App Zero module state.
            </p>
          </div>
          <div className="rounded-2xl border border-border bg-card p-5 shadow-sm">
            <h2 className="text-sm font-semibold">Data source</h2>
            <p className="mt-2 text-sm leading-6 text-muted-foreground">
              Config is passed as a deterministic object in the OSS demo entry point.
            </p>
          </div>
          <div className="rounded-2xl border border-border bg-card p-5 shadow-sm">
            <h2 className="text-sm font-semibold">Persistence</h2>
            <p className="mt-2 text-sm leading-6 text-muted-foreground">
              The demo uses no-op callbacks so the browser flow can be tested without backend calls.
            </p>
          </div>
        </section>

        <OnboardingTour
          config={onboardingDemoConfig}
          loadStatus={loadDemoOnboardingStatus}
          completeStep={completeDemoStep}
          dismissTour={dismissDemoTour}
        />
      </div>
    </div>
  );
}

function DemoApp() {
  return (
    <DemoOnboardingPage />
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <DemoApp />
  </React.StrictMode>
);
