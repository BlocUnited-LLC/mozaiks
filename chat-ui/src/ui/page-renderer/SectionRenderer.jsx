/**
 * SectionRenderer — renders a single AppPageSection using the PrimitiveRegistry.
 *
 * Responsibilities:
 *   - Resolve section.primitive → React component via PrimitiveRegistry
 *   - Adapt declarative schema config into live primitive props
 *   - Inject live data from usePageData when api_endpoint is present
 *   - Execute declarative page actions (navigate, event, workflow, submit, delete)
 *   - Render nested schema children for container primitives
 *   - Render section.title above the primitive when present
 *   - Render an Unknown fallback when the primitive type is not registered
 */

import { useCallback } from 'react';
import { getPrimitive, getPrimitiveSchema } from './PrimitiveRegistry.js';
import { getChildSections } from './schemaUtils.js';
import { emitAppEvent } from '../hooks/useAppEventBus.js';
import { cn } from '../lib/cn.js';
import { useWorkflowStart } from '../../hooks/useWorkflowStart.js';

function UnknownPrimitive({ type }) {
  return (
    <div className="rounded-lg border border-destructive/40 bg-destructive/10 p-4 text-sm text-destructive">
      Unknown primitive type: <code className="font-mono">{type}</code>
    </div>
  );
}

const GAP_VALUES = {
  sm: '2',
  md: '4',
  lg: '6',
};

function isRecord(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function titleize(value) {
  return String(value)
    .replace(/[_-]+/g, ' ')
    .replace(/\b\w/g, (match) => match.toUpperCase());
}

function resolvePath(source, path) {
  if (!path) return undefined;
  return String(path)
    .split('.')
    .filter(Boolean)
    .reduce((current, segment) => (current == null ? undefined : current[segment]), source);
}

function interpolateString(template, context) {
  if (typeof template !== 'string') return template;
  return template.replace(/\{([^}]+)\}/g, (_, token) => {
    const resolved = resolvePath(context, token.trim());
    return resolved === undefined || resolved === null ? '' : String(resolved);
  });
}

function interpolateValue(value, context) {
  if (typeof value === 'string') return interpolateString(value, context);
  if (Array.isArray(value)) return value.map((item) => interpolateValue(item, context));
  if (isRecord(value)) {
    return Object.fromEntries(
      Object.entries(value).map(([key, item]) => [key, interpolateValue(item, context)])
    );
  }
  return value;
}

function resolveActionType(action) {
  if (!isRecord(action)) return null;
  if (typeof action.action_type === 'string' && action.action_type.trim()) return action.action_type;
  if (typeof action.event_type === 'string' && action.event_type.trim()) return 'event';
  if (typeof action.workflow_id === 'string' && action.workflow_id.trim()) return 'workflow';
  if (typeof action.href === 'string' && action.href.trim()) return 'navigate';
  return null;
}

function normalizeColumns(columns) {
  if (!Array.isArray(columns)) return [];
  return columns
    .filter((column) => typeof column === 'string' || isRecord(column))
    .map((column) => {
      if (typeof column === 'string') {
        return {
          key: column,
          label: titleize(column),
          sortable: true,
        };
      }

      const key = column.key ?? column.field ?? column.name ?? column.id;
      return {
        ...column,
        key,
        label: column.label ?? titleize(key ?? 'value'),
      };
    })
    .filter((column) => typeof column.key === 'string' && column.key.trim());
}

function normalizeFormFields(fields) {
  if (!Array.isArray(fields)) return [];
  return fields
    .filter(isRecord)
    .map((field, index) => ({
      ...field,
      name: field.name ?? field.id ?? `field-${index + 1}`,
      type: field.type ?? field.field_type ?? 'text',
    }));
}

function buildActionContext(liveData, extraContext = {}) {
  const selectedRows = Array.isArray(extraContext.selectedRows) ? extraContext.selectedRows : [];
  const selectedRow = selectedRows[0] ?? null;
  const values = isRecord(extraContext.values) ? extraContext.values : {};

  return {
    ...(isRecord(liveData) ? liveData : {}),
    ...(isRecord(selectedRow) ? selectedRow : {}),
    row: selectedRow,
    selected_row: selectedRow,
    selected_rows: selectedRows,
    values,
    form: values,
    data: liveData ?? null,
    result: liveData ?? null,
  };
}

