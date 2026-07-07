/**
 * PrimitiveSchemas — JSON Schema definitions for every registered AppPageSection primitive.
 *
 * These schemas are the single source of truth for:
 *   1. Runtime dev-mode validation in SectionRenderer (warns on invalid config)
 *   2. Agent guidance injected by ui_primitives.py into AppPlanAgent / AppSchemaAgent
 *   3. companion primitive_schemas.json (generated from this file for Python consumption)
 *
 * Ownership rule: this file owns config field definitions. PrimitiveRegistry.js owns
 * component bindings. Neither owns layout or spacing — the page container and PageFrame
 * own those.
 *
 * When adding a new primitive:
 *   1. Add its schema here
 *   2. Add it to PrimitiveRegistry.js (Component + schema reference)
 *   3. Regenerate primitive_schemas.json (run: node scripts/export-primitive-schemas.js)
 */

/** Shared definitions referenced by multiple primitive schemas. */
export const SHARED_DEFINITIONS = {
  action: {
    type: 'object',
    required: ['label', 'action_type'],
    properties: {
      id:                 { type: 'string' },
      label:              { type: 'string' },
      variant:            { type: 'string', enum: ['primary', 'secondary', 'ghost', 'destructive'] },
      action_type:        { type: 'string', enum: ['navigate', 'event', 'workflow', 'submit', 'delete'] },
      href:               { type: 'string', description: 'Route or API/module endpoint. Required for navigate, submit, and delete actions.' },
      event_type:         { type: 'string' },
      workflow_id:        { type: 'string' },
      context_variables:  { type: 'object' },
      payload:            { type: 'object' },
      requires_selection: { type: 'boolean', default: false },
      closes_modal:       { type: 'boolean', default: true },
    },
  },
  columnObject: {
    type: 'object',
    required: ['key'],
    properties: {
      key:      { type: 'string' },
      label:    { type: 'string' },
      type:     { type: 'string', enum: ['text', 'number', 'date', 'badge', 'status', 'actions'] },
      sortable: { type: 'boolean' },
      width:    { type: 'string' },
    },
  },
  formField: {
    type: 'object',
    required: ['name', 'label', 'type'],
    properties: {
      name:          { type: 'string' },
      label:         { type: 'string' },
      type:          { type: 'string', enum: ['text', 'email', 'password', 'number', 'textarea', 'select', 'checkbox'] },
      required:      { type: 'boolean' },
      placeholder:   { type: 'string' },
      default_value: {},
      options: {
        type: 'array',
        items: { type: 'object', required: ['value', 'label'], properties: { value: {}, label: { type: 'string' } } },
      },
    },
  },
  childSection: {
    type: 'object',
    required: ['primitive'],
    properties: {
      id:        { type: 'string' },
      primitive: { type: 'string' },
      title:     { type: 'string' },
      config:    { type: 'object' },
    },
  },
  timelineItem: {
    type: 'object',
    required: ['label', 'status'],
    properties: {
      label:       { type: 'string' },
      status:      { type: 'string', enum: ['done', 'active', 'pending', 'error'] },
      description: { type: 'string' },
      timestamp:   { type: 'string' },
    },
  },
  stageItem: {
    type: 'object',
    required: ['label', 'status'],
    properties: {
      label:       { type: 'string' },
      status:      { type: 'string', enum: ['done', 'active', 'pending', 'error'] },
      description: { type: 'string' },
    },
  },
  fileItem: {
    type: 'object',
    required: ['name'],
    properties: {
      name:   { type: 'string' },
      type:   { type: 'string' },
      size:   { type: 'string' },
      url:    { type: 'string' },
      status: { type: 'string', enum: ['ready', 'generating', 'error'] },
    },
  },
  tableFilter: {
    type: 'object',
    required: ['label', 'value'],
    properties: {
      label:        { type: 'string' },
      value:        { type: 'string' },
      field:        { type: 'string' },
      match_values: { type: 'array', items: { type: 'string' } },
    },
  },
  summaryItem: {
    type: 'object',
    required: ['label', 'value'],
    properties: {
      id:     { type: 'string' },
      label:  { type: 'string' },
      value:  {},
      detail: { type: 'string' },
    },
  },
  segment: {
    type: 'object',
    required: ['label', 'value'],
    properties: {
      id:    { type: 'string' },
      label: { type: 'string' },
      value: { type: 'number' },
      tone:  { type: 'string', enum: ['default', 'primary', 'success', 'warning', 'destructive'] },
    },
  },
  pricingCatalogGroup: {
    type: 'object',
    required: ['group_id', 'label'],
    properties: {
      group_id:          { type: 'string' },
      label:             { type: 'string' },
      description:       { type: 'string' },
      kind:              { type: 'string', enum: ['subscription', 'service', 'add_on', 'mixed'] },
      plan_ids:          { type: 'array', items: { type: 'string' } },
      capability_groups: { type: 'array', items: { type: 'string' } },
      add_on_ids:        { type: 'array', items: { type: 'string' } },
    },
  },
  pricingCatalogPlan: {
    type: 'object',
    required: ['plan_id', 'label'],
    properties: {
      plan_id:       { type: 'string' },
      label:         { type: 'string' },
      description:   { type: 'string' },
      price_display: { type: 'string' },
      cta_label:     { type: 'string' },
      highlights:    { type: 'array', items: { type: 'string' } },
      managed_ai:    { type: 'object' },
      usage_limits:  { type: 'array', items: { type: 'object' } },
      pricing:       { type: 'object' },
      is_default:    { type: 'boolean' },
    },
  },
  pricingCatalogAddOn: {
    type: 'object',
    required: ['label'],
    properties: {
      id:            { type: 'string' },
      add_on_id:     { type: 'string' },
      label:         { type: 'string' },
      description:   { type: 'string' },
      price_display: { type: 'string' },
      price:         { type: 'object' },
      cta_label:     { type: 'string' },
      highlights:    { type: 'array', items: { type: 'string' } },
    },
  },
};

