import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Button } from '../primitives/Button.jsx';
import { FocusTrap } from '../primitives/FocusTrap.jsx';

const VALID_PLACEMENTS = new Set(['top', 'bottom', 'left', 'right']);
const SPOTLIGHT_PADDING = 8;
const TOOLTIP_MARGIN = 12;

function normalizeStep(step, index) {
  if (!step || typeof step !== 'object') return null;
  const id = typeof step.id === 'string' && step.id.trim() ? step.id.trim() : `step_${index + 1}`;
  const selector = typeof step.selector === 'string' ? step.selector.trim() : '';
  const title = typeof step.title === 'string' ? step.title.trim() : '';
  const description = typeof step.description === 'string' ? step.description.trim() : '';
  if (!selector || !title || !description) return null;

  const placement = typeof step.placement === 'string' && VALID_PLACEMENTS.has(step.placement)
    ? step.placement
    : 'bottom';

  return {
    id,
    selector,
    title,
    description,
    placement,
  };
}

function normalizeSteps(config) {
  const steps = Array.isArray(config?.steps) ? config.steps : Array.isArray(config) ? config : [];
  return steps.map(normalizeStep).filter(Boolean);
}

function getTargetRect(selector) {
  if (typeof document === 'undefined') return null;
  const target = document.querySelector(selector);
  if (!target) return null;
  const rect = target.getBoundingClientRect();
  return {
    top: rect.top - SPOTLIGHT_PADDING,
    left: rect.left - SPOTLIGHT_PADDING,
    width: rect.width + SPOTLIGHT_PADDING * 2,
    height: rect.height + SPOTLIGHT_PADDING * 2,
    bottom: rect.bottom + SPOTLIGHT_PADDING,
    right: rect.right + SPOTLIGHT_PADDING,
    centerX: rect.left + rect.width / 2,
    centerY: rect.top + rect.height / 2,
  };
}

function resolveStepIndex(steps, status) {
  if (!steps.length) return -1;
  const completedSteps = status?.steps || {};
  const firstIncomplete = steps.findIndex((step) => !completedSteps?.[step.id]?.completed);
  return firstIncomplete >= 0 ? firstIncomplete : -1;
}

function OverlayBackdrop({ rect }) {
  if (!rect) return null;

  return (
    <div aria-hidden="true" className="pointer-events-none fixed inset-0" style={{ zIndex: 9998 }}>
      <div
        className="absolute inset-x-0 top-0"
        style={{
          height: rect.top,
          backgroundColor: 'var(--color-surface-overlay, rgba(0, 0, 0, 0.62))',
        }}
      />
      <div
        className="absolute inset-x-0 bottom-0"
        style={{
          top: rect.bottom,
          backgroundColor: 'var(--color-surface-overlay, rgba(0, 0, 0, 0.62))',
        }}
      />
      <div
        className="absolute left-0"
        style={{
          top: rect.top,
          width: rect.left,
          height: rect.height,
          backgroundColor: 'var(--color-surface-overlay, rgba(0, 0, 0, 0.62))',
        }}
      />
      <div
        className="absolute right-0"
        style={{
          top: rect.top,
          width: Math.max(0, window.innerWidth - rect.right),
          height: rect.height,
          backgroundColor: 'var(--color-surface-overlay, rgba(0, 0, 0, 0.62))',
        }}
      />
      <div
        className="absolute rounded-xl border-2"
        style={{
          top: rect.top,
          left: rect.left,
          width: rect.width,
          height: rect.height,
          borderColor: 'var(--color-primary, #3b82f6)',
          boxShadow: '0 0 0 1px rgba(255, 255, 255, 0.08), 0 0 0 9999px rgba(0, 0, 0, 0.02)',
        }}
      />
    </div>
  );
}

