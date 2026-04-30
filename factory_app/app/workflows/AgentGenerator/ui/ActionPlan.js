// ============================================================================
// FILE: ChatUI/src/workflows/AgentGenerator/components/ActionPlan.js
// REWRITE: Accordion-based Action Plan artifact (schema: { workflow:{...}, agent_message })
// PURPOSE: Present hierarchical workflow (modules -> agents -> tools) with robust, defensive parsing.
// ============================================================================

import { useState, useEffect, useRef } from 'react';
import { ChevronDown, ChevronRight, Layers, Plug, UserCheck, Bot, Sparkles, Zap, Activity, GitBranch, Clock, Settings, Database, MousePointerClick, Compass, MessageSquare, DollarSign, ArrowRightCircle } from 'lucide-react';
import { createToolsLogger } from '../../../../../chat-ui/src/platform/workflowSurfaceRuntime.js';

// Badge classes using --mz-* semantic tokens (v2 theme system)
const MZ_BADGE = {
  primary:   'inline-flex items-center gap-2 rounded-lg border-2 border-primary/45 bg-primary/10 text-primary px-4 py-2 text-xs font-bold uppercase tracking-wide',
  secondary: 'inline-flex items-center gap-2 rounded-lg border-2 border-secondary/45 bg-secondary/10 text-secondary-foreground px-4 py-2 text-xs font-bold uppercase tracking-wide',
  success:   'inline-flex items-center gap-2 rounded-lg border-2 border-success/45 bg-success/10 text-success px-4 py-2 text-xs font-bold uppercase tracking-wide',
  warning:   'inline-flex items-center gap-2 rounded-lg border-2 border-warning/45 bg-warning/10 text-warning-foreground px-4 py-2 text-xs font-bold uppercase tracking-wide',
  neutral:   'inline-flex items-center gap-2 rounded-lg border-2 border-border bg-muted text-muted-foreground px-4 py-2 text-xs font-bold uppercase tracking-wide',
  info:      'inline-flex items-center gap-2 rounded-lg border-2 border-primary/45 bg-primary/10 text-primary px-4 py-2 text-xs font-bold uppercase tracking-wide',
};

// Semantic model field mappings (3 orthogonal dimensions)
const INITIATED_BY = {
  user: { label: 'User', desc: 'Human explicitly starts workflow', color: 'cyan' },
  system: { label: 'System', desc: 'Platform automatically starts workflow', color: 'violet' },
  external_event: { label: 'External Event', desc: 'External service triggers workflow', color: 'amber' },
};

const TRIGGER_TYPE = {
  form_submit: { label: 'Form Submit', desc: 'User submits web form', color: 'emerald' },
  chat_start: { label: 'Chat-Based', desc: 'User initiates conversation', color: 'cyan' },
  chatstart: { label: 'Chat-Based', desc: 'User initiates conversation', color: 'cyan' },
  cron_schedule: { label: 'Scheduled', desc: 'Time-based trigger', color: 'violet' },
  webhook: { label: 'Webhook', desc: 'External HTTP POST', color: 'amber' },
  database_condition: { label: 'Database', desc: 'Database state trigger', color: 'blue' },
};

const PATTERN_META = {
  pipeline: { label: 'Pipeline', desc: 'Sequential handoffs across modules', color: 'violet' },
  hierarchical: { label: 'Hierarchical', desc: 'Lead agent delegates work to specialists', color: 'cyan' },
  star: { label: 'Star', desc: 'Central coordinator distributes and gathers work', color: 'emerald' },
  redundant: { label: 'Redundant', desc: 'Parallel agents produce overlapping outputs', color: 'amber' },
  feedbackloop: { label: 'Feedback Loop', desc: 'Iterative refinement until acceptance', color: 'blue' },
  escalation: { label: 'Escalation', desc: 'Progressively engages higher-tier experts', color: 'violet' },
  contextawarerouting: { label: 'Context-Aware Routing', desc: 'Dynamically routes tasks based on context variables', color: 'cyan' },
  organic: { label: 'Organic', desc: 'Free-form collaboration among agents', color: 'emerald' },
  triagewithtasks: { label: 'Triage with Tasks', desc: 'Intake triage followed by targeted execution tasks', color: 'amber' },
};

const LIFECYCLE_TRIGGER_META = {
  before_chat: { label: 'Before Chat', desc: 'Runs before the first agent turn', color: 'violet' },
  after_chat: { label: 'After Chat', desc: 'Runs after the workflow concludes', color: 'violet' },
  before_agent: { label: 'Before Agent', desc: 'Runs immediately before the target agent starts', color: 'cyan' },
  after_agent: { label: 'After Agent', desc: 'Runs immediately after the target agent finishes', color: 'emerald' },
};

const toTitle = (text) => text.replace(/_/g, ' ').replace(/\b\w/g, char => char.toUpperCase());

const SemanticChip = ({ value, mapping, icon: Icon = Sparkles, prefix }) => {
  const raw = String(value || '').trim();
  const normalized = raw.toLowerCase();
  const canonical = normalized.replace(/[^a-z0-9]/g, '');
  const meta = mapping[normalized] || mapping[canonical] || {
    label: raw ? toTitle(raw) : 'Unknown',
    desc: raw ? toTitle(raw) : 'Not specified',
    color: 'neutral',
  };
  const badgeClasses = {
    cyan:    MZ_BADGE.primary,
    emerald: MZ_BADGE.success,
    violet:  MZ_BADGE.secondary,
    amber:   MZ_BADGE.warning,
    blue:    MZ_BADGE.info,
    neutral: MZ_BADGE.neutral,
  };

  return (
    <span className={`${badgeClasses[meta.color] || MZ_BADGE.neutral}`} title={meta.desc}>
      <Icon className="h-3.5 w-3.5" /> {prefix}: {meta.label}
    </span>
  );
};

const ToolPill = ({ tool, idx, type = 'integration' }) => {
  const rawName = typeof tool === 'string' ? tool : tool?.name;
  const rawPurpose = typeof tool === 'string' ? null : tool?.purpose;
  const name = String(rawName || `Tool ${idx + 1}`);
  const purpose = rawPurpose && String(rawPurpose).trim() ? String(rawPurpose).trim() : null;
  const integration = tool && typeof tool === 'object' && typeof tool.integration === 'string'
    ? tool.integration
    : null;
  const trigger = tool && typeof tool === 'object' && typeof tool.trigger === 'string'
    ? tool.trigger
    : null;

  // Different colors for operations vs integrations
  const colorScheme = type === 'operation' 
    ? {
        border: 'border-primary/50',
        borderHover: 'hover:border-primary',
        bg: 'bg-primary/20',
        ring: 'ring-primary/50',
        iconColor: 'text-primary'
      }
    : {
        border: 'border-secondary/50',
        borderHover: 'hover:border-secondary-foreground',
        bg: 'bg-secondary/20',
        ring: 'ring-secondary/50',
        iconColor: 'text-secondary-foreground'
      };

  return (
    <div className={`group relative overflow-hidden rounded-lg border-2 ${colorScheme.border} bg-card p-3 md:p-4 transition-all ${colorScheme.borderHover} hover:bg-card hover:shadow-xl hover:shadow-secondary/20`}>
      <div className="flex items-start gap-2 md:gap-3">
        <div className={`rounded-lg ${colorScheme.bg} p-2 md:p-2.5 ring-2 ${colorScheme.ring}`}>
          <Plug className={`h-4 w-4 md:h-5 md:w-5 ${colorScheme.iconColor}`} />
        </div>
        <div className="flex-1 min-w-0">
          <p className="font-bold text-foreground text-sm break-words">{name}</p>
          {purpose && <p className="mt-1.5 text-xs text-muted-foreground break-words line-clamp-3">{purpose}</p>}
          {type === 'integration' && integration && (
            <p className="mt-1 text-[0.65rem] uppercase tracking-wider text-muted-foreground break-words">
              Integration: <span className="text-foreground break-all">{integration}</span>
            </p>
          )}
          {type === 'operation' && trigger && (
            <p className="mt-1 text-[0.65rem] uppercase tracking-wider text-muted-foreground break-words">
              Trigger: <span className="text-foreground break-all">{trigger}</span>
            </p>
          )}
        </div>
      </div>
    </div>
  );
};

const ToolSection = ({ title, icon: Icon = Zap, items, type = 'integration' }) => {
  if (!Array.isArray(items) || items.length === 0) return null;

  // Different colors for section headers
  const headerColor = type === 'operation' 
    ? 'text-primary' 
    : 'text-secondary-foreground';

  return (
    <div className="space-y-3">
      <div className={`flex items-center gap-2 text-xs font-black uppercase tracking-wider ${headerColor}`}>
        <Icon className="h-4 w-4" />
        {title}
      </div>
      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
        {items.map((tool, tIdx) => {
          const key = typeof tool === 'string'
            ? `${title}-${tool}-${tIdx}`
            : `${title}-${tool?.name || tIdx}-${tIdx}`;
          return <ToolPill key={key} tool={tool} idx={tIdx} type={type} />;
        })}
      </div>
    </div>
  );
};

const ComponentCard = ({ component, idx }) => {
  if (!component || typeof component !== 'object') return null;
  const label = String(component?.label || component?.tool || `component ${idx + 1}`);
  const moduleName = String(component?.module_name || 'Module');
  const agentName = String(component?.agent || 'Agent');
  const toolName = String(component?.tool || '').trim();
  const componentName = String(component?.component || '').trim();
  const display = String(component?.display || 'inline').trim();
  const interactionPattern = String(component?.interaction_pattern || component?.interactionPattern || '').trim();
  const summary = component?.summary ? String(component.summary) : '';

  // Color code by display type
  const isInline = display === 'inline';
  const borderColor = isInline ? 'border-primary/40' : 'border-secondary/40';
  const bgColor = isInline ? 'bg-primary/5' : 'bg-secondary/5';
  const iconBg = isInline ? 'bg-primary/20' : 'bg-secondary/20';
  const iconRing = isInline ? 'ring-primary/40' : 'ring-secondary/40';
  const iconColor = isInline ? 'text-primary' : 'text-secondary-foreground';

  return (
    <div className={`rounded-xl border-2 ${borderColor} ${bgColor} p-3 md:p-4`}>
      <div className="flex items-start justify-between gap-2 md:gap-3">
        <div className="flex items-center gap-2 min-w-0">
          <div className={`rounded-lg ${iconBg} p-1.5 md:p-2 ring-2 ${iconRing}`}>
            <MousePointerClick className={`h-3.5 w-3.5 md:h-4 md:w-4 ${iconColor}`} />
          </div>
          <span className="font-bold text-foreground text-sm break-words">{label}</span>
        </div>
        <span className="rounded-full bg-muted px-2 md:px-3 py-0.5 md:py-1 text-[10px] md:text-xs font-semibold uppercase tracking-wide text-foreground shrink-0">
          {toTitle(String(interactionPattern || display).replace(/_/g, ' '))}
        </span>
      </div>
      <div className="mt-2 md:mt-3 space-y-1 text-xs text-muted-foreground">
        <div className="break-words">
          <span className="font-semibold text-foreground">Module:</span> {moduleName}
        </div>
        <div className="break-words">
          <span className="font-semibold text-foreground">Agent:</span> {agentName}
        </div>
        {toolName && (
          <div className="break-words">
            <span className="font-semibold text-foreground">Tool:</span> {toolName}
          </div>
        )}
        {componentName && (
          <div className="break-words">
            <span className="font-semibold text-foreground">Component:</span> {componentName}
          </div>
        )}
      </div>
      {summary && (
        <p className="mt-2 md:mt-3 text-sm leading-relaxed text-foreground break-words line-clamp-4">{summary}</p>
      )}
    </div>
  );
};

