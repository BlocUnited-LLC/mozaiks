import { useEffect, useMemo, useState } from 'react';
import {
  CheckCircle2,
  ExternalLink,
  KeyRound,
  Link,
  Plug,
  ShieldCheck
} from 'lucide-react';
import { createToolsLogger } from '@mozaiks/chat-ui/platform/workflowSurfaceRuntime.js';
import { workflowSurfaceStyles } from '@mozaiks/chat-ui/platform/workflowSurfaceStyles.js';

const SECRET_TYPES = new Set(['secret', 'password', 'api_key', 'token']);

const LANE_META = {
  managed: {
    label: 'Use managed service',
    description: 'Let the platform provide the connection when a managed setup is available.',
    icon: ShieldCheck
  },
  connect_account: {
    label: 'Connect account',
    description: 'Use a provider login or consent flow when the host exposes one.',
    icon: Link
  },
  bring_your_own_key: {
    label: 'Add setup values',
    description: 'Provide the account values needed by this generated app.',
    icon: KeyRound
  },
  not_required: {
    label: 'No setup required',
    description: 'Continue without configuring this optional integration.',
    icon: CheckCircle2
  }
};

const normalizeLaneId = (value) => {
  const raw = String(value || '').trim().toLowerCase().replace(/[-\s]+/g, '_');
  const aliases = {
    api_key: 'bring_your_own_key',
    byok: 'bring_your_own_key',
    credentials: 'bring_your_own_key',
    credential: 'bring_your_own_key',
    oauth: 'connect_account',
    oauth_connect: 'connect_account',
    hosted: 'managed',
    none: 'not_required',
    skip: 'not_required'
  };
  const lane = aliases[raw] || raw;
  return LANE_META[lane] ? lane : '';
};

const toTitle = (value = '') => {
  return value
    .split(/[_\s-]+/)
    .filter(Boolean)
    .map((segment) => segment.charAt(0).toUpperCase() + segment.slice(1))
    .join(' ');
};

const normalizeField = (field, index) => {
  if (!field || typeof field !== 'object') {
    return null;
  }
  const name = String(field.name || '').trim();
  if (!name) {
    return null;
  }
  const type = String(field.type || 'text').trim().toLowerCase();
  const secret = Boolean(field.secret) || SECRET_TYPES.has(type);
  return {
    name,
    label: field.label || toTitle(name),
    type: secret ? 'secret' : type,
    required: field.required === undefined ? true : Boolean(field.required),
    frontendSafe: secret ? false : field.frontend_safe !== false,
    options: Array.isArray(field.options) ? field.options : [],
    order: index
  };
};

const normalizeFields = (rawService, displayName) => {
  const declared = Array.isArray(rawService?.required_fields)
    ? rawService.required_fields.map(normalizeField).filter(Boolean)
    : [];
  if (declared.length > 0) {
    return declared;
  }
  return [
    {
      name: 'api_key',
      label: rawService?.label || `${displayName} API Key`,
      type: 'secret',
      required: rawService?.required === undefined ? true : Boolean(rawService.required),
      frontendSafe: false,
      options: [],
      order: 0
    }
  ];
};

const normalizeLaneIds = (raw) => {
  if (!Array.isArray(raw)) {
    const lane = normalizeLaneId(raw);
    return lane ? [lane] : [];
  }
  const lanes = [];
  raw.forEach((entry) => {
    const lane = normalizeLaneId(typeof entry === 'object' && entry ? entry.id : entry);
    if (lane && !lanes.includes(lane)) {
      lanes.push(lane);
    }
  });
  return lanes;
};