function materializeActions(actions, executeAction, defaultPrefix) {
  if (!Array.isArray(actions)) return [];

  return actions
    .filter(isRecord)
    .map((action, index) => {
      const id = typeof action.id === 'string' && action.id.trim()
        ? action.id
        : `${defaultPrefix}-action-${index + 1}`;
      const schemaAction = { ...action, id };
      return {
        ...schemaAction,
        onClick: () => {
          void executeAction(schemaAction);
        },
      };
    });
}

function buildEmptyAction(config, executeAction, defaultPrefix) {
  if (!isRecord(config)) return undefined;

  const canonicalAction = isRecord(config.action) ? config.action : null;
  const legacyAction = typeof config.action_label === 'string' && config.action_label.trim()
    ? {
        label: config.action_label,
        action_type: config.action_type,
        href: config.href ?? null,
        event_type: config.action_event ?? config.event_type ?? null,
        workflow_id: config.workflow_id ?? null,
        context_variables: isRecord(config.context_variables) ? config.context_variables : null,
        event_payload: config.action_payload ?? config.event_payload ?? null,
      }
    : null;

  const [action] = materializeActions(
    canonicalAction ? [canonicalAction] : legacyAction ? [legacyAction] : [],
    executeAction,
    defaultPrefix,
  );

  return action;
}

function buildButtonAction(config) {
  if (!isRecord(config)) return null;
  if (isRecord(config.action)) return config.action;

  if (!config.action_type && !config.event_type && !config.workflow_id && !config.href) {
    return null;
  }

  return {
    label: config.label ?? 'Action',
    action_type: config.action_type,
    href: config.href ?? null,
    event_type: config.event_type ?? null,
    workflow_id: config.workflow_id ?? null,
    context_variables: isRecord(config.context_variables) ? config.context_variables : null,
    event_payload: config.event_payload ?? null,
  };
}

function resolveTableData(config, liveData) {
  if (Array.isArray(config.data)) return config.data;
  if (typeof config.data_key === 'string' && config.data_key.trim()) {
    const resolved = resolvePath(liveData, config.data_key.trim());
    if (Array.isArray(resolved)) return resolved;
  }
  if (Array.isArray(liveData)) return liveData;
  if (isRecord(liveData)) {
    return liveData.items ?? liveData.results ?? liveData.rows ?? liveData.data ?? [];
  }
  return [];
}

function resolveStatValue(config, liveData, staticKey, dynamicKey, fallbackKey) {
  if (config[staticKey] !== undefined) return config[staticKey];
  if (typeof config[dynamicKey] === 'string') {
    const resolved = resolvePath(liveData, config[dynamicKey]);
    if (resolved !== undefined) return resolved;
  }
  if (isRecord(liveData) && liveData[fallbackKey] !== undefined) {
    return liveData[fallbackKey];
  }
  if (!isRecord(liveData) && !Array.isArray(liveData) && liveData !== undefined) {
    return liveData;
  }
  return undefined;
}

/**
 * @param {object} props
 * @param {AppPageSection}   props.section    - Section definition from AppPageSchema
 * @param {object}           props.pageData   - { data, loading, error } keyed by section.id
 * @param {(id?:string)=>void} props.onRefetch - Called when the section wants to reload data
 * @param {(path:string)=>void} [props.onNavigate]
 */