const normalizeStringList = (value) => (
  Array.isArray(value)
    ? value
        .filter((item) => typeof item === 'string' && item.trim().length > 0)
        .map((item) => item.trim())
    : []
);

const mapToolStrings = (items, purpose) =>
  items.map((name) => ({ name, purpose }));

const normalizeLifecycleOperations = (value) => {
  const items = [];
  if (Array.isArray(value)) {
    items.push(...value);
  } else if (value && typeof value === 'object') {
    items.push(value);
  }

  return items
    .filter((item) => item && typeof item === 'object')
    .map((item, idx) => {
      const triggerCandidate = item.trigger ?? item.trigger_type ?? item.lifecycle_trigger;
      const trigger = String(triggerCandidate || '').trim().toLowerCase();
      const targetCandidate =
        item.target ?? item.agent ?? item.agent_name ?? item.source_agent ?? null;
      const descriptionSource =
        item.description ?? item.purpose ?? item.summary ?? '';

      return {
        name: String(item.name || `Lifecycle ${idx + 1}`),
        trigger,
        target:
          targetCandidate === undefined || targetCandidate === null || String(targetCandidate).trim() === ''
            ? null
            : String(targetCandidate).trim(),
        description:
          descriptionSource !== undefined && descriptionSource !== null
            ? String(descriptionSource)
            : '',
      };
    })
    .filter((op) => op.trigger);
};

const mergeLifecycleCollections = (...collections) => {
  const merged = [];
  const seen = new Set();

  collections.forEach((collection) => {
    if (!Array.isArray(collection)) return;
    collection.forEach((op) => {
      if (!op || typeof op !== 'object') return;
      const trigger = String(op.trigger || '').toLowerCase();
      const target = op.target ? String(op.target).toLowerCase() : '';
      const name = String(op.name || '').toLowerCase();
      const key = `${trigger}::${target}::${name}`;
      if (seen.has(key)) return;
      seen.add(key);
      merged.push(op);
    });
  });

  return merged;
};

// Lifecycle operation card component (reusable)
const LifecycleCard = ({ operation, idx, compact = false }) => {
  const triggerKey = String(operation.trigger || '').toLowerCase();

  return (
    <div className={`rounded-xl border-2 border-warning/50 bg-gradient-to-br from-background/80 to-card/80 ${compact ? 'p-3' : 'p-4'}`}>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <div className="rounded-lg bg-warning/20 p-2 ring-2 ring-warning/50">
            <Activity className="h-4 w-4 text-warning" />
          </div>
          <span className={`${compact ? 'text-sm' : 'text-base'} font-bold text-foreground`}>
            {operation.name || `Lifecycle ${idx + 1}`}
          </span>
        </div>
        <SemanticChip
          value={triggerKey}
          mapping={LIFECYCLE_TRIGGER_META}
          prefix=""
          icon={Clock}
        />
      </div>
      {operation.target && (
        <div className="mt-2 text-xs text-muted-foreground">
          Target Agent: <span className="font-semibold text-foreground">{operation.target}</span>
        </div>
      )}
      {operation.description && !compact && (
        <p className="mt-2 text-sm leading-relaxed text-foreground whitespace-pre-line">
          {operation.description}
        </p>
      )}
    </div>
  );
};

// Workflow-level lifecycle section (before_chat / after_chat)
const WorkflowLifecycleSection = ({ operations, type }) => {
  if (!Array.isArray(operations) || operations.length === 0) return null;

  const isSetup = type === 'before_chat';
  const title = isSetup ? 'Pre-Workflow Setup' : 'Post-Workflow Cleanup';
  const subtitle = isSetup
    ? 'Operations executed before the first agent runs'
    : 'Operations executed after all agents complete';

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3 rounded-lg bg-card px-6 py-4 border-l-4 border-warning">
        <Activity className="h-6 w-6 text-warning" />
        <span className="text-xl font-black uppercase tracking-wider text-foreground">{title}</span>
      </div>
      <div className="rounded-2xl border-2 border-warning/50 bg-background/60 p-6">
        <p className="mb-4 text-sm text-muted-foreground">{subtitle}</p>
        <div className="grid gap-3">
          {operations.map((op, idx) => (
            <LifecycleCard key={`${type}-${idx}`} operation={op} idx={idx} />
          ))}
        </div>
      </div>
    </div>
  );
};

// Minimal inference helper: derive trigger_type from a raw trigger string when missing.
const inferTriggerTypeFrom = (trigger) => {
  if (!trigger || typeof trigger !== 'string') return undefined;
  const s = trigger.toLowerCase();
  if (s.includes('webhook')) return 'webhook';
  if (s.includes('cron') || s.includes('schedule')) return 'cron_schedule';
  if (s.includes('form') || s.includes('submit')) return 'form_submit';
  if (s.includes('chat') || s.includes('conversation') || s.includes('user_initiated')) return 'chat_start';
  if (s.includes('db') || s.includes('database')) return 'database_condition';
  return undefined;
};