const normalizeServices = (payload = {}, componentId) => {
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
      const fields = normalizeFields(rawService, displayName);
      const allowedLanes = normalizeLaneIds(rawService.allowed_setup_lanes);
      const legacyLanes = normalizeLaneIds(rawService.setup_lanes);
      const explicitLanes = allowedLanes.length > 0 ? allowedLanes : legacyLanes;
      const lanes = explicitLanes.length > 0 ? explicitLanes : ['bring_your_own_key'];
      const preferredLane = normalizeLaneId(rawService.preferred_setup_lane);
      const activeLane = preferredLane && lanes.includes(preferredLane) ? preferredLane : lanes[0];

      return {
        id: `${identifier}-${index}`,
        service: identifier,
        integrationId: rawService.integration_id || identifier,
        provider: rawService.provider || identifier,
        displayName,
        description:
          rawService.description ||
          rawService.purpose ||
          `Configure ${displayName} so the generated app can use it.`,
        required: rawService.required === undefined ? true : Boolean(rawService.required),
        fields,
        lanes,
        preferredLane: activeLane,
        managedDefault: rawService.managed_default || null,
        integrationsUrl: payload.integrations_url || null,
        agentMessageId:
          rawService.agent_message_id || `${payload.agent_message_id || componentId}:${identifier}:${index}`
      };
    })
    .filter(Boolean);
};

const buildInitialValues = (services) => {
  const values = {};
  services.forEach((svc) => {
    values[svc.service] = {};
    svc.fields.forEach((field) => {
      values[svc.service][field.name] = '';
    });
  });
  return values;
};

const buildInitialVisibility = (services) => {
  const visibility = {};
  services.forEach((svc) => {
    visibility[svc.service] = {};
    svc.fields.forEach((field) => {
      visibility[svc.service][field.name] = field.type !== 'secret';
    });
  });
  return visibility;
};

const buildInitialLanes = (services) => {
  const lanes = {};
  services.forEach((svc) => {
    lanes[svc.service] = svc.preferredLane;
  });
  return lanes;
};