function resolveTooltipPosition(rect, placement, viewportWidth, viewportHeight, tooltipWidth) {
  const preferredPlacement = VALID_PLACEMENTS.has(placement) ? placement : 'bottom';
  const spaceAbove = rect.top;
  const spaceBelow = viewportHeight - rect.bottom;

  let finalPlacement = preferredPlacement;
  if (preferredPlacement === 'bottom' && spaceBelow < 160 && spaceAbove > spaceBelow) {
    finalPlacement = 'top';
  } else if (preferredPlacement === 'top' && spaceAbove < 160 && spaceBelow > spaceAbove) {
    finalPlacement = 'bottom';
  }

  let left = rect.centerX - tooltipWidth / 2;
  left = Math.max(16, Math.min(left, viewportWidth - tooltipWidth - 16));

  let top;
  let translate = 'none';

  if (finalPlacement === 'top') {
    top = rect.top - TOOLTIP_MARGIN;
    translate = 'translateY(-100%)';
  } else if (finalPlacement === 'left') {
    left = rect.left - TOOLTIP_MARGIN - tooltipWidth;
    top = Math.max(16, Math.min(rect.centerY - 8, viewportHeight - 16));
    translate = 'translateY(-50%)';
  } else if (finalPlacement === 'right') {
    left = rect.right + TOOLTIP_MARGIN;
    top = Math.max(16, Math.min(rect.centerY - 8, viewportHeight - 16));
    translate = 'translateY(-50%)';
  } else {
    top = rect.bottom + TOOLTIP_MARGIN;
  }

  left = Math.max(16, Math.min(left, viewportWidth - tooltipWidth - 16));

  return {
    placement: finalPlacement,
    left,
    top,
    translate,
  };
}

function OnboardingTooltip({
  rect,
  step,
  stepIndex,
  totalSteps,
  acting,
  onNext,
  onBack,
  onSkip,
}) {
  if (!rect || !step) return null;

  const viewportWidth = typeof window !== 'undefined' ? window.innerWidth : 1280;
  const viewportHeight = typeof window !== 'undefined' ? window.innerHeight : 800;
  const tooltipWidth = Math.min(320, viewportWidth - 32);
  const position = resolveTooltipPosition(rect, step.placement, viewportWidth, viewportHeight, tooltipWidth);

  return (
    <div
      role="dialog"
      aria-modal="false"
      aria-label={`Onboarding step ${stepIndex + 1} of ${totalSteps}`}
      className="fixed flex flex-col gap-4 rounded-2xl border border-border bg-card/96 p-4 text-card-foreground shadow-2xl backdrop-blur-md"
      style={{
        zIndex: 9999,
        width: tooltipWidth,
        left: position.left,
        top: position.top,
        transform: position.translate,
        boxShadow: 'var(--shadow-elevated, 0 24px 60px rgba(0, 0, 0, 0.4))',
      }}
    >
      <FocusTrap active className="contents">
        <div className="flex items-center justify-between gap-3">
          <span className="rounded-full border border-border bg-background px-2.5 py-1 text-[11px] font-semibold uppercase tracking-[0.18em] text-muted-foreground">
            {stepIndex + 1} / {totalSteps}
          </span>
          <button
            type="button"
            onClick={onSkip}
            disabled={acting}
            className="text-xs font-medium text-muted-foreground transition-colors hover:text-foreground disabled:opacity-50"
          >
            Skip tour
          </button>
        </div>

        <div className="space-y-2">
          <p className="text-sm font-semibold text-foreground">{step.title}</p>
          <p className="text-sm leading-6 text-muted-foreground">{step.description}</p>
        </div>

        <div className="flex items-center gap-1.5" aria-hidden="true">
          {Array.from({ length: totalSteps }).map((_, index) => (
            <div
              key={`${step.id}-dot-${index}`}
              className={`h-1.5 rounded-full transition-all ${
                index === stepIndex ? 'w-5 bg-primary' : 'w-1.5 bg-muted-foreground/30'
              }`}
            />
          ))}
        </div>

        <div className="flex flex-col gap-2 sm:flex-row sm:justify-between">
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="sm:flex-none"
            onClick={onBack}
            disabled={acting || stepIndex === 0}
          >
            Back
          </Button>
          <Button
            type="button"
            size="sm"
            className="sm:flex-1"
            onClick={onNext}
            disabled={acting}
          >
            {acting ? 'Saving...' : stepIndex < totalSteps - 1 ? 'Next' : 'Done'}
          </Button>
        </div>
      </FocusTrap>
    </div>
  );
}