/** Per-primitive config schemas. Keys match PrimitiveRegistry names exactly. */
export const PRIMITIVE_SCHEMAS = {
  PageHeader: {
    type: 'object',
    required: ['title'],
    properties: {
      title:      { type: 'string' },
      subtitle:   { type: 'string' },
      actions:    { type: 'array', items: SHARED_DEFINITIONS.action },
      title_font: { type: 'string', enum: ['body', 'heading'] },
    },
  },

  ResourceTable: {
    type: 'object',
    required: ['columns'],
    properties: {
      columns: {
        type: 'array',
        minItems: 1,
        items: { oneOf: [{ type: 'string' }, SHARED_DEFINITIONS.columnObject] },
      },
      api_endpoint:       { type: 'string' },
      data:               { type: 'array' },
      data_key:           { type: 'string' },
      selection:          { type: 'string', enum: ['none', 'single', 'multi'], default: 'none' },
      search:             { type: 'boolean', default: true },
      search_placeholder: { type: 'string' },
      filters:            { type: 'array', items: SHARED_DEFINITIONS.tableFilter },
      default_sort:       { type: 'string' },
      actions:            { type: 'array', items: SHARED_DEFINITIONS.action },
      empty: {
        type: 'object',
        properties: {
          title:         { type: 'string' },
          message:       { type: 'string' },
          error_title:   { type: 'string' },
          error_message: { type: 'string' },
          retry_label:   { type: 'string' },
          action:        SHARED_DEFINITIONS.action,
        },
      },
    },
  },

  SummaryStrip: {
    type: 'object',
    required: ['items'],
    properties: {
      items: { type: 'array', minItems: 1, maxItems: 4, items: SHARED_DEFINITIONS.summaryItem },
    },
  },

  PricingCatalog: {
    type: 'object',
    properties: {
      title:               { type: 'string' },
      subtitle:            { type: 'string' },
      api_endpoint:        { type: 'string', description: 'Optional endpoint returning plans, pricing_catalog, and add-on data.' },
      plans_key:           { type: 'string', description: 'Path to plan array in endpoint response.' },
      groups_key:          { type: 'string', description: 'Path to pricing group array in endpoint response.' },
      add_ons_key:         { type: 'string', description: 'Path to add-on array in endpoint response.' },
      default_group_id:    { type: 'string' },
      default_group_key:   { type: 'string', description: 'Path to default group id in endpoint response.' },
      current_plan_key:    { type: 'string', description: 'Path to current plan id in endpoint response.' },
      highlighted_plan_id: { type: 'string' },
      plan_action_label:   { type: 'string' },
      add_on_action_label: { type: 'string' },
      plans:               { type: 'array', items: SHARED_DEFINITIONS.pricingCatalogPlan },
      groups:              { type: 'array', items: SHARED_DEFINITIONS.pricingCatalogGroup },
      add_ons:             { type: 'array', items: SHARED_DEFINITIONS.pricingCatalogAddOn },
      plan_action:         SHARED_DEFINITIONS.action,
      add_on_action:       SHARED_DEFINITIONS.action,
    },
  },

  InlineEmptyState: {
    type: 'object',
    required: ['title'],
    properties: {
      title:       { type: 'string' },
      description: { type: 'string' },
      action:      SHARED_DEFINITIONS.action,
    },
  },

  LoadingState: {
    type: 'object',
    properties: {
      label: { type: 'string' },
    },
  },

  ErrorState: {
    type: 'object',
    required: ['message'],
    properties: {
      title:   { type: 'string' },
      message: { type: 'string' },
    },
  },

  Panel: {
    type: 'object',
    properties: {
      title:    { type: 'string' },
      eyebrow:  { type: 'string' },
      subtitle: { type: 'string' },
    },
  },

  SurfaceCard: {
    type: 'object',
    properties: {
      title:    { type: 'string' },
      eyebrow:  { type: 'string' },
      subtitle: { type: 'string' },
      accent:   { type: 'boolean' },
    },
  },

  StatusPill: {
    type: 'object',
    required: ['label'],
    properties: {
      label: { type: 'string' },
      tone:  { type: 'string', enum: ['default', 'primary', 'success', 'warning', 'destructive'] },
    },
  },

  Metric: {
    type: 'object',
    required: ['label'],
    properties: {
      label:         { type: 'string' },
      value:         {},
      value_key:     { type: 'string' },
      detail:        { type: 'string' },
      detail_key:    { type: 'string' },
      trend:         {},
      trend_key:     { type: 'string' },
      format:        { type: 'string', enum: ['number', 'currency', 'percent'] },
      detail_format: { type: 'string', enum: ['number', 'currency', 'percent'] },
      trend_format:  { type: 'string', enum: ['number', 'currency', 'percent'] },
      trend_label:   { type: 'string' },
    },
  },

  SegmentedBar: {
    type: 'object',
    required: ['segments'],
    properties: {
      segments: { type: 'array', minItems: 1, items: SHARED_DEFINITIONS.segment },
    },
  },

  DataTable: {
    type: 'object',
    required: ['columns'],
    properties: {
      columns: {
        type: 'array',
        minItems: 1,
        items: { oneOf: [{ type: 'string' }, SHARED_DEFINITIONS.columnObject] },
        description: 'Column definitions. Each entry is a string key or a column config object.',
      },
      api_endpoint: { type: 'string', description: 'Backend endpoint to fetch rows from.' },
      data:         { type: 'array', description: 'Static row data (alternative to api_endpoint).' },
      selection:    { type: 'string', enum: ['none', 'single', 'multi'], default: 'none' },
      pagination:   { type: 'boolean', default: true },
      page_size:    { type: 'integer', minimum: 1, default: 20 },
      search:       { type: 'boolean', default: true },
      actions:      { type: 'array', items: SHARED_DEFINITIONS.action },
      empty: {
        type: 'object',
        properties: {
          title:   { type: 'string' },
          message: { type: 'string' },
          action:  SHARED_DEFINITIONS.action,
        },
      },
    },
  },

  Form: {
    type: 'object',
    required: ['fields'],
    properties: {
      fields:        { type: 'array', minItems: 1, items: SHARED_DEFINITIONS.formField },
      layout:        { type: 'string', enum: ['vertical', 'horizontal', 'grid'], default: 'vertical' },
      columns:       { type: 'integer', minimum: 1, default: 2 },
      submit_label:  { type: 'string', default: 'Submit' },
      submit_action: SHARED_DEFINITIONS.action,
      cancel_action: SHARED_DEFINITIONS.action,
      cancel_label:  { type: 'string', default: 'Cancel' },
      disabled:      { type: 'boolean', default: false },
    },
  },

  Grid: {
    type: 'object',
    required: ['columns', 'children'],
    properties: {
      columns:      { type: 'integer', minimum: 1, maximum: 6, description: 'Number of grid columns.' },
      gap:          { type: 'string', enum: ['sm', 'md', 'lg', '1', '2', '3', '4', '6', '8'], default: '4' },
      api_endpoint: { type: 'string', description: 'Optional shared data endpoint for child primitive binding.' },
      children:     { type: 'array', minItems: 1, items: SHARED_DEFINITIONS.childSection },
    },
  },

  Button: {
    type: 'object',
    required: ['label'],
    properties: {
      label:    { type: 'string' },
      variant:  { type: 'string', enum: ['primary', 'secondary', 'ghost', 'destructive'], default: 'primary' },
      size:     { type: 'string', enum: ['sm', 'default', 'lg'], default: 'default' },
      icon:     { type: 'string' },
      disabled: { type: 'boolean', default: false },
      action:   SHARED_DEFINITIONS.action,
    },
  },

  Modal: {
    type: 'object',
    properties: {
      title:       { type: 'string' },
      description: { type: 'string' },
      size:        { type: 'string', enum: ['small', 'medium', 'large', 'full'], default: 'medium' },
      actions:     { type: 'array', items: SHARED_DEFINITIONS.action },
      children:    { type: 'array', items: SHARED_DEFINITIONS.childSection },
    },
  },

  Alert: {
    type: 'object',
    required: ['message'],
    properties: {
      title:       { type: 'string' },
      message:     { type: 'string' },
      variant:     { type: 'string', enum: ['default', 'info', 'success', 'warning', 'destructive'], default: 'default' },
      dismissible: { type: 'boolean', default: false },
    },
  },

  Skeleton: {
    type: 'object',
    properties: {
      rows:   { type: 'integer', minimum: 1 },
      height: { type: 'string' },
    },
  },

  Empty: {
    type: 'object',
    properties: {
      title:   { type: 'string' },
      message: { type: 'string' },
      action:  SHARED_DEFINITIONS.action,
      icon:    { type: 'string' },
    },
  },

  Timeline: {
    type: 'object',
    required: ['items'],
    properties: {
      title: { type: 'string' },
      items: { type: 'array', minItems: 1, items: SHARED_DEFINITIONS.timelineItem },
    },
  },

  CodeBlock: {
    type: 'object',
    required: ['code'],
    properties: {
      title:    { type: 'string' },
      code:     { type: 'string' },
      language: { type: 'string' },
      filename: { type: 'string' },
    },
  },

  ProgressTracker: {
    type: 'object',
    required: ['stages'],
    properties: {
      title:  { type: 'string' },
      stages: { type: 'array', minItems: 1, items: SHARED_DEFINITIONS.stageItem },
    },
  },

  AlertBanner: {
    type: 'object',
    required: ['message'],
    properties: {
      title:       { type: 'string' },
      message:     { type: 'string' },
      variant:     { type: 'string', enum: ['info', 'success', 'warning', 'destructive'] },
      dismissible: { type: 'boolean' },
      actions:     { type: 'array', items: SHARED_DEFINITIONS.action },
    },
  },

  ActionButton: {
    type: 'object',
    required: ['actions'],
    properties: {
      title:   { type: 'string' },
      layout:  { type: 'string', enum: ['row', 'column'], default: 'row' },
      actions: {
        type: 'array',
        minItems: 1,
        items: {
          type: 'object',
          required: ['label'],
          properties: {
            id:      { type: 'string' },
            label:   { type: 'string' },
            variant: { type: 'string' },
            action:  SHARED_DEFINITIONS.action,
          },
        },
      },
    },
  },

  FileList: {
    type: 'object',
    required: ['files'],
    properties: {
      title: { type: 'string' },
      files: { type: 'array', minItems: 1, items: SHARED_DEFINITIONS.fileItem },
    },
  },
};