const fieldInputType = (field, visible) => {
  if (field.type === 'secret') {
    return visible ? 'text' : 'password';
  }
  if (field.type === 'url') {
    return 'url';
  }
  if (field.type === 'number') {
    return 'number';
  }
  return 'text';
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

  const services = useMemo(
    () => normalizeServices(payload, componentId),
    [payload, componentId]
  );
  const [currentIndex, setCurrentIndex] = useState(0);
  const [formValues, setFormValues] = useState(() => buildInitialValues(services));
  const [visibility, setVisibility] = useState(() => buildInitialVisibility(services));
  const [selectedLanes, setSelectedLanes] = useState(() => buildInitialLanes(services));
  const [errors, setErrors] = useState({});
  const [isSubmitting, setIsSubmitting] = useState(false);

  useEffect(() => {
    setFormValues((current) => {
      const next = buildInitialValues(services);
      services.forEach((svc) => {
        svc.fields.forEach((field) => {
          next[svc.service][field.name] = current?.[svc.service]?.[field.name] || '';
        });
      });
      return next;
    });
    setVisibility((current) => {
      const next = buildInitialVisibility(services);
      services.forEach((svc) => {
        svc.fields.forEach((field) => {
          if (current?.[svc.service]?.[field.name] !== undefined) {
            next[svc.service][field.name] = current[svc.service][field.name];
          }
        });
      });
      return next;
    });
    setSelectedLanes((current) => ({ ...buildInitialLanes(services), ...current }));
    setErrors({});
    setCurrentIndex(0);
  }, [services]);

  const currentService = services[currentIndex] || null;
  const agentMessageId = payload.agent_message_id || null;

  const tlog = createToolsLogger({
    tool: toolName || componentId,
    eventId: toolCallId,
    workflowName: resolvedWorkflowName,
    agentMessageId
  });

  const heading = (() => {
    const explicit = typeof payload.heading === 'string' ? payload.heading.trim() : '';
    if (explicit) {
      return explicit;
    }
    return currentService ? `Configure ${currentService.displayName}` : 'Configure Integrations';
  })();

  const introMessage = (() => {
    const explicit =
      (typeof payload.agent_message === 'string' && payload.agent_message.trim()) ||
      (typeof payload.description === 'string' && payload.description.trim());
    if (explicit) {
      return explicit;
    }
    if (currentService) {
      return currentService.description;
    }
    return 'Choose a setup path and provide only the fields needed to continue.';
  })();

  const inputClasses = (hasError, isDisabled) =>
    [
      'w-full rounded-lg border px-4 py-3 transition-colors border-border bg-card text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-primary focus:border-primary',
      hasError ? 'border-destructive focus:ring-destructive focus:border-destructive' : '',
      isDisabled ? 'opacity-50 cursor-not-allowed' : ''
    ]
      .filter(Boolean)
      .join(' ');

  const cardClasses =
    'w-full max-w-2xl rounded-lg border border-[rgba(var(--color-primary-rgb),0.18)] bg-[rgba(10,16,38,0.92)] px-6 py-5 shadow-[0_18px_38px_rgba(8,15,40,0.45)] space-y-5';
  const headingClasses = 'text-lg font-heading font-semibold text-foreground';
  const descriptionClasses = 'text-xs font-sans text-muted-foreground';
  const assistiveTextClasses = workflowSurfaceStyles.assistiveText;
  const errorTextClasses = workflowSurfaceStyles.errorText;
  const buttonGroup = workflowSurfaceStyles.buttonGroup;
  const secondaryButtonClasses = workflowSurfaceStyles.secondaryButton;
  const primaryButtonClasses = workflowSurfaceStyles.primaryButton;
  const skipButtonClasses = workflowSurfaceStyles.skipButton;

  const updateField = (service, fieldName, value) => {
    setFormValues((current) => ({
      ...current,
      [service]: {
        ...(current[service] || {}),
        [fieldName]: value
      }
    }));
    setErrors((current) => {
      const next = { ...current };
      delete next[`${service}.${fieldName}`];
      return next;
    });
  };

  const toggleVisibility = (service, fieldName) => {
    setVisibility((current) => ({
      ...current,
      [service]: {
        ...(current[service] || {}),
        [fieldName]: !current?.[service]?.[fieldName]
      }
    }));
  };

  const setLane = (service, lane) => {
    setSelectedLanes((current) => ({
      ...current,
      [service]: lane
    }));
  };

  const validateCurrentService = () => {
    if (!currentService) {
      return {};
    }
    if (selectedLanes[currentService.service] === 'not_required') {
      return {};
    }
    const validationErrors = {};
    currentService.fields.forEach((field) => {
      const value = String(formValues?.[currentService.service]?.[field.name] || '').trim();
      if (field.required && !value) {
        validationErrors[`${currentService.service}.${field.name}`] = 'Required setup field is missing.';
      }
    });
    return validationErrors;
  };

  const buildSubmissionPayload = (overrides = {}) => {
    return services.map((svc) => {
      const values = { ...(formValues[svc.service] || {}), ...(overrides[svc.service] || {}) };
      const trimmedValues = {};
      svc.fields.forEach((field) => {
        trimmedValues[field.name] = String(values[field.name] || '').trim();
      });
      const firstSecret = svc.fields.find((field) => field.type === 'secret');
      const rawSecret = firstSecret ? trimmedValues[firstSecret.name] || '' : '';
      return {
        service: svc.service,
        serviceDisplayName: svc.displayName,
        integration_id: svc.integrationId,
        provider: svc.provider,
        apiKey: rawSecret,
        hasApiKey: Boolean(rawSecret),
        keyLength: rawSecret.length,
        required: svc.required,
        maskInput: svc.fields.some((field) => field.type === 'secret'),
        selected_setup_lane: selectedLanes[svc.service] || svc.preferredLane,
        fields: trimmedValues,
        agent_message_id: svc.agentMessageId
      };
    });
  };

  const finalizeSubmission = async (overrides = {}) => {
    const submission = buildSubmissionPayload(overrides);
    try {
      tlog.event('submit', 'start', {
        services: submission.map((item) => ({
          service: item.service,
          lane: item.selected_setup_lane,
          provided: item.hasApiKey
        }))
      });
      if (onResponse) {
        await onResponse({
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
        });
      }
      setFormValues(buildInitialValues(services));
      setVisibility(buildInitialVisibility(services));
      setSelectedLanes(buildInitialLanes(services));
      setCurrentIndex(0);
      setErrors({});
      tlog.event('submit', 'done', {
        configured: submission.filter((item) => item.hasApiKey || item.selected_setup_lane === 'not_required').length,
        requested: services.length
      });
    } catch (submitError) {
      tlog.error('submit failed', {
        error: submitError?.message,
        services: services.length
      });
      if (onResponse) {
        await onResponse({
          status: 'error',
          action: 'submit',
          error: submitError?.message || 'Unable to save integration setup.',
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
      setErrors((current) => ({ ...current, ...validationErrors }));
      return;
    }

    setIsSubmitting(true);
    const trimmed = {};
    currentService.fields.forEach((field) => {
      trimmed[field.name] = String(formValues?.[currentService.service]?.[field.name] || '').trim();
    });
    setFormValues((current) => ({
      ...current,
      [currentService.service]: trimmed
    }));

    tlog.event('submit_step', 'done', {
      service: currentService.service,
      lane: selectedLanes[currentService.service],
      step: currentIndex + 1,
      total: services.length
    });

    if (currentIndex < services.length - 1) {
      setCurrentIndex((index) => index + 1);
      setIsSubmitting(false);
      return;
    }
    await finalizeSubmission({ [currentService.service]: trimmed });
  };

  const handleSkip = async () => {
    if (!currentService) {
      return;
    }
    setLane(currentService.service, 'not_required');
    setErrors({});
    if (currentIndex < services.length - 1) {
      setCurrentIndex((index) => index + 1);
      return;
    }
    setIsSubmitting(true);
    await finalizeSubmission({ [currentService.service]: {} });
  };

  const handleCancel = async () => {
    setIsSubmitting(true);
    try {
      tlog.event('cancel', 'start', { services: services.length });
      if (onResponse) {
        await onResponse({
          status: 'cancelled',
          action: 'cancel',
          data: {
            services: buildSubmissionPayload().map((svc) => ({
              service: svc.service,
              required: svc.required,
              hasApiKey: svc.hasApiKey,
              selected_setup_lane: svc.selected_setup_lane
            })),
            cancelTime: new Date().toISOString(),
            tool_name: toolName,
            tool_call_id: toolCallId,
            workflowName: resolvedWorkflowName,
            sourceWorkflowName,
            generatedWorkflowName,
            agent_message_id: agentMessageId
          }
        });
      }
      tlog.event('cancel', 'done', { services: services.length });
    } catch (cancelError) {
      tlog.error('cancel failed', { error: cancelError?.message });
    } finally {
      setIsSubmitting(false);
    }
  };

  if (services.length === 0 || !currentService) {
    return (
      <div className={cardClasses}>
        <div className="flex items-start gap-3">
          <Plug className="mt-0.5 h-5 w-5 text-primary" aria-hidden="true" />
          <div className="space-y-1">
            <h2 className={headingClasses}>No integration setup required</h2>
            <p className={descriptionClasses}>
              The workflow did not declare integrations that need configuration.
            </p>
          </div>
        </div>
      </div>
    );
  }

  const currentValues = formValues[currentService.service] || {};
  const currentLane = selectedLanes[currentService.service] || currentService.preferredLane;
  const isLastStep = currentIndex === services.length - 1;
  const disableSubmit = isSubmitting || Object.keys(validateCurrentService()).length > 0;

  return (
    <div className="w-full" data-agent-message-id={agentMessageId || undefined}>
      <div className={cardClasses}>
        <div className="flex items-start gap-3">
          <Plug className="mt-1 h-5 w-5 text-primary" aria-hidden="true" />
          <div className="flex-1 space-y-1.5">
            <h2 className={headingClasses}>{heading}</h2>
            <p className={descriptionClasses}>{introMessage}</p>
            <p className="text-xs font-sans text-muted-foreground">
              Step {currentIndex + 1} of {services.length}
            </p>
          </div>
        </div>

        <div className="space-y-2">
          <p className="text-xs font-sans font-bold uppercase text-muted-foreground">Setup path</p>
          <div className="grid gap-2 sm:grid-cols-2">
            {currentService.lanes.map((lane) => {
              const meta = LANE_META[lane] || LANE_META.bring_your_own_key;
              const Icon = meta.icon;
              const active = currentLane === lane;
              return (
                <button
                  key={lane}
                  type="button"
                  onClick={() => setLane(currentService.service, lane)}
                  disabled={isSubmitting}
                  className={[
                    'rounded-lg border px-3 py-3 text-left transition-colors',
                    active
                      ? 'border-primary bg-[rgba(var(--color-primary-rgb),0.16)] text-foreground'
                      : 'border-border bg-card/70 text-muted-foreground hover:text-foreground hover:border-primary/60',
                    isSubmitting ? 'opacity-50 cursor-not-allowed' : ''
                  ].filter(Boolean).join(' ')}
                >
                  <span className="flex items-center gap-2 text-sm font-semibold">
                    <Icon className="h-4 w-4" aria-hidden="true" />
                    {meta.label}
                  </span>
                  <span className="mt-1 block text-xs leading-5">{meta.description}</span>
                </button>
              );
            })}
          </div>
          {currentService.integrationsUrl ? (
            <a
              href={currentService.integrationsUrl}
              className="inline-flex items-center gap-1 text-xs font-semibold text-primary hover:underline"
            >
              Open integrations
              <ExternalLink className="h-3 w-3" aria-hidden="true" />
            </a>
          ) : null}
        </div>

        <form onSubmit={handleSubmit} className="space-y-4 pt-1">
          {currentLane !== 'not_required' ? (
            <div className="space-y-3">
              {currentService.fields.map((field) => {
                const key = `${currentService.service}.${field.name}`;
                const currentError = errors[key];
                const visible = visibility?.[currentService.service]?.[field.name];
                const value = currentValues[field.name] || '';
                return (
                  <div key={field.name} className="space-y-2">
                    <div className="flex items-center justify-between gap-3">
                      <label className="text-xs font-sans font-bold uppercase text-muted-foreground">
                        {field.label}
                        {field.required ? (
                          <span className="ml-2 text-xs uppercase text-[rgba(255,255,255,0.65)]">Required</span>
                        ) : null}
                      </label>
                      <span className="text-xs font-sans text-muted-foreground">{currentService.service}</span>
                    </div>
                    {field.type === 'select' ? (
                      <select
                        value={value}
                        onChange={(event) => updateField(currentService.service, field.name, event.target.value)}
                        disabled={isSubmitting}
                        className={inputClasses(Boolean(currentError), isSubmitting)}
                      >
                        <option value="">Select {field.label}</option>
                        {field.options.map((option) => (
                          <option key={option} value={option}>{option}</option>
                        ))}
                      </select>
                    ) : (
                      <div className="relative">
                        <input
                          type={fieldInputType(field, visible)}
                          value={value}
                          onChange={(event) => updateField(currentService.service, field.name, event.target.value)}
                          placeholder={`Enter ${field.label}`}
                          disabled={isSubmitting}
                          className={`${inputClasses(Boolean(currentError), isSubmitting)} ${field.type === 'secret' ? 'pr-14' : ''}`}
                        />
                        {field.type === 'secret' ? (
                          <button
                            type="button"
                            onClick={() => toggleVisibility(currentService.service, field.name)}
                            disabled={isSubmitting}
                            className="absolute inset-y-0 right-3 flex items-center text-xs font-semibold uppercase text-[rgba(255,255,255,0.65)] hover:text-white transition-colors"
                          >
                            {visible ? 'Hide' : 'Show'}
                          </button>
                        ) : null}
                      </div>
                    )}
                    {currentError ? <p className={`${errorTextClasses} mt-1`}>{currentError}</p> : null}
                  </div>
                );
              })}
              <p className={assistiveTextClasses}>
                Secret fields are write-only. Public setup values may be saved as connector metadata.
              </p>
            </div>
          ) : (
            <p className={assistiveTextClasses}>
              This integration is marked optional for this build. No setup values will be saved.
            </p>
          )}

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
              {isSubmitting ? 'Saving...' : isLastStep ? 'Save Setup' : 'Next'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
};

AgentAPIKeysBundleInput.displayName = 'AgentAPIKeysBundleInput';
export default AgentAPIKeysBundleInput;