const AgentAccordionRow = ({ agent, index, isOpen, onToggle, agentLifecycleHooks = [] }) => {
  const agentName = String(agent?.agent_name || agent?.name || `Agent ${index + 1}`);
  const rawAgentTools = Array.isArray(agent?.agent_tools) ? agent.agent_tools : [];

  let integrationTools = [];
  let operationTools = [];

  if (rawAgentTools.length > 0) {
    integrationTools = [];
    operationTools = [];
    rawAgentTools.forEach((tool, tIdx) => {
      if (!tool || typeof tool !== 'object') return;
      const toolName = String(tool?.name || `Tool ${tIdx + 1}`);
      const purpose = tool?.purpose ? String(tool.purpose) : '';
      const integration = typeof tool?.integration === 'string' && tool.integration.trim().length
        ? tool.integration.trim()
        : null;
      const entry = {
        name: toolName,
        purpose,
        integration,
      };
      if (integration) {
        integrationTools.push(entry);
      } else {
        operationTools.push(entry);
      }
    });
  } else {
    const integrationNames = normalizeStringList(agent?.integrations);
    const operationNames = normalizeStringList(agent?.operations);
    integrationTools = integrationNames.map(name => ({ name, purpose: '', integration: name }));
    operationTools = mapToolStrings(operationNames, '');
  }

  const displayedToolCount = integrationTools.length + operationTools.length;
  const hasTools = displayedToolCount > 0;

  const lifecycleTools = Array.isArray(agent?.lifecycle_tools) ? agent.lifecycle_tools : [];
  const agentLifecycleTools = lifecycleTools
    .filter(tool => tool && typeof tool === 'object')
    .map((tool, tIdx) => ({
      name: String(tool?.name || `Lifecycle ${tIdx + 1}`),
      trigger: String(tool?.trigger || ''),
      target: agentName,
      description: tool?.purpose ? String(tool.purpose) : '',
      integration: typeof tool?.integration === 'string' && tool.integration.trim().length
        ? tool.integration.trim()
        : null,
    }));

  const combinedLifecycleHooks = [
    ...agentLifecycleHooks,
    ...agentLifecycleTools,
  ];
  const hasLifecycleHooks = combinedLifecycleHooks.length > 0;

  const systemHooks = Array.isArray(agent?.system_hooks)
    ? agent.system_hooks
        .filter(hook => hook && typeof hook === 'object')
        .map((hook, hIdx) => ({
          name: String(hook?.name || `Hook ${hIdx + 1}`),
          purpose: hook?.purpose ? String(hook.purpose) : '',
        }))
    : [];
  const hasSystemHooks = systemHooks.length > 0;

  // Determine interaction type from human_interaction field
  const humanInteraction = String(agent?.human_interaction || 'none').toLowerCase();
  const interactionType = ['none', 'context', 'approval', 'feedback', 'single'].includes(humanInteraction) 
    ? humanInteraction 
    : 'none';
  
  // Visual config per interaction type
  const interactionConfig = {
    none: {
      icon: Bot,
      bgClass: 'bg-primary/20 ring-2 ring-primary/50',
      iconColor: 'text-primary',
      badgeClass: 'bg-muted text-foreground',
      badgeText: 'AUTONOMOUS'
    },
    context: {
      icon: UserCheck,
      bgClass: 'bg-secondary/20 ring-2 ring-secondary/50',
      iconColor: 'text-secondary-foreground',
      badgeClass: 'bg-secondary text-foreground shadow-lg shadow-secondary/50',
      badgeText: 'REQUIRES CONTEXT'
    },
    approval: {
      icon: UserCheck,
      bgClass: 'bg-warning/20 ring-2 ring-warning/50',
      iconColor: 'text-warning',
      badgeClass: 'bg-warning text-foreground shadow-lg shadow-warning/50',
      badgeText: 'REQUIRES APPROVAL'
    },
    feedback: {
      icon: MessageSquare,
      bgClass: 'bg-secondary/20 ring-2 ring-secondary/50',
      iconColor: 'text-secondary-foreground',
      badgeClass: 'bg-secondary text-foreground shadow-lg shadow-secondary/40',
      badgeText: 'FEEDBACK LOOP'
    },
    single: {
      icon: UserCheck,
      bgClass: 'bg-primary/20 ring-2 ring-primary/50',
      iconColor: 'text-primary',
      badgeClass: 'bg-muted text-foreground',
      badgeText: 'SINGLE STEP'
    }
  };
  
  const config = interactionConfig[interactionType];
  const Icon = config.icon;
  
  return (
    <div className={`overflow-hidden rounded-xl border-2 transition-all ${isOpen ? 'border-primary bg-card shadow-xl shadow-primary/20' : 'border-border bg-card/50'}`}>
      <button
        onClick={onToggle}
        className="flex w-full items-center gap-2 md:gap-4 p-3 md:p-5 text-left transition-colors hover:bg-muted/50 border-l-4 border-transparent hover:border-primary"
      >
        <div className={`flex h-6 w-6 md:h-8 md:w-8 shrink-0 items-center justify-center rounded-full border-2 transition-all ${isOpen ? 'border-primary bg-primary/20 rotate-90' : 'border-primary bg-muted/50'}`}>
          <ChevronRight className={`h-3 w-3 md:h-4 md:w-4 transition-transform ${isOpen ? 'text-primary' : 'text-primary'}`} />
        </div>
        <div className="flex items-center gap-2 md:gap-3 min-w-0">
          <div className={`rounded-lg p-2 md:p-2.5 ${config.bgClass}`}>
            <Icon className={`h-4 w-4 md:h-5 md:w-5 ${config.iconColor}`} />
          </div>
          <span className="text-sm md:text-lg font-bold text-foreground break-words">
            {agentName}
          </span>
        </div>
        <div className="ml-auto flex items-center gap-2">
          {interactionType !== 'none' && (
            <span className={`rounded-lg px-3 py-1 text-xs font-bold ${config.badgeClass}`}>
              {config.badgeText}
            </span>
          )}
        </div>
      </button>
      {isOpen && (
        <div className="space-y-4 md:space-y-5 border-t-2 border-primary/30 bg-background p-4 md:p-6 ml-2 md:ml-4">
          <div className="rounded-lg bg-card/70 p-4 border-l-4 border-primary">
            <p className="text-sm leading-relaxed text-foreground">
              {String(agent?.description || 'No description provided.')}
            </p>
          </div>

          {hasLifecycleHooks && (
            <div className="space-y-3">
              <div className="flex items-center gap-2 text-xs font-black uppercase tracking-wider text-warning">
                <Activity className="h-4 w-4" />
                Lifecycle Hooks
              </div>
              <div className="grid gap-3">
                {combinedLifecycleHooks.map((hook, hIdx) => (
                  <LifecycleCard key={`agent-hook-${hIdx}`} operation={hook} idx={hIdx} compact />
                ))}
              </div>
            </div>
          )}

          {hasSystemHooks && (
            <div className="space-y-3">
              <div className="flex items-center gap-2 text-xs font-black uppercase tracking-wider text-secondary-foreground">
                <Settings className="h-4 w-4" />
                System Hooks
              </div>
              <div className="grid gap-3">
                {systemHooks.map((hook, hIdx) => (
                  <div key={`system-hook-${hIdx}`} className="rounded-lg border-2 border-border bg-card/40 p-4">
                    <p className="text-sm font-semibold text-foreground">{hook.name}</p>
                    {hook.purpose && (
                      <p className="mt-1 text-xs text-muted-foreground">{hook.purpose}</p>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          <div className="space-y-4">
            {integrationTools.length > 0 && (
              <ToolSection title="Integrations" icon={Plug} items={integrationTools} type="integration" />
            )}
            {!hasTools && !hasLifecycleHooks && (
              <div className="rounded-lg border-2 border-dashed border-border bg-card/30 p-6 text-center text-sm font-medium text-muted-foreground">
                No tools configured for this agent yet
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
};

const ModuleAccordion = ({ module, index, open, onToggle, lifecycleOperations = [] }) => {
  const agents = Array.isArray(module?.agents) ? module.agents : [];
  const [openAgents, setOpenAgents] = useState({});
  const toggleAgent = (i) => setOpenAgents(prev => ({ ...prev, [i]: !prev[i] }));

  // Helper to get lifecycle hooks for a specific agent
  const getAgentLifecycleHooks = (agentName) => {
    return lifecycleOperations.filter(op => {
      const trigger = String(op.trigger || '').toLowerCase();
      const target = String(op.target || '').trim();
      return (trigger === 'before_agent' || trigger === 'after_agent') && target === agentName;
    });
  };
  
  // Count approval gates
  const approvalCount = agents.filter(a => 
    String(a?.human_interaction || '').toLowerCase() === 'approval'
  ).length;
  
  // Count context collection points
  const contextCount = agents.filter(a => 
    String(a?.human_interaction || '').toLowerCase() === 'context'
  ).length;
  
  // Monetization logic
  const monetizationScope = String(module?.monetization_scope || 'free_trial').toLowerCase();
  const isPaid = monetizationScope === 'paid';
  const isFreeTrialEntry = module?.free_trial_entry === true;
  
  return (
    <div className={`overflow-hidden rounded-2xl border-2 transition-all ${open ? 'border-primary bg-card shadow-2xl shadow-primary/20' : 'border-border bg-card/80 shadow-sm'}`}>
      <button
        onClick={onToggle}
        className="flex w-full items-center gap-5 p-6 text-left transition-colors hover:bg-card"
      >
        <div className={`flex h-14 w-14 shrink-0 items-center justify-center rounded-xl ${open ? 'bg-gradient-to-br from-primary to-secondary shadow-lg shadow-primary/50' : 'bg-muted'}`}>
          {open ? (
            <ChevronDown className="h-6 w-6 text-foreground" />
          ) : (
            <ChevronRight className="h-6 w-6 text-muted-foreground" />
          )}
        </div>
        
        <div className="flex flex-col gap-1">
            <div className="flex items-center gap-4">
              <span className="text-xl font-black text-foreground">
                {String(module?.module_name || `Module ${index + 1}`)}
              </span>
              {/* Monetization Badge */}
              {isPaid ? (
                 <span className="flex items-center gap-1.5 rounded-lg bg-warning px-2 py-0.5 text-[10px] font-black uppercase tracking-wide text-foreground shadow-sm">
                    <DollarSign className="h-3 w-3" /> PAID
                 </span>
              ) : (
                 <span className="flex items-center gap-1.5 rounded-lg bg-success px-2 py-0.5 text-[10px] font-black uppercase tracking-wide text-foreground shadow-sm">
                    FREE TRIAL
                 </span>
              )}
            </div>
            {isFreeTrialEntry && (
                <span className="text-xs text-success font-medium flex items-center gap-1">
                    <ArrowRightCircle className="h-3 w-3" /> Entry Point
                </span>
            )}
        </div>

        <div className="ml-auto flex flex-col items-end gap-2 sm:gap-2">
          <div className="flex items-center gap-1.5 rounded-lg bg-card/80 px-3 py-1.5 text-[10px] sm:text-xs font-black uppercase tracking-wide text-foreground shadow-lg shadow-primary/30">
            <Activity className="h-3.5 w-3.5 text-primary" />
            <span className="text-base font-black text-foreground leading-none">{agents.length}</span>
            <span className="text-[10px] sm:text-xs text-foreground">
              {agents.length === 1 ? 'Agent' : 'Agents'}
            </span>
          </div>
          {contextCount > 0 && (
            <span className="flex items-center gap-1.5 rounded-lg bg-secondary px-3 py-1.5 text-[10px] sm:text-xs font-black uppercase tracking-wide text-foreground shadow-lg shadow-secondary/35">
              <UserCheck className="h-3.5 w-3.5 text-foreground/90" />
              {contextCount} Context Required
            </span>
          )}
          {approvalCount > 0 && (
            <span className="flex items-center gap-1.5 rounded-lg bg-warning px-3 py-1.5 text-[10px] sm:text-xs font-black uppercase tracking-wide text-foreground shadow-lg shadow-warning/35">
              <UserCheck className="h-3.5 w-3.5 text-foreground/90" />
              {approvalCount} Approval {approvalCount === 1 ? 'Gate' : 'Gates'}
            </span>
          )}
        </div>
      </button>
      {open && (
        <div className="space-y-4 md:space-y-6 border-t-4 border-primary/30 bg-background p-4 md:p-6">
          <div className="rounded-lg bg-card/50 p-5 border-l-4 border-primary">
            <p className="text-base leading-relaxed text-foreground">
              {String(module?.module_description || 'No description provided.')}
            </p>
          </div>
          <div className="space-y-4">
            <div className="flex items-center gap-2 text-sm font-black uppercase tracking-wider text-primary">
              <Bot className="h-5 w-5" />
              Agents
            </div>
            {agents.length > 0 ? (
              agents.map((agent, aIdx) => {
                const agentName = String(agent?.agent_name || agent?.name || '');
                const agentHooks = getAgentLifecycleHooks(agentName);
                return (
                  <AgentAccordionRow
                    key={aIdx}
                    agent={agent}
                    index={aIdx}
                    isOpen={!!openAgents[aIdx]}
                    onToggle={() => toggleAgent(aIdx)}
                    agentLifecycleHooks={agentHooks}
                  />
                );
              })
            ) : (
              <div className="rounded-lg border-2 border-dashed border-border bg-card/30 p-8 text-center text-sm font-medium text-muted-foreground">
                No agents defined in this module
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
};

// Mermaid preview with bold styling and multi-diagram support (flowchart + sequence)
const forceFlowchartOrientation = (diagram, orientation) => {
  if (typeof diagram !== 'string' || !diagram.trim().startsWith('flowchart')) return diagram;
  const normalizedOrientation = orientation?.toUpperCase?.() || 'TD';
  const directiveRegex = /^flowchart\s+(LR|RL|TD|TB|BT)/i;
  if (directiveRegex.test(diagram)) {
    return diagram.replace(directiveRegex, `flowchart ${normalizedOrientation}`);
  }
  return diagram.replace(/^flowchart/i, `flowchart ${normalizedOrientation}`);
};

const MermaidPreview = ({ chart, pendingMessage, pattern }) => {
  const ref = useRef(null);
  const [isFullscreen, setIsFullscreen] = useState(false);
  const [isMobile, setIsMobile] = useState(false);
  
  useEffect(() => {
    const checkMobile = () => {
      setIsMobile(window.innerWidth <= 640);
    };
    checkMobile();
    window.addEventListener('resize', checkMobile);
    return () => window.removeEventListener('resize', checkMobile);
  }, []);
  
  useEffect(() => {
    if (typeof window === 'undefined') return () => {};

    let disposed = false;
    const isMobileViewport = window.matchMedia ? window.matchMedia('(max-width: 640px)').matches : false;
    let mobileFlowchartNodeCount = 0;
    let mobileHorizontalFlow = false;

    const detectTheme = () => {
      const root = document.documentElement;
      const body = document.body;
      const rootStyle = root ? window.getComputedStyle(root) : null;
      const bodyStyle = body ? window.getComputedStyle(body) : null;

      const hasDarkClass = (root?.classList?.contains('dark') || body?.classList?.contains('dark')) ?? false;
      const dataTheme = (root?.dataset?.theme || root?.getAttribute('data-theme') || body?.dataset?.theme || body?.getAttribute('data-theme') || '').toLowerCase();
      const declaredScheme = (rootStyle?.colorScheme || bodyStyle?.colorScheme || '').toLowerCase();
      const mediaPrefersDark = window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)').matches : false;

      const isDark = Boolean(hasDarkClass || dataTheme.includes('dark') || declaredScheme.includes('dark') || mediaPrefersDark);

      const pickVar = (name, fallback) => {
        const val = rootStyle?.getPropertyValue(name) || bodyStyle?.getPropertyValue(name);
        return val && val.trim().length ? val.trim() : fallback;
      };

      const palette = {
        text: pickVar('--color-text-primary', isDark ? '#e2e8f0' : '#1f2937'),
        textSecondary: pickVar('--color-text-secondary', isDark ? '#cbd5f5' : '#4b5563'),
        surface: pickVar('--color-surface', isDark ? '#0f172a' : '#ffffff'),
        surfaceAlt: pickVar('--color-surface-alt', isDark ? '#131d33' : '#f9fafb'),
        note: pickVar('--color-surface-overlay', isDark ? '#1e293b' : '#f3f4f6'),
        border: pickVar('--color-border-subtle', isDark ? '#334155' : '#d1d5db'),
        primary: pickVar('--color-primary', '#3b82f6'),
        primaryLight: pickVar('--color-primary-light', '#60a5fa'),
        secondary: pickVar('--color-secondary', '#8b5cf6'),
        accent: pickVar('--color-accent', '#10b981')
      };

      return { isDark, palette };
    };

    const shouldOverrideFill = (fill, isDark) => {
      if (!fill) return true;
      const normalized = fill.trim().toLowerCase();
      const alwaysOverride = ['none', 'transparent', '#fff', '#ffffff', 'white'];
      if (alwaysOverride.includes(normalized)) return true;
      if (!isDark) return false;
      const softLights = ['#edf2ae', '#fefce8', '#f5f5f5', '#eaeaea', '#f9fafb'];
      if (softLights.includes(normalized)) return true;
      const hexMatch = normalized.match(/^#([0-9a-f]{6})$/i);
      if (hexMatch) {
        const hex = hexMatch[1];
        const r = parseInt(hex.slice(0, 2), 16);
        const g = parseInt(hex.slice(2, 4), 16);
        const b = parseInt(hex.slice(4, 6), 16);
        const brightness = (r * 299 + g * 587 + b * 114) / 1000;
        return brightness > 180; // treat bright colors as needing override in dark mode
      }
      return false;
    };

    const applyFill = (el, fill, stroke) => {
      if (!el) return;
      if (fill) {
        el.setAttribute('fill', fill);
        el.style.fill = fill;
      }
      if (stroke) {
        el.setAttribute('stroke', stroke);
        el.style.stroke = stroke;
      }
    };

    const renderDiagram = async () => {
      if (disposed) return;

      const hasChart = typeof chart === 'string' && chart.trim().length > 0;
      if (!hasChart) {
        if (!ref.current) return;
        const safeMessage = typeof pendingMessage === 'string' && pendingMessage.trim().length > 0
          ? pendingMessage.trim()
          : 'Approve the plan to generate a Mermaid sequence diagram.';
        ref.current.innerHTML = `<div class="flex h-full items-center justify-center p-6 text-center text-sm text-muted-foreground">${safeMessage}</div>`;
        return;
      }

      let normalized = chart.trim();
      const isSequence = normalized.startsWith('sequenceDiagram');
      if (isSequence) {
        // Guarantee a newline after the header
        normalized = normalized.replace(/^sequenceDiagram(\s*)/i, 'sequenceDiagram\n');
      }
      const normalizeLineEndings = (s) => s.replace(/\r\n/g, '\n').replace(/\r/g, '\n');
      const sanitizeSequence = (s) => {
        // Backend _fix_mermaid_syntax now handles all sanitization including legend stripping
        // Frontend only normalizes line endings for cross-platform compatibility
        return normalizeLineEndings(s);
      };
      if (isSequence) {
        normalized = sanitizeSequence(normalized);
      }
      const isFlowchart = normalized.startsWith('flowchart');

      if (!isSequence && isFlowchart) {
        const isLR = normalized.startsWith('flowchart LR');
        const nodeMatches = normalized.match(/\w+[[(]/g);
        const nodeCount = nodeMatches ? nodeMatches.length : 0;
        mobileFlowchartNodeCount = nodeCount;
        console.log('🎨 [MermaidPreview] Analyzing flowchart:', { isLR, nodeCount, mobileViewport: isMobileViewport });
        if (isMobileViewport) {
          normalized = forceFlowchartOrientation(normalized, 'LR');
          mobileHorizontalFlow = true;
        } else if (isLR && nodeCount > 5) {
          normalized = normalized.replace('flowchart LR', 'flowchart TD');
          console.log('🎨 [MermaidPreview] Converted LR to TD for better layout (nodeCount:', nodeCount, ')');
        }
      }

      if (!isSequence && !isFlowchart) {
        normalized = 'flowchart TD\n' + normalized;
      }

      const themeState = detectTheme();

      const safeRender = () => {
        try {
          const id = `diag-${Math.random().toString(36).slice(2)}`;
          const { isDark, palette } = themeState;

          const mermaidConfig = {
            startOnLoad: false,
            theme: isDark ? 'dark' : 'neutral',
            flowchart: {
              htmlLabels: true,
              curve: 'basis',
              padding: isMobileViewport ? 10 : 20,
              nodeSpacing: isMobileViewport ? 60 : 80,
              rankSpacing: isMobileViewport ? 60 : 80,
              useMaxWidth: true,
              wrappingWidth: isMobileViewport ? 150 : 200
            },
            sequence: {
              diagramMarginX: isMobileViewport ? 10 : 20,
              diagramMarginY: isMobileViewport ? 10 : 20,
              actorMargin: isMobileViewport ? 50 : 80,
              width: isMobileViewport ? 150 : 200,
              height: isMobileViewport ? 50 : 65,
              boxMargin: isMobileViewport ? 5 : 10,
              boxTextMargin: isMobileViewport ? 3 : 5,
              noteMargin: isMobileViewport ? 5 : 10,
              messageMargin: isMobileViewport ? 35 : 50,
              mirrorActors: !isMobileViewport,
              useMaxWidth: true,
              wrap: true,
              wrapPadding: isMobileViewport ? 5 : 10
            },
            themeVariables: {
              primaryColor: palette.primary,
              primaryTextColor: palette.text,
              primaryBorderColor: palette.primaryLight,
              lineColor: palette.border,
              secondaryColor: palette.secondary,
              tertiaryColor: palette.accent,
              noteBkgColor: palette.note,
              noteTextColor: palette.text,
              noteBorderColor: palette.border,
              activationBkgColor: isDark ? palette.secondary : palette.surfaceAlt,
              activationBorderColor: palette.primaryLight,
              sequenceNumberColor: palette.text,
              fontSize: '16px',
              fontFamily: 'ui-sans-serif, system-ui, sans-serif'
            }
          };

          window.mermaid.initialize(mermaidConfig);

          window.mermaid.render(id, normalized).then(({ svg }) => {
            if (!ref.current || disposed) return;
            ref.current.innerHTML = svg;
            const svgEl = ref.current.querySelector('svg');
            if (!svgEl) return;

            svgEl.style.maxWidth = '100%';
            svgEl.style.height = 'auto';

            // Mobile-optimized heights
            if (isMobileViewport) {
              svgEl.style.minHeight = isSequence ? '350px' : '300px';
            } else {
              svgEl.style.minHeight = isSequence ? '500px' : '400px';
            }

            // Mobile flowchart handling with better UX
            if (mobileHorizontalFlow && !isSequence) {
              const minWidth = Math.min(1200, Math.max(600, mobileFlowchartNodeCount * 110));
              svgEl.style.minWidth = `${minWidth}px`;
              ref.current.style.overflowX = 'auto';
              ref.current.style.overflowY = 'hidden';
              // Add scroll shadow indicator
              ref.current.style.background = 'linear-gradient(90deg, rgba(15, 23, 42, 0) 0%, rgba(15, 23, 42, 0.05) 5%, rgba(15, 23, 42, 0.05) 95%, rgba(15, 23, 42, 0) 100%)';
              // Smooth scrolling
              ref.current.style.scrollBehavior = 'smooth';
              ref.current.style.webkitOverflowScrolling = 'touch';
            } else {
              svgEl.style.minWidth = '';
              ref.current.style.overflowX = '';
              ref.current.style.overflowY = '';
              ref.current.style.background = '';
            }
            svgEl.style.colorScheme = isDark ? 'dark' : 'light';
            svgEl.style.background = palette.surfaceAlt;
            ref.current.style.background = palette.surface;

            console.log('🎨 [MermaidPreview] SVG Color Mode:', {
              dark: isDark,
              text: palette.text,
              note: palette.note,
              surface: palette.surface,
              surfaceAlt: palette.surfaceAlt
            });

            const textElements = svgEl.querySelectorAll('text, tspan, .messageText, .labelText, .actor');
            textElements.forEach((el) => applyFill(el, palette.text));

            const noteRects = svgEl.querySelectorAll('rect.note, .noteBox');
            noteRects.forEach((el) => applyFill(el, palette.note, palette.border));

            const actorRects = svgEl.querySelectorAll('rect.actor, .actor-box');
            actorRects.forEach((el) => {
              if (isDark || shouldOverrideFill(el.getAttribute('fill'), isDark)) {
                applyFill(el, palette.surface, palette.border);
              }
            });

            const otherRects = svgEl.querySelectorAll('rect:not(.note):not(.noteBox):not(.actor):not(.actor-box)');
            otherRects.forEach((el) => {
              if (shouldOverrideFill(el.getAttribute('fill'), isDark)) {
                applyFill(el, palette.surfaceAlt, palette.border);
              }
            });
          }).catch((err) => {
            console.error('🎨 [MermaidPreview] Render error:', err);
            if (ref.current && !disposed) {
              ref.current.innerHTML = '<div class="flex items-center justify-center h-full text-sm text-destructive">Diagram render error</div>';
            }
          });
        } catch (err) {
          console.error('🎨 [MermaidPreview] Render exception:', err);
          if (ref.current && !disposed) {
            ref.current.innerHTML = '<div class="flex items-center justify-center h-full text-sm text-destructive">Diagram render error</div>';
          }
        }
      };

      if (!window.mermaid) {
        const script = document.createElement('script');
        script.src = 'https://cdnjs.cloudflare.com/ajax/libs/mermaid/10.6.1/mermaid.min.js';
        script.onload = () => !disposed && safeRender();
        script.onerror = () => {
          console.error('🎨 [MermaidPreview] Failed to load Mermaid.js');
          if (ref.current && !disposed) {
            ref.current.innerHTML = '<div class="flex items-center justify-center h-full text-sm text-destructive">Failed to load diagram library</div>';
          }
        };
        document.head.appendChild(script);
      } else {
        safeRender();
      }
    };

    renderDiagram();

    const handleThemeChange = () => {
      if (!disposed) {
        renderDiagram();
      }
    };

    let mql;
    if (window.matchMedia) {
      mql = window.matchMedia('(prefers-color-scheme: dark)');
      mql.addEventListener('change', handleThemeChange);
    }

    let observer;
    if (window.MutationObserver) {
      observer = new MutationObserver(handleThemeChange);
      try {
        observer.observe(document.documentElement, { attributes: true, attributeFilter: ['class', 'data-theme'] });
        if (document.body) {
          observer.observe(document.body, { attributes: true, attributeFilter: ['class', 'data-theme'] });
        }
      } catch {}
    }

    return () => {
      disposed = true;
      if (mql) {
        try {
          mql.removeEventListener('change', handleThemeChange);
        } catch {
          mql.removeListener(handleThemeChange);
        }
      }
      observer?.disconnect();
    };
  }, [chart, pendingMessage]);
  
  const hasChart = typeof chart === 'string' && chart.trim().length > 0;
  const isLandscape = typeof window !== 'undefined' && window.innerWidth > window.innerHeight;
  
  if (isMobile && !isFullscreen) {
    return (
      <div className={`border-2 border-primary bg-card overflow-hidden rounded-2xl`}>
        <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-2 sm:gap-0 border-b-2 border-secondary/50 bg-gradient-to-r from-secondary/80 to-secondary px-4 md:px-6 py-3 md:py-4">
          <div className="flex items-center gap-2 md:gap-3 text-sm md:text-base font-black uppercase tracking-wider text-foreground">
            <GitBranch className="h-4 w-4 md:h-5 md:w-5" />
            Workflow Diagram
          </div>
          {pattern && (
            <SemanticChip value={pattern} mapping={PATTERN_META} prefix="Pattern" icon={GitBranch} />
          )}
        </div>
        <div className="flex flex-col items-center justify-center gap-4 bg-background p-8 min-h-[200px]">
          {hasChart ? (
            <>
              <GitBranch className="h-12 w-12 text-secondary-foreground" />
              <p className="text-sm text-muted-foreground text-center">
                View the full workflow diagram in landscape mode for the best experience
              </p>
              <button
                onClick={() => setIsFullscreen(true)}
                className="flex items-center gap-2 rounded-lg bg-gradient-to-r from-primary to-secondary px-6 py-3 text-sm font-bold text-foreground shadow-lg hover:shadow-xl transition-all"
              >
                <GitBranch className="h-4 w-4" />
                View Flow Diagram
              </button>
            </>
          ) : (
            <p className="text-sm text-muted-foreground text-center">
              {typeof pendingMessage === 'string' && pendingMessage.trim().length > 0
                ? pendingMessage.trim()
                : 'Approve the plan to generate a Mermaid sequence diagram.'}
            </p>
          )}
        </div>
      </div>
    );
  }
  
  if (isFullscreen) {
    return (
      <div className="fixed inset-0 z-50 bg-background flex flex-col">
        {/* Header with close button */}
        <div className="flex items-center justify-between gap-4 border-b-2 border-secondary/50 bg-gradient-to-r from-secondary/80 to-secondary px-4 py-3">
          <div className="flex items-center gap-2 text-sm font-black uppercase tracking-wider text-foreground">
            <GitBranch className="h-4 w-4" />
            Workflow Diagram
          </div>
          <button
            onClick={() => setIsFullscreen(false)}
            className="flex items-center gap-2 rounded-lg bg-muted hover:bg-muted px-3 py-2 text-sm font-bold text-foreground transition-colors"
          >
            <ChevronDown className="h-4 w-4" />
            Close
          </button>
        </div>
        
        {/* Landscape hint for portrait mode */}
        {!isLandscape && (
          <div className="flex items-center gap-3 bg-warning/20 border-b-2 border-warning/50 px-4 py-3">
            <div className="animate-pulse">
              <svg className="h-6 w-6 text-warning" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 18h.01M8 21h8a2 2 0 002-2V5a2 2 0 00-2-2H8a2 2 0 00-2 2v14a2 2 0 002 2z" />
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 9l3 3m0 0l3-3m-3 3V3" transform="rotate(90 12 12)" />
              </svg>
            </div>
            <p className="text-xs font-semibold text-warning-foreground">
              Rotate your device to landscape mode for better viewing
            </p>
          </div>
        )}
        
        {/* Diagram content */}
        <div className="flex-1 overflow-auto bg-background">
          <div ref={ref} className="min-h-full p-4" />
        </div>
      </div>
    );
  }
  
  return (
    <div className={`border-2 border-primary bg-card overflow-hidden rounded-2xl`}>
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-2 sm:gap-0 border-b-2 border-secondary/50 bg-gradient-to-r from-secondary/80 to-secondary px-4 md:px-6 py-3 md:py-4">
        <div className="flex items-center gap-2 md:gap-3 text-sm md:text-base font-black uppercase tracking-wider text-foreground">
          <GitBranch className="h-4 w-4 md:h-5 md:w-5" />
          Workflow Diagram
        </div>
        {pattern && (
          <SemanticChip value={pattern} mapping={PATTERN_META} prefix="Pattern" icon={GitBranch} />
        )}
      </div>
      <div ref={ref} className="min-h-[300px] md:min-h-[400px] overflow-auto bg-background p-4 md:p-8" />
    </div>
  );
};

// Data View - Connection status and context variables
const DataView = ({ workflow, contextVariableDefinitions }) => {
  const definitions = contextVariableDefinitions || {};
  const databaseCapability = workflow?.database_capability || {};
  const schemaProvided = Boolean(workflow?.database_schema);
  const [expandedCollections, setExpandedCollections] = useState({});
  const [carouselIndex, setCarouselIndex] = useState(0);
  const mobileCarouselRef = useRef(null);
  const schemaCollections = Array.isArray(workflow?.database_schema?.collections)
    ? workflow.database_schema.collections
    : [];
  const sortedCollections = schemaCollections.slice().sort((a, b) => {
    const left = String(a?.name || '').toLowerCase();
    const right = String(b?.name || '').toLowerCase();
    if (left < right) return -1;
    if (left > right) return 1;
    return 0;
  });
  const toggleCollection = (key) => {
    setExpandedCollections((prev) => ({ ...prev, [key]: !prev[key] }));
  };
  useEffect(() => {
    const node = mobileCarouselRef.current;
    if (!node) return undefined;
    const handleScroll = () => {
      const child = node.firstElementChild;
      if (!child) return;
      const cardWidth = child.getBoundingClientRect().width || 1;
      const idx = Math.round(node.scrollLeft / cardWidth);
      const bounded = Math.min(Math.max(idx, 0), Math.max(sortedCollections.length - 1, 0));
      if (bounded !== carouselIndex) {
        setCarouselIndex(bounded);
      }
    };
    node.addEventListener('scroll', handleScroll, { passive: true });
    return () => node.removeEventListener('scroll', handleScroll);
  }, [sortedCollections.length, carouselIndex]);

  useEffect(() => {
    setCarouselIndex(0);
  }, [sortedCollections.length]);
  
  // Group variables by source type (six-type taxonomy)
  const variablesByType = {
    config: [],           // Environment variables and static defaults
    data_reference: [],   // Read-only MongoDB queries
    data_entity: [],      // Workflow-owned writes
    computed: [],         // Deterministic derivations
    state: [],            // Mutable orchestration state
    external: []          // Third-party API fetches
  };
  
  Object.entries(definitions).forEach(([name, def]) => {
    const type = def?.source?.type || def?.type || 'computed';
    if (variablesByType[type]) {
      variablesByType[type].push({ name, ...def });
    }
  });
  
  // Check for database configuration
  const hasDatabaseVars = variablesByType.data_reference.length > 0 || 
                          variablesByType.data_entity.length > 0;
  const hasRuntimeVars = variablesByType.computed.length > 0 || 
                         variablesByType.state.length > 0;
  const hasSystemVars = variablesByType.config.length > 0;
  
  // Check context-aware status (would come from environment/config)
  const contextAwareEnabled = Boolean(databaseCapability.enabled || schemaProvided || hasDatabaseVars);
  
  // Extract unique database collections from data_reference and data_entity variables
  const databaseCollections = new Set();
  [...variablesByType.data_reference, ...variablesByType.data_entity].forEach(variable => {
    const collection = variable?.source?.collection || variable?.collection;
    if (collection && typeof collection === 'string') {
      databaseCollections.add(collection);
    }
  });

  const renderCollectionCard = (collection, idx, variant = 'grid') => {
    const cardKey = `${collection?.name || 'collection'}-${idx}`;
    const isExpanded = !!expandedCollections[cardKey];
    const fields = Array.isArray(collection?.fields) ? collection.fields : [];
    const previewFields = fields.slice(0, 3);
    const previewOverflow = fields.length - previewFields.length;
    const sampleKeys = Array.isArray(collection?.sample_doc_keys) ? collection.sample_doc_keys : [];
    const containerBase = 'rounded-2xl border border-primary/40 bg-background/60 p-4 shadow-[0_10px_30px_rgba(15,23,42,0.35)] transition-all duration-300';
    const variantClasses = variant === 'mobile'
      ? 'min-w-[82vw] snap-center mr-2'
      : '';

    return (
      <div key={cardKey} className={`${containerBase} ${variantClasses}`}>
        <button
          type="button"
          onClick={() => toggleCollection(cardKey)}
          className="flex w-full items-center justify-between gap-3 text-left"
          aria-expanded={isExpanded}
        >
          <div>
            <p className="text-base font-bold text-primary">{collection?.name || `Collection ${idx + 1}`}</p>
            <div className="mt-1 flex flex-wrap gap-2 text-[0.65rem] uppercase tracking-widest text-muted-foreground">
              <span>{fields.length} Fields</span>
                {collection?.is_app && (
                  <span className="rounded-full bg-secondary/20 px-2 py-0.5 text-secondary-foreground">
                  App
                </span>
              )}
              {collection?.has_sample_data && (
                <span className="rounded-full bg-emerald-500/20 px-2 py-0.5 text-emerald-200">
                  Sample Data
                </span>
              )}
            </div>
          </div>
          <div className={`rounded-full border border-primary/50 p-1 ${isExpanded ? 'rotate-180' : ''}`}>
            <ChevronDown className="h-4 w-4 text-primary" />
          </div>
        </button>
        {!isExpanded && previewFields.length > 0 && (
          <div className="mt-3 flex flex-wrap gap-1 text-xs">
            {previewFields.map((field, fieldIdx) => (
              <span
                key={`${cardKey}-preview-${field?.name || fieldIdx}`}
                className="rounded-full bg-card/80 px-2 py-1 text-foreground"
              >
                {field?.name || 'field'}
              </span>
            ))}
            {previewOverflow > 0 && (
              <span className="text-muted-foreground">+{previewOverflow} more</span>
            )}
          </div>
        )}
        <div className={`overflow-hidden transition-all duration-300 ${isExpanded ? 'max-h-[460px] opacity-100 mt-4' : 'max-h-0 opacity-0'}`}>
          {fields.length > 0 && (
            <div className="space-y-2">
              <div className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
                Field Definitions
              </div>
              <div className="max-h-64 overflow-y-auto space-y-1 pr-1">
                {fields.map((field, fieldIdx) => (
                  <div
                    key={`${cardKey}-field-${field?.name || fieldIdx}`}
                    className="flex items-center justify-between gap-2 rounded-lg border border-border/70 bg-background/80 px-2 py-1 text-xs"
                  >
                    <span className="font-mono text-foreground">{field?.name || 'field'}</span>
                    <span className="text-muted-foreground">{field?.type || 'unknown'}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
          {collection?.has_sample_data && sampleKeys.length > 0 && (
            <div className="mt-3">
              <div className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
                Sample Keys
              </div>
              <div className="mt-1 flex flex-wrap gap-1 text-xs text-muted-foreground">
                {sampleKeys.map((key, sampleIdx) => (
                  <span key={`${cardKey}-sample-${sampleIdx}`} className="rounded border border-border px-2 py-0.5">
                    {key}
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    );
  };
  
  return (
    <div className="space-y-6">
      {/* Connection Status Overview */}
      <div className="rounded-xl border-2 border-primary/50 bg-gradient-to-br from-background/80 to-card/80 p-6 min-h-[200px] flex flex-col">
        <div className="flex items-start gap-4 mb-6">
          <div className={`rounded-lg p-3 ring-2 ${
            contextAwareEnabled 
              ? 'bg-success/30 ring-success/50' 
              : 'bg-warning/30 ring-warning/50'
          }`}>
            <Database className={`h-6 w-6 ${contextAwareEnabled ? 'text-success' : 'text-warning'}`} />
          </div>
          <div className="flex-1">
            <div className="flex items-center gap-3 mb-2">
              <h3 className="text-xl font-black text-foreground">Connection Status</h3>
              <span className={`rounded-full px-3 py-1 text-xs font-bold uppercase tracking-wider ${
                contextAwareEnabled
                  ? 'bg-success/20 border-2 border-success/50 text-success'
                  : 'bg-warning/20 border-2 border-warning/50 text-warning'
              }`}>
                {contextAwareEnabled ? 'Connected' : 'Not Connected'}
              </span>
            </div>
            {!contextAwareEnabled && (
              <p className="text-sm text-muted-foreground mt-2">
                Configure your MongoDB URL in settings to enable context-aware features
              </p>
            )}
          </div>
        </div>
        
        {/* Database Schema Info */}
        {contextAwareEnabled && workflow?.database_schema && (
          <div className="mb-4">
            <div className="flex items-center gap-2 mb-3">
              <span className="text-xs font-black uppercase tracking-widest text-muted-foreground">
                Database: {databaseCapability.database_name || workflow.database_schema.database_name || 'Unknown'}
              </span>
              <span className="rounded-full bg-primary/20 border border-primary/50 px-2 py-0.5 text-xs font-bold text-primary">
                {databaseCapability.collections_reported || workflow.database_schema.total_collections || workflow.database_schema.collections?.length || 0} Collections
              </span>
            </div>
            
            {/* Collections List */}
            {sortedCollections.length > 0 && (
              <div className="space-y-4">
                <div className="flex items-center justify-between gap-2 flex-wrap">
                  <div className="text-xs font-black uppercase tracking-widest text-muted-foreground">
                    Collections ({sortedCollections.length})
                  </div>
                  {sortedCollections.length > 3 && (
                    <button
                      type="button"
                      onClick={() => setExpandedCollections({})}
                      className="text-[0.65rem] font-semibold uppercase tracking-widest text-primary hover:text-primary-foreground"
                    >
                      Collapse All
                    </button>
                  )}
                </div>

                {/* Mobile Carousel */}
                <div className="md:hidden">
                  <div
                    ref={mobileCarouselRef}
                    className="flex snap-x snap-mandatory gap-4 overflow-x-auto pb-4"
                  >
                    {sortedCollections.map((collection, idx) => renderCollectionCard(collection, idx, 'mobile'))}
                  </div>
                  {sortedCollections.length > 1 && (
                    <div className="flex justify-center gap-1">
                      {sortedCollections.map((_, dotIdx) => (
                        <span
                          key={`collections-dot-${dotIdx}`}
                          className={`h-1.5 rounded-full transition-all ${dotIdx === carouselIndex ? 'w-4 bg-primary' : 'w-2 bg-muted'}`}
                        />
                      ))}
                    </div>
                  )}
                </div>

                {/* Desktop Grid */}
                <div className="hidden md:grid gap-3 md:grid-cols-2">
                  {sortedCollections.map((collection, idx) => renderCollectionCard(collection, idx))}
                </div>
              </div>
            )}
          </div>
        )}
        
        {/* Database Variables Cards */}
        {contextAwareEnabled && hasDatabaseVars && (
          <div className="flex-1">
            <div className="text-xs font-black uppercase tracking-widest text-muted-foreground mb-3">
              Database Variables
            </div>
            <div className="space-y-2">
              {[...variablesByType.data_reference, ...variablesByType.data_entity].map((variable, idx) => (
                <div key={idx} className="rounded-lg border border-border bg-card/70 p-3">
                  <div className="flex items-start justify-between gap-3">
                    <div className="flex-1">
                      <div className="flex items-center gap-2 mb-1">
                        <span className="font-bold text-primary">{variable.name}</span>
                        <span className="rounded-full bg-primary/20 border border-primary/50 px-2 py-0.5 text-xs font-semibold text-primary">
                          {variable.source?.type === 'data_entity' ? 'Write' : 'Query'}
                        </span>
                      </div>
                      {variable.purpose && (
                        <p className="text-xs text-muted-foreground mb-2">{variable.purpose}</p>
                      )}
                      {variable.trigger_hint && (
                        <p className="text-xs text-muted-foreground">
                          <span className="text-muted-foreground">Source:</span> {variable.trigger_hint}
                        </p>
                      )}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
      
      {/* System Variables Section */}
      {hasSystemVars && (
        <div className="rounded-xl border-2 border-secondary/40 bg-card/50 p-6 min-h-[200px] flex flex-col">
          <div className="flex items-center gap-2 mb-4">
            <Settings className="h-5 w-5 text-secondary-foreground" />
            <span className="text-sm font-bold uppercase tracking-wider text-secondary-foreground">
              System Configuration
            </span>
            <span className="rounded-full bg-secondary px-2 py-0.5 text-xs font-bold text-foreground">
              {variablesByType.config.length}
            </span>
          </div>
          <p className="text-xs text-muted-foreground mb-4">
            Pre-configured values loaded from environment or workflow definition
          </p>
          <div className="grid gap-3 md:grid-cols-2 flex-1">
            {variablesByType.config.map((variable, idx) => (
              <div key={idx} className="rounded-lg border border-border bg-card/70 p-3 h-fit">
                <div className="flex items-start justify-between gap-2 mb-2">
                  <span className="font-bold text-foreground text-sm">{variable.name}</span>
                  <span className="rounded-full bg-success/20 border border-success/50 px-2 py-0.5 text-xs font-semibold text-success">
                    CONFIG
                  </span>
                </div>
                {variable.purpose && (
                  <p className="text-xs text-muted-foreground">{variable.purpose}</p>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
      
      {/* Runtime Variables Section */}
      {hasRuntimeVars && (
        <div className="rounded-xl border-2 border-accent/40 bg-card/50 p-6 min-h-[200px] flex flex-col">
          <div className="flex items-center gap-2 mb-4">
            <Activity className="h-5 w-5 text-warning" />
            <span className="text-sm font-bold uppercase tracking-wider text-warning">
              Runtime Variables
            </span>
            <span className="rounded-full bg-warning px-2 py-0.5 text-xs font-bold text-foreground">
              {variablesByType.computed.length + variablesByType.state.length}
            </span>
          </div>
          <p className="text-xs text-muted-foreground mb-4">
            Variables computed or updated during workflow execution based on agent outputs or user interactions
          </p>
          <div className="space-y-3 flex-1">
            {[...variablesByType.computed, ...variablesByType.state].map((variable, idx) => (
              <div key={idx} className="rounded-lg border border-border bg-card/70 p-3">
                <div className="flex items-start justify-between gap-3 mb-2">
                  <div className="flex items-center gap-2">
                    <span className="font-bold text-foreground text-sm">{variable.name}</span>
                    <span className="rounded-full bg-warning/20 border border-warning/50 px-2 py-0.5 text-xs font-semibold text-warning">
                      {variable.source?.type === 'state' ? 'State' : 'Computed'}
                    </span>
                  </div>
                </div>
                {variable.purpose && (
                  <p className="text-xs text-muted-foreground mb-2">{variable.purpose}</p>
                )}
                {variable.trigger_hint && (
                  <div className="rounded-md bg-background/50 border border-border p-2 mt-2">
                    <p className="text-xs text-muted-foreground">
                      <span className="font-semibold text-muted-foreground">Trigger:</span> {variable.trigger_hint}
                    </p>
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
      
      {/* Empty State */}
      {!hasDatabaseVars && !hasSystemVars && !hasRuntimeVars && (
        <div className="rounded-xl border-2 border-border bg-card/50 p-12 text-center">
          <Database className="h-12 w-12 text-muted-foreground mx-auto mb-4" />
          <p className="text-muted-foreground text-sm">No context variables defined for this workflow</p>
        </div>
      )}
    </div>
  );
};

const ActionPlan = ({ payload = {}, onResponse, ui_tool_id, eventId, workflowName, componentId = 'ActionPlan' }) => {
  // CRITICAL: All hooks MUST be called before any conditional returns (React rules of hooks)
  // Initialize state first
  const [pending, setPending] = useState(false);
  const [openModules, setOpenModules] = useState({ 0: true });
  const [activeTab, setActiveTab] = useState('workflow'); // Tab state: 'workflow' | 'data' | 'interactions' | 'diagram'
  const [activeWorkflowIndex, setActiveWorkflowIndex] = useState(0); // Pack mode: active child workflow tab

  const packWorkflows = Array.isArray(payload?.workflows)
    ? payload.workflows.filter((entry) => entry && typeof entry === 'object')
    : [];
  const hasChildWorkflows = packWorkflows.length > 1;

  useEffect(() => {
    try {
      const workflows = Array.isArray(payload?.workflows) ? payload.workflows : null;
      if (!workflows || workflows.length < 2) {
        setActiveWorkflowIndex(0);
        return;
      }

      const desiredRaw = payload?.active_workflow_name || payload?.activeWorkflowName;
      const desired = typeof desiredRaw === 'string' ? desiredRaw.trim() : '';
      if (!desired) return;

      const idx = workflows.findIndex((entry) => {
        if (!entry || typeof entry !== 'object') return false;
        const entryNameRaw =
          typeof entry.workflow_name === 'string'
            ? entry.workflow_name
            : (entry.workflow && typeof entry.workflow === 'object' && typeof entry.workflow.name === 'string'
                ? entry.workflow.name
                : '');
        return String(entryNameRaw || '').trim() === desired;
      });
      if (idx >= 0) {
        setActiveWorkflowIndex(idx);
        setActiveTab('workflow');
        setOpenModules({ 0: true });
      }
    } catch (e) {
      // Best-effort only; never block render for tab sync
    }
  }, [payload]);

  const clampedWorkflowIndex = hasChildWorkflows
    ? Math.max(0, Math.min(activeWorkflowIndex, packWorkflows.length - 1))
    : 0;
  const workflowPayload = hasChildWorkflows ? (packWorkflows[clampedWorkflowIndex] || payload) : payload;
  
  // CRITICAL: Early validation to prevent null reference errors on revision (AFTER hooks)
  const isValidPayload = payload && typeof payload === 'object';
  
  if (!isValidPayload) {
    console.error('🎨 [ActionPlan] Invalid payload received:', payload);
    return (
      <div className="min-h-screen space-y-8 rounded-2xl p-8 bg-background text-foreground">
        <div className="text-destructive">Error: Invalid workflow data received. Please refresh and try again.</div>
      </div>
    );
  }

  // CRITICAL: Early logging to debug render issues
  try {
    console.log('🎨 [ActionPlan] Component ENTRY - Starting render', {
      payloadKeys: Object.keys(payload),
      ui_tool_id,
      eventId
    });
  } catch (e) {
    console.error('🎨 [ActionPlan] Error in entry logging:', e);
  }
  
  // Resolve root according to the current UI payload contract:
  // { workflow, agent_message, agent_message_id, ... }
  const preferredWorkflow = workflowPayload?.workflow && typeof workflowPayload.workflow === 'object' && !Array.isArray(workflowPayload.workflow) ? workflowPayload.workflow : null;
  const workflow = preferredWorkflow || {};
  
  try {
    console.log('🎨 [ActionPlan] Resolved workflow:', {
      hasWorkflow: !!workflow,
      workflowType: typeof workflow,
      isArray: Array.isArray(workflow),
      workflowKeys: workflow && typeof workflow === 'object' && !Array.isArray(workflow) ? Object.keys(workflow) : 'not-an-object',
      name: workflow?.name,
      trigger: workflow?.trigger,
      triggerType: typeof workflow?.trigger,
      hasTechnicalBlueprint: !!workflow?.technical_blueprint,
      hasLifecycleOps: !!workflow?.lifecycle_operations
    });
  } catch (e) {
    console.error('🎨 [ActionPlan] Error logging resolved workflow:', e);
  }

  const safeWorkflow = (workflow && typeof workflow === 'object' && !Array.isArray(workflow)) ? workflow : {};
  const modules = Array.isArray(safeWorkflow?.modules) ? safeWorkflow.modules : [];

  const technicalBlueprintCandidates = [
    safeWorkflow?.technical_blueprint,
    workflowPayload?.technical_blueprint,
  ];
  const technicalBlueprint = technicalBlueprintCandidates.find(
    (candidate) => candidate && typeof candidate === 'object' && !Array.isArray(candidate),
  ) || null;

  console.log('🎨 [ActionPlan] TechnicalBlueprint resolved:', {
    found: !!technicalBlueprint,
    keys: technicalBlueprint ? Object.keys(technicalBlueprint) : 'null',
    before_chat_lifecycle: technicalBlueprint?.before_chat_lifecycle,
    after_chat_lifecycle: technicalBlueprint?.after_chat_lifecycle,
    global_context_variables_count: Array.isArray(technicalBlueprint?.global_context_variables) ? technicalBlueprint.global_context_variables.length : 0,
    ui_components_count: Array.isArray(technicalBlueprint?.ui_components) ? technicalBlueprint.ui_components.length : 0
  });

  const workflowLifecycleOperations = normalizeLifecycleOperations(safeWorkflow?.lifecycle_operations);
  const blueprintLifecycleOperations = normalizeLifecycleOperations(technicalBlueprint?.lifecycle_operations || null);
  const blueprintBeforeChat = normalizeLifecycleOperations(
    technicalBlueprint?.before_chat_lifecycle
      ? {
          ...technicalBlueprint.before_chat_lifecycle,
          trigger: technicalBlueprint.before_chat_lifecycle.trigger || 'before_chat',
        }
      : null,
  );
  const blueprintAfterChat = normalizeLifecycleOperations(
    technicalBlueprint?.after_chat_lifecycle
      ? {
          ...technicalBlueprint.after_chat_lifecycle,
          trigger: technicalBlueprint.after_chat_lifecycle.trigger || 'after_chat',
        }
      : null,
  );
  const lifecycleOperations = mergeLifecycleCollections(
    workflowLifecycleOperations,
    blueprintLifecycleOperations,
    blueprintBeforeChat,
    blueprintAfterChat
  );

  console.log('🎨 [ActionPlan] Lifecycle operations debug:', {
    workflowCount: workflowLifecycleOperations.length,
    blueprintCount: blueprintLifecycleOperations.length,
    beforeChatCount: blueprintBeforeChat.length,
    afterChatCount: blueprintAfterChat.length,
    mergedCount: lifecycleOperations.length,
    hasTechnicalBlueprint: !!technicalBlueprint,
    blueprintKeys: technicalBlueprint ? Object.keys(technicalBlueprint) : 'null'
  });

  // Separate lifecycle operations by context
  const chatLevelHooks = {
    before_chat: lifecycleOperations.filter(op => String(op.trigger || '').toLowerCase() === 'before_chat'),
    after_chat: lifecycleOperations.filter(op => String(op.trigger || '').toLowerCase() === 'after_chat'),
  };
  if (chatLevelHooks.before_chat.length === 0 && technicalBlueprint?.before_chat_lifecycle) {
    const fallbackBefore = normalizeLifecycleOperations({
      ...technicalBlueprint.before_chat_lifecycle,
      trigger: technicalBlueprint.before_chat_lifecycle.trigger || 'before_chat',
    });
    if (fallbackBefore.length > 0) {
      chatLevelHooks.before_chat = mergeLifecycleCollections(chatLevelHooks.before_chat, fallbackBefore);
    }
  }
  if (chatLevelHooks.after_chat.length === 0 && technicalBlueprint?.after_chat_lifecycle) {
    const fallbackAfter = normalizeLifecycleOperations({
      ...technicalBlueprint.after_chat_lifecycle,
      trigger: technicalBlueprint.after_chat_lifecycle.trigger || 'after_chat',
    });
    if (fallbackAfter.length > 0) {
      chatLevelHooks.after_chat = mergeLifecycleCollections(chatLevelHooks.after_chat, fallbackAfter);
    }
  }

  console.log('🎨 [ActionPlan] Final chat-level hooks:', {
    beforeChatCount: chatLevelHooks.before_chat.length,
    afterChatCount: chatLevelHooks.after_chat.length,
    beforeChat: chatLevelHooks.before_chat,
    afterChat: chatLevelHooks.after_chat
  });

  // Agent-level hooks will be distributed to individual agents within modules

  // agent_message intentionally ignored inside artifact to avoid duplicate display in chat; previously parsed here.
  // (If future logic needs it for analytics, reintroduce as const agentMessage = String(payload?.agent_message || '') and use.)

  // Derived counts (computed; not part of schema)
  const agentCount = modules.reduce((acc, m) => acc + (Array.isArray(m?.agents) ? m.agents.length : 0), 0);
  
  // Tool count: deduplicate integrations across all agents (same integration used multiple times = 1 tool)
  const uniqueToolNames = new Set();
  const uniqueIntegrations = new Set();
  modules.forEach(module => {
    if (!Array.isArray(module?.agents)) return;
    module.agents.forEach(agent => {
      const agentTools = Array.isArray(agent?.agent_tools) ? agent.agent_tools : [];
      if (agentTools.length > 0) {
        agentTools.forEach(tool => {
          if (!tool || typeof tool !== 'object') return;
          const toolName = tool?.name ? String(tool.name).trim() : '';
          if (toolName) uniqueToolNames.add(toolName);
          const integration = typeof tool?.integration === 'string' ? tool.integration.trim() : '';
          if (integration) uniqueIntegrations.add(integration);
        });
      } else {
        normalizeStringList(agent?.operations).forEach(name => {
          if (name) uniqueToolNames.add(name);
        });
        normalizeStringList(agent?.integrations).forEach(integration => {
          if (integration) uniqueIntegrations.add(integration);
        });
      }
    });
  });
  const toolCount = uniqueToolNames.size || uniqueIntegrations.size;
  const normalizeDiagram = (value) => (typeof value === 'string' ? value.trim() : '');
  const workflowDiagram = normalizeDiagram(safeWorkflow?.mermaid_flow);
  const mermaidChart = workflowDiagram;
  const mermaidMessage = 'Approve the plan to generate a Mermaid sequence diagram.';

  const uicomponents = Array.isArray(safeWorkflow?.ui_components)
    ? safeWorkflow.ui_components
    : Array.isArray(technicalBlueprint?.ui_components)
      ? technicalBlueprint.ui_components
      : [];

  const UIComponentItems = uicomponents.filter(item => item && typeof item === 'object');
  const UIComponentCount = UIComponentItems.length;

  // Logging / action integration
  const agentMessageId = payload?.agent_message_id || payload?.agentMessageId || workflowPayload?.agent_message_id || workflowPayload?.agentMessageId || null;
  const tlog = createToolsLogger({ tool: ui_tool_id || componentId, eventId, workflowName, agentMessageId });
  const toggleModule = (idx) => setOpenModules(prev => ({ ...prev, [idx]: !prev[idx] }));

  const emit = async ({ action, planAcceptance = false, agentContextOverrides = {} }) => {
    if (pending) return;
    setPending(true);
    try {
      tlog.event(action, 'start');
      const resp = {
        status: 'success',
        action,
        data: {
          action,
          workflow_name: String(safeWorkflow?.name || 'Generated Workflow'),
          trigger: String(safeWorkflow?.trigger || 'chatbot'),
          module_count: modules.length,
          agent_count: agentCount,
          tool_count: toolCount,
          ui_tool_id,
          eventId,
          workflowName,
          agent_message_id: agentMessageId,
          plan_acceptance: Boolean(planAcceptance),
        },
        plan_acceptance: Boolean(planAcceptance),
        agentContext: {
          action_completed: true,
          workflow_viewed: true,
          action_type: action,
          plan_acceptance: Boolean(planAcceptance),
          ...agentContextOverrides,
        },
      };
      await onResponse?.(resp);
      tlog.event(action, 'done', { ok: true });
    } catch (e) {
      const msg = e?.message || 'Unknown error';
      tlog.error('action_failed', { error: msg });
      onResponse?.({
        status: 'error',
        action,
        error: msg,
        plan_acceptance: Boolean(planAcceptance),
        agentContext: { action_completed: false, plan_acceptance: Boolean(planAcceptance) },
      });
    } finally {
      setPending(false);
    }
  };

  const approve = () => emit({ action: 'accept_workflow', planAcceptance: true });

  // Tab configuration
  const tabs = [
    { id: 'workflow', label: 'Workflow', icon: Compass },
    { id: 'data', label: 'Data', icon: Database },
    { id: 'interactions', label: 'Interactions', icon: MessageSquare },
    { id: 'diagram', label: 'Diagram', icon: GitBranch },
  ];
  return (
    <div className="min-h-screen space-y-4 md:space-y-8 rounded-2xl border-2 border-primary bg-card p-6 md:p-8 shadow-2xl shadow-primary/20" data-agent-message-id={agentMessageId || undefined}>
      {/* Header Section */}
        <header className="space-y-4 md:space-y-6 rounded-2xl border-2 border-primary bg-gradient-to-br from-background to-card p-4 md:p-8 shadow-2xl shadow-primary/30">
          <div className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
            <div className="flex items-center gap-3 text-sm font-black uppercase tracking-[0.3em] text-primary">
              <Sparkles className="h-5 w-5" />
              Workflow Blueprint
              {safeWorkflow?.human_in_loop && (
                <span className="ml-2 md:ml-4 flex items-center gap-1.5 rounded-full bg-warning/20 border border-warning/50 px-3 py-0.5 text-[10px] font-bold tracking-wide text-warning-foreground shadow-sm">
                  <UserCheck className="h-3 w-3" />
                  HUMAN IN LOOP
                </span>
              )}
            </div>
            <div className="flex flex-col items-start gap-2 text-left md:items-end md:text-right">
              <div className="flex flex-wrap items-center gap-2">
                <button
                  onClick={approve}
                  disabled={pending}
                  className="rounded-lg bg-primary px-6 py-3 text-xs md:text-sm font-bold uppercase tracking-wide text-primary-foreground transition-all hover:bg-primary/90 disabled:opacity-60 shadow-lg shadow-primary/30"
                >
                  Approve Plan
                </button>
              </div>
            </div>
          </div>
          
          <div className="space-y-5">
            <h1 className={`${fonts.heading} font-black text-xl sm:text-2xl md:text-3xl lg:text-4xl ${colors.text.primary} drop-shadow-lg break-words max-w-full leading-tight overflow-hidden`}>
          {String(safeWorkflow?.name || 'Generated Workflow')}
            </h1>
          </div>
        </header>

      {/* Pack Workflow Tabs (1 tab per child workflow) */}
      {hasChildWorkflows && (
        <div className="flex items-center gap-2 rounded-xl border-2 border-border bg-background p-2 overflow-x-auto">
          {packWorkflows.map((entry, idx) => {
            const isActive = idx === clampedWorkflowIndex;
            const rawName =
              typeof entry?.workflow_name === 'string'
                ? entry.workflow_name
                : (entry?.workflow && typeof entry.workflow === 'object' && typeof entry.workflow.name === 'string'
                    ? entry.workflow.name
                    : `Workflow ${idx + 1}`);
            const role = typeof entry?.role === 'string' && entry.role.trim() ? entry.role.trim() : null;
            return (
              <button
                key={`${String(rawName)}-${idx}`}
                onClick={() => {
                  setActiveWorkflowIndex(idx);
                  setActiveTab('workflow');
                  setOpenModules({ 0: true });
                }}
                className={`flex items-center gap-2 rounded-lg px-3 py-2 text-xs font-bold transition-all whitespace-nowrap ${
                  isActive
                    ? 'bg-gradient-to-r from-primary to-secondary text-foreground shadow-lg'
                    : 'text-muted-foreground hover:bg-card'
                }`}
                title={String(rawName)}
              >
                <span className="max-w-[240px] truncate">{String(rawName)}</span>
                {role && (
                  <span className="rounded-full bg-muted/70 px-2 py-0.5 text-[10px] font-extrabold uppercase tracking-wide text-foreground">
                    {role}
                  </span>
                )}
              </button>
            );
          })}
        </div>
      )}

      {/* Tab Navigation */}
      <div className="flex items-center gap-2 rounded-xl border-2 border-border bg-background p-2">
        {tabs.map(tab => {
          const Icon = tab.icon;
          const isActive = activeTab === tab.id;
          return (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`flex flex-1 flex-col sm:flex-row items-center justify-center gap-1 sm:gap-2 rounded-lg px-2 sm:px-4 py-2 sm:py-3 text-xs sm:text-sm font-bold transition-all ${
                isActive
                  ? 'bg-gradient-to-r from-primary to-secondary text-foreground shadow-lg'
                  : 'text-muted-foreground hover:bg-card hover:text-foreground'
              }`}
            >
              <Icon className="h-4 w-4" />
              <span className="text-[10px] sm:text-xs leading-tight text-center">{tab.label}</span>
            </button>
          );
        })}
      </div>

      {/* Tab Content */}
      {/* Data Tab - Context variables and database information */}
      {activeTab === 'data' && (
        <DataView
          workflow={safeWorkflow}
          contextVariableDefinitions={safeWorkflow?.context_variable_definitions || {}}
        />
      )}

      {/* Interactions Tab - UI Components */}
      {activeTab === 'interactions' && (
        <div className="space-y-6">
          {/* Initialization Process Section */}
          <div className="rounded-xl border-2 border-border bg-gradient-to-br from-background/80 to-card/80 overflow-hidden shadow-xl">
            <div className="flex items-center gap-3 bg-gradient-to-r from-primary/10 via-secondary/10 to-primary/10 px-6 py-5 border-b-2 border-border">
              <Zap className="h-6 w-6 text-primary animate-pulse" />
              <span className="text-xl font-black uppercase tracking-widest text-foreground">
                Workflow Initialization
              </span>
            </div>
            <div className="grid grid-cols-2 divide-x-2 divide-border/50">
              <div className="p-6 hover:bg-muted/30 transition-colors">
                <div className="text-xs font-black uppercase tracking-widest text-primary mb-3">Initiated By</div>
                <div className="text-2xl font-black text-primary mb-2">
                  {INITIATED_BY[String(safeWorkflow?.initiated_by || '').toLowerCase()]?.label || 'Unknown'}
                </div>
                <div className="text-sm text-muted-foreground leading-relaxed">
                  {INITIATED_BY[String(safeWorkflow?.initiated_by || '').toLowerCase()]?.desc || 'Not specified'}
                </div>
              </div>
              <div className="p-6 hover:bg-muted/30 transition-colors">
                <div className="text-xs font-black uppercase tracking-widest text-primary mb-3">Trigger Type</div>
                <div className="text-2xl font-black text-primary mb-2">
                  {TRIGGER_TYPE[String(safeWorkflow?.trigger_type || inferTriggerTypeFrom(safeWorkflow?.trigger) || '').toLowerCase().replace(/[^a-z0-9]/g, '')]?.label || toTitle(String(safeWorkflow?.trigger_type || safeWorkflow?.trigger || 'Not specified'))}
                </div>
                <div className="text-sm text-muted-foreground leading-relaxed">
                  {TRIGGER_TYPE[String(safeWorkflow?.trigger_type || inferTriggerTypeFrom(safeWorkflow?.trigger) || '').toLowerCase().replace(/[^a-z0-9]/g, '')]?.desc || 'Workflow activation condition'}
                </div>
              </div>
            </div>
          </div>

          {/* UI Components Section */}
          {UIComponentCount > 0 ? (
            <div className="space-y-4">
              <div className="flex items-center gap-3 rounded-lg bg-card px-6 py-4 border-l-4 border-primary">
                <MousePointerClick className="h-6 w-6 text-primary" />
                <span className="text-xl font-black uppercase tracking-wider text-foreground">UI Components</span>
              </div>
              <div className="grid gap-3 lg:grid-cols-2">
                {UIComponentItems.map((component, idx) => (
                  <ComponentCard key={`component-${idx}`} component={component} idx={idx} />
                ))}
              </div>
              
              {/* Legend */}
              <div className="flex items-center justify-center gap-6 rounded-lg bg-card/50 px-6 py-3 border border-border">
                <div className="flex items-center gap-2">
                  <div className="w-4 h-4 rounded border-2 border-primary/60 bg-primary/20"></div>
                  <span className="text-sm font-medium text-muted-foreground">Inline Display</span>
                </div>
                <div className="flex items-center gap-2">
                  <div className="w-4 h-4 rounded border-2 border-secondary/60 bg-secondary/20"></div>
                  <span className="text-sm font-medium text-muted-foreground">Artifact Display</span>
                </div>
              </div>
            </div>
          ) : (
            <div className="rounded-2xl border-2 border-dashed border-border bg-background/50 p-12 text-center">
              <MousePointerClick className="h-12 w-12 text-muted-foreground mx-auto mb-4" />
              <p className="text-base font-medium text-muted-foreground">No UI interactions defined for this workflow</p>
            </div>
          )}
        </div>
      )}

      {/* Workflow Tab - Lifecycle hooks + Module details */}
      {activeTab === 'workflow' && (
        <div className="space-y-8">
          {/* Workflow Description */}
          {safeWorkflow?.description && (
            <div className="rounded-lg bg-card/70 p-5 border-l-4 border-primary">
              <p className="text-base leading-relaxed text-foreground">{String(safeWorkflow.description)}</p>
            </div>
          )}
          
          {/* Setup Hooks (before_chat) */}
          <WorkflowLifecycleSection operations={chatLevelHooks.before_chat} type="before_chat" />

          {/* Modules Section */}
          <div className="space-y-6">
            <div className="flex items-center gap-3 rounded-lg bg-card px-6 py-4 border-l-4 border-primary">
              <Layers className="h-6 w-6 text-primary" />
              <span className="text-xl font-black uppercase tracking-wider text-foreground">Execution Modules</span>
            </div>
            
            {modules.length === 0 && (
              <div className="rounded-2xl border-2 border-dashed border-border bg-background/50 p-12 text-center">
                <p className="text-base font-medium text-muted-foreground">No modules defined. Please ensure the ActionPlan includes at least one module.</p>
              </div>
            )}
            
            <div className="space-y-5">
              {modules.map((module, idx) => (
                <ModuleAccordion
                  key={idx}
                  module={module}
                  index={idx}
                  open={!!openModules[idx]}
                  onToggle={() => toggleModule(idx)}
                  lifecycleOperations={lifecycleOperations}
                />
              ))}
            </div>
          </div>

          {/* Teardown Hooks (after_chat) */}
          <WorkflowLifecycleSection operations={chatLevelHooks.after_chat} type="after_chat" />
        </div>
      )}

      {/* Diagram Tab - Mermaid visualization */}
      {activeTab === 'diagram' && (
        <MermaidPreview chart={mermaidChart} pendingMessage={mermaidMessage} pattern={safeWorkflow?.pattern} />
      )}
    </div>
  );
};

ActionPlan.displayName = 'ActionPlan';
export default ActionPlan;


