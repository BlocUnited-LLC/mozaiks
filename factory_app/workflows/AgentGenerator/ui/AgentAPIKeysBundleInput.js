// ==============================================================================
// FILE: ChatUI/src/workflows/AgentGenerator/components/AgentAPIKeysBundleInput.js
// DESCRIPTION: Consolidated API key intake component for multi-service collection
// ==============================================================================

import { useState, useMemo, useEffect } from 'react';
import { createToolsLogger } from '@mozaiks/chat-ui/platform/workflowSurfaceRuntime.js';

const toTitle = (value = '') => {
  return value
    .split(/[_\s-]+/)
    .filter(Boolean)
    .map((segment) => segment.charAt(0).toUpperCase() + segment.slice(1))
    .join(' ');
};

const AgentAPIKeysBundleInput = ({
  payload = {},
  onResponse,
  toolName,
  toolCallId,
  sourceWorkflowName,
  generatedWorkflowName,
  componentId = 'AgentAPIKeysBundleInput'
}) => {
  const resolvedWorkflowName =
    generatedWorkflowName ||
    sourceWorkflowName ||
    payload.workflowName ||
    payload.workflow_name ||
    null;

  const services = useMemo(() => {
    if (!Array.isArray(payload.services)) {
      return [];
    }

    return payload.services
      .map((rawService, index) => {
        const identifier = typeof rawService?.service === 'string' ? rawService.service.trim().toLowerCase() : '';
        if (!identifier) {
          return null;
        }
        const displayName =
          rawService.service_display_name ||
          rawService.display_name ||
          rawService.displayName ||
          toTitle(identifier);
        const required = rawService.required === undefined ? true : Boolean(rawService.required);
        const maskInput = rawService.maskInput === undefined
          ? (rawService.mask_input === undefined ? true : Boolean(rawService.mask_input))
          : Boolean(rawService.maskInput);

        return {
          id: `${identifier}-${index}`,
          service: identifier,
          displayName,
          label: rawService.label || `${displayName} API Key`,
          description: rawService.description || `Enter your ${displayName} API key to continue.`,
          placeholder: rawService.placeholder || `Enter your ${displayName} API key...`,
          required,
          maskInput,
          agentMessageId:
            rawService.agent_message_id || `${payload.agent_message_id || componentId}:${identifier}:${index}`
        };
      })
      .filter(Boolean);
  }, [payload.services, payload.agent_message_id, componentId]);

  const [formValues, setFormValues] = useState(() => {
    const initial = {};
    services.forEach((svc) => {
      initial[svc.service] = '';
    });
    return initial;
  });

  const [visibility, setVisibility] = useState(() => {
    const initial = {};
    services.forEach((svc) => {
      initial[svc.service] = !svc.maskInput;
    });
    return initial;
  });

  const [errors, setErrors] = useState({});
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [currentIndex, setCurrentIndex] = useState(0);

  useEffect(() => {
    setFormValues((current) => {
      const next = {};
      services.forEach((svc) => {
        next[svc.service] = current[svc.service] || '';
      });
      return next;
    });
    setVisibility((current) => {
      const next = {};
      services.forEach((svc) => {
        next[svc.service] = current.hasOwnProperty(svc.service) ? current[svc.service] : !svc.maskInput;
      });
      return next;
    });
    setErrors((current) => {
      const filtered = {};
      services.forEach((svc) => {
        if (current[svc.service]) {
          filtered[svc.service] = current[svc.service];
        }
      });
      return filtered;
    });
    setCurrentIndex(0);
  }, [services]);

  const currentService = services[currentIndex] || null;

  const agentMessageId = payload.agent_message_id || null;

  const tlog = createToolsLogger({
    tool: toolName || componentId,
    eventId: toolCallId,
    workflowName: resolvedWorkflowName,
    agentMessageId: agentMessageId
  });

  const containerClasses = 'w-full';
  const cardClasses =
    'w-full max-w-xl rounded-2xl border border-[rgba(var(--color-primary-rgb),0.18)] bg-[rgba(10,16,38,0.92)] px-6 py-5 shadow-[0_18px_38px_rgba(8,15,40,0.45)] space-y-5';
  const headingClasses = 'text-lg font-heading font-semibold text-foreground';
  const descriptionClasses = 'text-xs font-sans text-muted-foreground';
  const inputClasses = (hasError, isDisabled, mask, visible) =>
    [
      'w-full rounded-lg border px-4 py-3 transition-colors border-border bg-card text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-primary focus:border-primary',
      mask ? 'pr-12' : '',
      hasError ? 'border-destructive focus:ring-destructive focus:border-destructive' : '',
      isDisabled ? 'opacity-50 cursor-not-allowed' : '',
      visible ? 'tracking-wide' : ''
    ]
      .filter(Boolean)
      .join(' ');
  const assistiveTextClasses = 'text-xs font-sans text-muted-foreground';
  const errorTextClasses = 'text-xs font-sans text-destructive';
  const buttonGroup = 'flex items-center gap-3';
  const secondaryButtonClasses = 'rounded-lg border border-border bg-card px-4 py-2 text-sm font-sans text-muted-foreground transition hover:border-border hover:bg-muted disabled:opacity-60 flex-1';
  const primaryButtonClasses = 'rounded-lg bg-primary px-6 py-3 text-xs font-sans font-bold uppercase tracking-wide text-primary-foreground transition-all hover:bg-primary/90 disabled:opacity-60 flex-1';
  const skipButtonClasses = 'rounded-lg border border-border bg-card px-6 py-3 text-xs font-sans font-bold uppercase tracking-wide text-muted-foreground transition-all hover:bg-muted disabled:opacity-60 flex-1';

  const heading = (() => {
    const explicit = typeof payload.heading === 'string' ? payload.heading.trim() : '';
    if (explicit) {
      return explicit;
    }
    if (currentService) {
      return `Provide ${currentService.displayName} API Key`;
    }
    return 'Provide Required API Keys';
  })();

  const introMessage = (() => {
    const explicit =
      (typeof payload.agent_message === 'string' && payload.agent_message.trim()) ||
      (typeof payload.description === 'string' && payload.description.trim());
    if (explicit) {
      return explicit;
    }
    if (currentService && currentService.description) {
      return currentService.description;
    }
    return 'The workflow needs the following API keys. Only metadata is recorded; secrets are never stored.';
  })();

  const handleChange = (service, value) => {
    setFormValues((current) => ({
      ...current,
      [service]: value
    }));
    if (errors[service]) {
      setErrors((current) => {
        const next = { ...current };
        delete next[service];
        return next;
      });
    }
  };

  const toggleVisibility = (service) => {
    setVisibility((current) => ({
      ...current,
      [service]: !current[service]
    }));
  };

  const validateCurrentService = () => {
    if (!currentService) {
      return {};
    }
    const value = (formValues[currentService.service] || '').trim();
    if (currentService.required && !value) {
      return { [currentService.service]: 'API key is required.' };
    }
    return {};
  };

  const buildSubmissionPayload = (overrides = {}) => {
    return services.map((svc) => {
      const sourceValue = Object.prototype.hasOwnProperty.call(overrides, svc.service)
        ? overrides[svc.service]
        : formValues[svc.service];
      const raw = (sourceValue || '').trim();
      return {
        service: svc.service,
        serviceDisplayName: svc.displayName,
        apiKey: raw,
        hasApiKey: Boolean(raw),
        keyLength: raw.length,
        required: svc.required,
        maskInput: svc.maskInput,
        agent_message_id: svc.agentMessageId
      };
    });
  };

  const finalizeSubmission = async (overrides = {}) => {
    const submission = buildSubmissionPayload(overrides);
    try {
      tlog.event('submit', 'start', {
        services: submission.map((item) => ({ service: item.service, provided: item.hasApiKey }))
      });
      const responsePayload = {
        status: 'success',
        action: 'submit',
        data: {
          services: submission,
          submissionTime: new Date().toISOString(),
          tool_name: toolName,
          tool_call_id: toolCallId,
          workflowName: resolvedWorkflowName,
          sourceWorkflowName,
          generatedWorkflowName,
          agent_message_id: agentMessageId
        }
      };
      if (onResponse) {
        await onResponse(responsePayload);
      }
      setFormValues(() => {
        const cleared = {};
        services.forEach((svc) => {
          cleared[svc.service] = '';
        });
        return cleared;
      });
      setVisibility(() => {
        const next = {};
        services.forEach((svc) => {
          next[svc.service] = !svc.maskInput;
        });
        return next;
      });
      setCurrentIndex(0);
      setErrors({});
      tlog.event('submit', 'done', {
        provided: submission.filter((item) => item.hasApiKey).length,
        requested: services.length
      });
    } catch (submitError) {
      tlog.error('submit failed', {
        error: submitError?.message,
        services: services.length
      });
      if (onResponse) {
        onResponse({
          status: 'error',
          action: 'submit',
          error: submitError?.message || 'Unable to submit API keys.',
          data: {
            tool_name: toolName,
            tool_call_id: toolCallId
          }
        });
      }
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleSubmit = async (event) => {
    event.preventDefault();
    if (!currentService) {
      return;
    }

    const validationErrors = validateCurrentService();
    if (Object.keys(validationErrors).length > 0) {
      setErrors((prev) => ({ ...prev, ...validationErrors }));
      return;
    }

    const trimmedValue = (formValues[currentService.service] || '').trim();
    setIsSubmitting(true);
    setErrors((prev) => {
      const next = { ...prev };
      delete next[currentService.service];
      return next;
    });
    setFormValues((prev) => ({
      ...prev,
      [currentService.service]: trimmedValue
    }));

    try {
      tlog.event('submit_step', 'done', {
        service: currentService.service,
        provided: Boolean(trimmedValue),
        step: currentIndex + 1,
        total: services.length
      });

      if (currentIndex < services.length - 1) {
        setCurrentIndex((prev) => prev + 1);
        setIsSubmitting(false);
      } else {
        await finalizeSubmission({ [currentService.service]: trimmedValue });
      }
    } catch (submitError) {
      tlog.error('submit step failed', {
        service: currentService.service,
        error: submitError?.message
      });
      setIsSubmitting(false);
    }
  };

  const handleSkip = async () => {
    if (!currentService) {
      return;
    }
    setErrors((prev) => {
      const next = { ...prev };
      delete next[currentService.service];
      return next;
    });
    setFormValues((prev) => ({
      ...prev,
      [currentService.service]: ''
    }));
    tlog.event('skip', 'done', {
      service: currentService.service,
      step: currentIndex + 1,
      total: services.length
    });

    if (currentIndex < services.length - 1) {
      setCurrentIndex((prev) => prev + 1);
    } else {
      setIsSubmitting(true);
      await finalizeSubmission({ [currentService.service]: '' });
    }
  };

  const handleCancel = async () => {
    setIsSubmitting(true);
    try {
      tlog.event('cancel', 'start', { services: services.length });
      const responsePayload = {
        status: 'cancelled',
        action: 'cancel',
        data: {
          services: buildSubmissionPayload().map((svc) => ({
            service: svc.service,
            required: svc.required,
            hasApiKey: svc.hasApiKey
          })),
          cancelTime: new Date().toISOString(),
          tool_name: toolName,
          tool_call_id: toolCallId,
          workflowName: resolvedWorkflowName,
          sourceWorkflowName,
          generatedWorkflowName,
          agent_message_id: agentMessageId
        }
      };
      if (onResponse) {
        await onResponse(responsePayload);
      }
      tlog.event('cancel', 'done', { services: services.length });
    } catch (cancelError) {
      tlog.error('cancel failed', { error: cancelError?.message });
    } finally {
      setIsSubmitting(false);
    }
  };

  if (services.length === 0) {
    return (
      <div className={cardClasses}>
        <h2 className={headingClasses}>No API keys required</h2>
        <p className={descriptionClasses}>The workflow did not declare any integrations that need configuration.</p>
      </div>
    );
  }

  if (!currentService) {
    return (
      <div className={cardClasses}>
        <h2 className={headingClasses}>No API keys required</h2>
        <p className={descriptionClasses}>The workflow did not declare any integrations that need configuration.</p>
      </div>
    );
  }

  const currentValue = formValues[currentService.service] || '';
  const currentError = errors[currentService.service];
  const isVisible = visibility[currentService.service];
  const disableSubmit = isSubmitting || (currentService.required && !currentValue.trim());
  const isLastStep = currentIndex === services.length - 1;

  return (
    <div className={containerClasses} data-agent-message-id={agentMessageId || undefined}>
      <div className={cardClasses}>
        <div className="flex items-start gap-3">
          <span
            className="inline-flex h-10 w-10 items-center justify-center rounded-full bg-[rgba(255,200,0,0.12)] text-sm font-semibold"
            aria-hidden="true"
          >
            {'🔑'}
          </span>
          <div className="flex-1 space-y-1.5">
            <h2 className={headingClasses}>{heading}</h2>
            <p className={descriptionClasses}>{introMessage}</p>
            <p className="text-xs font-sans text-muted-foreground">
              Step {currentIndex + 1} of {services.length}
            </p>
          </div>
        </div>

        <form onSubmit={handleSubmit} className="space-y-2 pt-1">
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <label className="text-xs font-sans font-bold uppercase tracking-wide text-muted-foreground">
                {currentService.label}
                {currentService.required ? (
                  <span className="ml-2 text-xs uppercase tracking-wide text-[rgba(255,255,255,0.65)]">Required</span>
                ) : null}
              </label>
              <span className="text-xs font-sans text-muted-foreground">{currentService.service}</span>
            </div>
            <div className="relative">
              <input
                type={isVisible ? 'text' : 'password'}
                value={currentValue}
                onChange={(event) => handleChange(currentService.service, event.target.value)}
                placeholder={currentService.placeholder}
                required={currentService.required}
                disabled={isSubmitting}
                className={inputClasses(Boolean(currentError), isSubmitting, currentService.maskInput, isVisible)}
              />
              {currentService.maskInput && (
                <button
                  type="button"
                  onClick={() => toggleVisibility(currentService.service)}
                  disabled={isSubmitting}
                  className="absolute inset-y-0 right-3 flex items-center text-xs font-semibold uppercase tracking-wide text-[rgba(255,255,255,0.65)] hover:text-white transition-colors"
                >
                  {isVisible ? 'Hide' : 'Show'}
                </button>
              )}
            </div>
            {currentError ? <p className={`${errorTextClasses} mt-1`}>{currentError}</p> : null}
            <p className={assistiveTextClasses}>{currentService.description}</p>
          </div>

          <div className={buttonGroup}>
            <button
              type="button"
              onClick={handleCancel}
              disabled={isSubmitting}
              className={secondaryButtonClasses}
            >
              Cancel
            </button>
            {!currentService.required ? (
              <button
                type="button"
                onClick={handleSkip}
                disabled={isSubmitting}
                className={skipButtonClasses}
              >
                Skip
              </button>
            ) : null}
            <button
              type="submit"
              disabled={disableSubmit}
              className={primaryButtonClasses}
            >
              {isSubmitting ? 'Processing…' : isLastStep ? 'Submit Keys' : 'Next'}
            </button>
          </div>
        </form>

        <div className={`${assistiveTextClasses} space-y-1`}>
          <p>Keys are used immediately to configure your workflow. Only metadata (service + length) is logged.</p>
          <p>Optional integrations can be skipped; required ones must be completed before proceeding.</p>
        </div>
      </div>
    </div>
  );
};

AgentAPIKeysBundleInput.displayName = 'AgentAPIKeysBundleInput';
export default AgentAPIKeysBundleInput;
