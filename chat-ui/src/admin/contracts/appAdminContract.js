export const APP_BACKEND_ADMIN_SCHEMA_VERSION = 'mozaiks.admin.app_backend.v1';

export const APP_ADMIN_SECTION_IDS = Object.freeze([
  'overview',
  'users',
  'billing',
  'usage',
  'operations',
  'settings',
  'integrations',
  'support',
]);

export const APP_ADMIN_LAYOUTS = Object.freeze([
  'grid',
  'sidebar',
  'full-width',
  'split',
]);

export const APP_ADMIN_BUILTIN_PANELS = Object.freeze([
  'stats',
  'users',
  'subscriptions',
]);

const SECTION_SET = new Set(APP_ADMIN_SECTION_IDS);
const LAYOUT_SET = new Set(APP_ADMIN_LAYOUTS);
const BUILTIN_SET = new Set(APP_ADMIN_BUILTIN_PANELS);

function isRecord(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function optionalText(value) {
  if (typeof value !== 'string') return null;
  const text = value.trim();
  return text || null;
}

function requiredText(value) {
  const text = optionalText(value);
  return text || null;
}

function normalizeSection(value) {
  const section = optionalText(value)?.toLowerCase().replace(/_/g, '-') || null;
  return section && SECTION_SET.has(section) ? section : null;
}

function normalizePermissions(value) {
  if (!Array.isArray(value)) return [];
  return [...new Set(value.map(optionalText).filter(Boolean))];
}

function normalizeBasePanel(panel) {
  const id = requiredText(panel.id);
  const label = requiredText(panel.label);
  const section = normalizeSection(panel.section);
  if (!id || !label || !section) {
    return null;
  }

  return {
    id,
    label,
    description: optionalText(panel.description),
    section,
    order: Number.isInteger(panel.order) ? panel.order : 0,
    permissions: normalizePermissions(panel.permissions),
  };
}

function normalizeSchemaPanel(panel, base) {
  const sections = Array.isArray(panel.sections) && panel.sections.every(isRecord)
    ? panel.sections
    : null;
  if (!sections || sections.length === 0) {
    return null;
  }

  const layout = optionalText(panel.layout);
  return {
    ...base,
    renderer: 'schema',
    builtin_panel: null,
    layout: layout && LAYOUT_SET.has(layout) ? layout : 'full-width',
    sections,
    component: null,
  };
}

function normalizeCustomComponentPanel(panel, base) {
  const component = requiredText(panel.component);
  if (!component) {
    return null;
  }

  return {
    ...base,
    renderer: 'custom_component',
    builtin_panel: null,
    layout: null,
    sections: [],
    component,
  };
}

function normalizeBuiltinPanel(panel, base) {
  const builtinPanel = optionalText(panel.builtin_panel);
  if (!builtinPanel || !BUILTIN_SET.has(builtinPanel)) {
    return null;
  }

  return {
    ...base,
    renderer: 'builtin',
    builtin_panel: builtinPanel,
    layout: null,
    sections: [],
    component: null,
  };
}

export function parseAppBackendAdminConfig(rawConfig) {
  const issues = [];

  if (!isRecord(rawConfig)) {
    return {
      schema_version: APP_BACKEND_ADMIN_SCHEMA_VERSION,
      panels: [],
      issues: ['App backend admin config must be an object.'],
    };
  }

  if (rawConfig.schema_version !== APP_BACKEND_ADMIN_SCHEMA_VERSION) {
    return {
      schema_version: APP_BACKEND_ADMIN_SCHEMA_VERSION,
      panels: [],
      issues: [
        `App backend admin config must declare schema_version=${APP_BACKEND_ADMIN_SCHEMA_VERSION}.`,
      ],
    };
  }

  const rawPanels = rawConfig.panels;
  if (!Array.isArray(rawPanels)) {
    return {
      schema_version: APP_BACKEND_ADMIN_SCHEMA_VERSION,
      panels: [],
      issues: ['App backend admin config must declare panels[] as an array.'],
    };
  }

  const panels = [];
  const seen = new Set();

  rawPanels.forEach((panel, index) => {
    if (!isRecord(panel)) {
      issues.push(`panels[${index}] must be an object.`);
      return;
    }

    const base = normalizeBasePanel(panel);
    if (!base) {
      issues.push(`panels[${index}] is missing required id, label, or valid section.`);
      return;
    }

    if (seen.has(base.id)) {
      issues.push(`panels[${index}] duplicates id=${base.id}.`);
      return;
    }

    const renderer = optionalText(panel.renderer);
    let normalized = null;
    if (renderer === 'schema') {
      normalized = normalizeSchemaPanel(panel, base);
    } else if (renderer === 'custom_component') {
      normalized = normalizeCustomComponentPanel(panel, base);
    } else if (renderer === 'builtin') {
      normalized = normalizeBuiltinPanel(panel, base);
    } else {
      issues.push(`panels[${index}] must declare renderer as schema, custom_component, or builtin.`);
      return;
    }

    if (!normalized) {
      issues.push(`panels[${index}] does not satisfy the ${renderer} contract.`);
      return;
    }

    seen.add(base.id);
    panels.push(normalized);
  });

  return {
    schema_version: APP_BACKEND_ADMIN_SCHEMA_VERSION,
    panels,
    issues,
  };
}