export function SectionRenderer({
  section,
  pageData,
  onRefetch,
  onNavigate,
  className,
  inheritedData = null,
  inheritedLoading = false,
  inheritedRefreshTargetId = null,
}) {
  const Primitive = getPrimitive(section.primitive);
  const { startWorkflow } = useWorkflowStart();

  if (!Primitive) {
    return <UnknownPrimitive type={section.primitive} />;
  }

  const config = section.config ?? {};

  // Dev-mode schema validation — warns on missing required fields or bad enum values.
  // Never throws; advisory only. Remove the env check to enable in production.
  if (process.env.NODE_ENV === 'development') {
    const schema = getPrimitiveSchema(section.primitive);
    if (schema) {
      const violations = [];
      const required = schema.required ?? [];
      for (const field of required) {
        if (config[field] === undefined || config[field] === null) {
          violations.push(`missing required field "${field}"`);
        }
      }
      const props = schema.properties ?? {};
      for (const [field, def] of Object.entries(props)) {
        const val = config[field];
        if (val === undefined || val === null) continue;
        if (def.enum && !def.enum.includes(val)) {
          violations.push(`field "${field}" value "${val}" not in allowed values: ${def.enum.join(', ')}`);
        }
        if (def.type === 'array' && !Array.isArray(val)) {
          violations.push(`field "${field}" must be an array`);
        }
        if (def.type === 'integer' && typeof val !== 'number') {
          violations.push(`field "${field}" must be a number`);
        }
      }
      if (violations.length) {
        console.warn(
          `[SectionRenderer] Config violations for section "${section.id}" (${section.primitive}):`,
          violations
        );
      }
    }
  }
  const liveState = pageData?.[section.id];
  const hasOwnBinding = liveState !== undefined;
  const effectiveData = hasOwnBinding ? liveState.data : inheritedData;
  const effectiveLoading = hasOwnBinding ? (liveState.loading ?? false) : inheritedLoading;
  const refreshTargetId = hasOwnBinding ? section.id : inheritedRefreshTargetId;
  const componentId = section.primitive === 'Modal' && typeof config.modal_id === 'string' && config.modal_id.trim()
    ? config.modal_id
    : section.id;

  const refetchCurrentSection = useCallback(() => {
    if (!onRefetch || !refreshTargetId) return Promise.resolve(null);
    return onRefetch(refreshTargetId);
  }, [onRefetch, refreshTargetId]);

  const executeAction = useCallback(async (action, extraContext = {}) => {
    if (!isRecord(action)) return null;

    const actionType = resolveActionType(action);
    const context = buildActionContext(effectiveData, extraContext);

    try {
      if (actionType === 'event' && action.event_type) {
        const payload = interpolateValue(action.payload ?? action.event_payload ?? {}, context);
        emitAppEvent(action.event_type, payload);
        return payload;
      }

      if (actionType === 'navigate' && action.href) {
        const target = interpolateString(action.href, context);
        if (!target) return null;
        if (onNavigate) {
          onNavigate(target);
        } else {
          window.location.assign(target);
        }
        return target;
      }

      if (actionType === 'workflow' && action.workflow_id) {
        const workflowId = interpolateString(action.workflow_id, context);
        if (!workflowId) return null;
        const workflowContext = isRecord(action.context_variables)
          ? interpolateValue(action.context_variables, context)
          : {};
        await startWorkflow(workflowId, workflowContext, {
          trigger_source: 'page',
          action_id: action.id ?? null,
        });
        return workflowId;
      }

      if ((actionType === 'submit' || actionType === 'delete') && action.href) {
        const target = interpolateString(action.href, context);
        if (!target) return null;

        const method = actionType === 'delete' ? 'DELETE' : 'POST';
        const bodySource = action.payload !== undefined ? action.payload : action.event_payload;
        const body = bodySource !== undefined
          ? interpolateValue(bodySource, context)
          : extraContext.values ?? extraContext.selectedRows ?? {};

        const request = {
          method,
          headers: { 'Content-Type': 'application/json' },
        };

        if (body !== undefined && body !== null) {
          request.body = JSON.stringify(body);
        }

        const response = await fetch(target, request);
        if (!response.ok) {
          throw new Error(`${response.status} ${response.statusText}`);
        }

        await onRefetch?.();
        return response;
      }

      return null;
    } catch (error) {
      console.error(`Failed to execute page action for section '${section.id}'`, error);
      return null;
    }
  }, [effectiveData, onNavigate, onRefetch, section.id, startWorkflow]);

  const nestedChildren = getChildSections(section).map((child) => (
    <SectionRenderer
      key={child.id}
      section={child}
      pageData={pageData}
      onRefetch={onRefetch}
      onNavigate={onNavigate}
      inheritedData={effectiveData}
      inheritedLoading={effectiveLoading}
      inheritedRefreshTargetId={refreshTargetId}
    />
  ));

  let primitiveProps;

  switch (section.primitive) {
    case 'PageHeader': {
      const headerActions = materializeActions(config.actions, executeAction, section.id);
      const actionLookup = new Map(headerActions.map((action) => [action.id ?? action.label, action]));
      primitiveProps = {
        id: componentId,
        title: config.title,
        subtitle: config.subtitle,
        actions: headerActions,
        title_font: config.title_font ?? config.titleFont ?? 'body',
        onAction: (actionId) => {
          const action = actionLookup.get(actionId);
          if (action) void executeAction(action, {});
        },
      };
      break;
    }
    case 'SummaryStrip':
      primitiveProps = {
        id: componentId,
        items: Array.isArray(config.items) ? config.items : [],
      };
      break;
    case 'InlineEmptyState': {
      const action = buildEmptyAction(config, executeAction, section.id);
      primitiveProps = {
        id: componentId,
        title: config.title,
        description: config.description ?? config.message,
        action,
      };
      break;
    }
    case 'LoadingState':
      primitiveProps = {
        id: componentId,
        label: config.label,
      };
      break;
    case 'ErrorState':
      primitiveProps = {
        id: componentId,
        title: config.title,
        message: config.message,
      };
      break;
    case 'Panel':
    case 'SurfaceCard':
      primitiveProps = {
        id: componentId,
        title: config.title,
        eyebrow: config.eyebrow,
        subtitle: config.subtitle,
        accent: config.accent,
        children: nestedChildren.length ? nestedChildren : undefined,
      };
      break;
    case 'StatusPill':
      primitiveProps = {
        id: componentId,
        label: config.label,
        tone: config.tone ?? 'default',
      };
      break;
    case 'Metric':
      primitiveProps = {
        id: componentId,
        label: config.label,
        value: config.value,
        detail: config.detail,
      };
      break;
    case 'SegmentedBar':
      primitiveProps = {
        id: componentId,
        segments: Array.isArray(config.segments) ? config.segments : [],
      };
      break;
    case 'ResourceTable':
    case 'DataTable': {
      const actions = materializeActions(config.toolbar_actions ?? config.actions, executeAction, section.id);
      const emptyAction = buildEmptyAction(config.empty, executeAction, `${section.id}-empty`);
      const actionLookup = new Map(actions.map((action) => [action.id, action]));
      if (emptyAction) {
        actionLookup.set(emptyAction.id, emptyAction);
      }

      primitiveProps = {
        id: componentId,
        columns: normalizeColumns(config.columns),
        data: resolveTableData(config, effectiveData),
        selection: config.selection ?? 'none',
        pagination: config.pagination ?? true,
        page_size: config.page_size ?? config.pageSize ?? 20,
        search: config.search ?? true,
        actions,
        onAction: (actionId, selectedRows) => {
          const action = actionLookup.get(actionId);
          if (action) {
            void executeAction(action, { selectedRows });
          }
        },
        empty: config.empty
          ? { ...config.empty, action: emptyAction }
          : emptyAction
            ? { action: emptyAction }
            : undefined,
        loading: effectiveLoading,
        onRefresh: refreshTargetId ? refetchCurrentSection : undefined,
        variant: section.primitive === 'ResourceTable' ? 'resource' : undefined,
      };
      break;
    }
    case 'Form': {
      const submitAction = isRecord(config.submit_action)
        ? config.submit_action
        : typeof config.submit_endpoint === 'string' && config.submit_endpoint.trim()
          ? {
              label: config.submit_label ?? 'Submit',
              action_type: 'submit',
              href: config.submit_endpoint,
              event_payload: config.submit_payload ?? null,
            }
          : null;
      const [cancelAction] = materializeActions(
        isRecord(config.cancel_action) ? [config.cancel_action] : [],
        executeAction,
        `${section.id}-cancel`,
      );

      primitiveProps = {
        id: componentId,
        fields: normalizeFormFields(config.fields),
        layout: config.layout ?? 'vertical',
        columns: config.columns ?? 2,
        submit_label: config.submit_label ?? 'Submit',
        onSubmit: submitAction
          ? (values) => executeAction(submitAction, { values })
          : undefined,
        onCancel: cancelAction?.onClick,
        cancel_label: config.cancel_label ?? 'Cancel',
        disabled: config.disabled ?? false,
      };
      break;
    }
    case 'Stat':
      primitiveProps = {
        id: componentId,
        label: config.label,
        value: resolveStatValue(config, effectiveData, 'value', 'value_key', 'value'),
        trend: resolveStatValue(config, effectiveData, 'trend', 'trend_key', 'trend'),
        trend_direction: config.trend_direction ?? 'up_good',
        format: config.format,
        icon: config.icon,
        color: config.color,
      };
      break;
    case 'Grid':
      primitiveProps = {
        columns: Number(config.columns ?? config.cols ?? 3),
        gap: GAP_VALUES[String(config.gap)] ?? String(config.gap ?? '4'),
        children: nestedChildren,
      };
      break;
    case 'Card':
      primitiveProps = {
        title: config.title,
        subtitle: config.subtitle,
        actions: materializeActions(config.actions, executeAction, section.id),
        children: nestedChildren,
      };
      break;
    case 'Button': {
      const action = buildButtonAction(config);
      primitiveProps = {
        label: config.label,
        variant: config.variant ?? 'primary',
        size: config.size ?? 'default',
        icon: config.icon,
        disabled: config.disabled ?? false,
        onClick: action
          ? () => {
              void executeAction(action);
            }
          : undefined,
      };
      break;
    }
    case 'Modal':
      primitiveProps = {
        id: componentId,
        title: config.title,
        description: config.description,
        size: config.size ?? 'medium',
        actions: materializeActions(config.actions, executeAction, section.id),
        children: nestedChildren,
        open: config.open,
      };
      break;
    case 'Alert':
      primitiveProps = {
        id: componentId,
        title: config.title,
        message: config.message,
        variant: config.variant ?? 'default',
        dismissible: config.dismissible ?? false,
      };
      break;
    case 'Badge':
      primitiveProps = {
        label: config.label,
        variant: config.variant ?? 'default',
      };
      break;
    case 'Skeleton':
      primitiveProps = {
        rows: config.rows ?? 3,
        height: config.height ?? 'h-4',
      };
      break;
    case 'Empty': {
      const action = buildEmptyAction(config, executeAction, section.id);
      primitiveProps = {
        title: config.title,
        message: config.message,
        action,
        icon: config.icon,
      };
      break;
    }
    case 'Timeline':
      primitiveProps = {
        id: componentId,
        title: config.title,
        items: Array.isArray(config.items) ? config.items : [],
      };
      break;
    case 'CodeBlock':
      primitiveProps = {
        id: componentId,
        title: config.title,
        code: config.code ?? '',
        language: config.language,
        filename: config.filename,
      };
      break;
    case 'ProgressTracker':
      primitiveProps = {
        id: componentId,
        title: config.title,
        stages: Array.isArray(config.stages) ? config.stages : [],
      };
      break;
    case 'AlertBanner': {
      const bannerActions = materializeActions(config.actions, executeAction, section.id);
      primitiveProps = {
        id: componentId,
        message: config.message,
        title: config.title,
        variant: config.variant ?? 'info',
        dismissible: config.dismissible ?? false,
        actions: bannerActions,
        onAction: (actionId, selectedRows) => {
          const action = bannerActions.find((a) => (a.id ?? a.label) === actionId);
          if (action) void executeAction(action, { selectedRows });
        },
      };
      break;
    }
    case 'ActionButton': {
      const buttonActions = materializeActions(config.actions, executeAction, section.id);
      const actionLookup = new Map(buttonActions.map((a) => [a.id ?? a.label, a]));
      primitiveProps = {
        id: componentId,
        title: config.title,
        layout: config.layout ?? 'row',
        actions: buttonActions,
        onAction: (actionId) => {
          const action = actionLookup.get(actionId);
          if (action) void executeAction(action, {});
        },
      };
      break;
    }
    case 'FileList':
      primitiveProps = {
        id: componentId,
        title: config.title,
        files: Array.isArray(config.files) ? config.files : [],
        onAction: (actionId, selectedRows) => void executeAction({ id: actionId }, { selectedRows }),
      };
      break;
    default:
      primitiveProps = { ...config, id: componentId };
      break;
  }

  return (
    <div className={cn('w-full', className)}>
      {section.title && (
        <h2 className="mb-3 text-sm font-black uppercase tracking-widest text-muted-foreground">
          {section.title}
        </h2>
      )}
      <Primitive {...primitiveProps} />
    </div>
  );
}