export default function OnboardingTour({
  config = null,
  steps: stepsProp = null,
  loadStatus = null,
  completeStep = null,
  dismissTour = null,
  enabled = true,
}) {
  const steps = useMemo(() => normalizeSteps(stepsProp || config), [config, stepsProp]);
  const [ready, setReady] = useState(false);
  const [stepIndex, setStepIndex] = useState(0);
  const [rect, setRect] = useState(null);
  const [acting, setActing] = useState(false);
  const rafRef = useRef(null);

  useEffect(() => {
    let cancelled = false;
    setReady(false);
    setRect(null);
    setActing(false);

    if (!enabled || steps.length === 0 || typeof loadStatus !== 'function') {
      return () => {
        cancelled = true;
      };
    }

    (async () => {
      try {
        const status = await loadStatus();
        if (cancelled) return;
        if (!status || status.dismissed || Number(status.progress) >= 100) {
          return;
        }
        const firstIncomplete = resolveStepIndex(steps, status);
        if (firstIncomplete < 0) {
          return;
        }
        setStepIndex(firstIncomplete);
        setReady(true);
      } catch {
        // Best effort: if progress cannot load, keep the overlay hidden.
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [enabled, loadStatus, steps]);

  useEffect(() => {
    if (!ready) return undefined;

    const update = () => {
      const currentStep = steps[stepIndex];
      if (!currentStep) return;
      const nextRect = getTargetRect(currentStep.selector);
      setRect(nextRect);
    };

    update();
    window.addEventListener('resize', update);
    window.addEventListener('scroll', update, true);
    rafRef.current = window.setInterval(update, 450);

    return () => {
      window.removeEventListener('resize', update);
      window.removeEventListener('scroll', update, true);
      if (rafRef.current) {
        window.clearInterval(rafRef.current);
      }
    };
  }, [ready, stepIndex, steps]);

  const finalizeTour = useCallback(async () => {
    try {
      await dismissTour?.();
    } catch {
      // Best effort.
    } finally {
      setReady(false);
      setRect(null);
      setActing(false);
    }
  }, [dismissTour]);

  const handleNext = useCallback(async () => {
    const currentStep = steps[stepIndex];
    if (!currentStep) return;

    setActing(true);
    try {
      await completeStep?.(currentStep.id);
    } catch {
      // Best effort: continue the tour even if persistence fails.
    }

    const nextIndex = stepIndex + 1;
    if (nextIndex >= steps.length) {
      await finalizeTour();
      return;
    }

    setStepIndex(nextIndex);
    setActing(false);
  }, [completeStep, finalizeTour, stepIndex, steps]);

  const handleBack = useCallback(() => {
    setStepIndex((current) => Math.max(0, current - 1));
  }, []);

  const handleSkip = useCallback(async () => {
    setActing(true);
    await finalizeTour();
  }, [finalizeTour]);

  if (!ready || !rect || !steps[stepIndex]) {
    return null;
  }

  const currentStep = steps[stepIndex];

  return (
    <>
      <OverlayBackdrop rect={rect} />
      <OnboardingTooltip
        rect={rect}
        step={currentStep}
        stepIndex={stepIndex}
        totalSteps={steps.length}
        acting={acting}
        onNext={handleNext}
        onBack={handleBack}
        onSkip={handleSkip}
      />
    </>
  );
}
