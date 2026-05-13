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
      type:     { type: 'string', enum: ['text', 'number', 'date', 'badge', 'actions'] },
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
    required: ['label', 'value'],
    properties: {
      label:  { type: 'string' },
      value:  {},
      detail: { type: 'string' },
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

  Stat: {
    type: 'object',
    required: ['label'],
    properties: {
      label:           { type: 'string', description: 'KPI metric label.' },
      value:           { description: 'Static metric value.' },
      value_key:       { type: 'string', description: 'Key path into api_endpoint response for dynamic value.' },
      format:          { type: 'string', enum: ['number', 'currency', 'percentage', 'compact'] },
      trend:           { description: 'Static trend value (positive = up).' },
      trend_key:       { type: 'string', description: 'Key path into api_endpoint response for dynamic trend.' },
      trend_direction: { type: 'string', enum: ['up_good', 'up_bad', 'neutral'], default: 'up_good' },
      color:           { type: 'string' },
      icon:            { type: 'string' },
    },
  },

  Grid: {
    type: 'object',
    required: ['columns', 'children'],
    properties: {
      columns:      { type: 'integer', minimum: 1, maximum: 6, description: 'Number of grid columns.' },
      gap:          { type: 'string', enum: ['sm', 'md', 'lg', '1', '2', '3', '4', '6', '8'], default: '4' },
      api_endpoint: { type: 'string', description: 'Optional shared data endpoint for child Stat/Card binding.' },
      children:     { type: 'array', minItems: 1, items: SHARED_DEFINITIONS.childSection },
    },
  },

  Card: {
    type: 'object',
    properties: {
      title:    { type: 'string' },
      subtitle: { type: 'string' },
      actions:  { type: 'array', items: SHARED_DEFINITIONS.action },
      children: { type: 'array', items: SHARED_DEFINITIONS.childSection },
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

  Badge: {
    type: 'object',
    required: ['label'],
    properties: {
      label:   { type: 'string' },
      variant: { type: 'string' },
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
