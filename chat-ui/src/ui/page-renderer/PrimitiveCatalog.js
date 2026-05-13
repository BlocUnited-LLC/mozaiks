/**
 * PrimitiveCatalog — product-quality guidance for generated page primitives.
 *
 * PrimitiveRegistry.js answers "what can render?"
 * PrimitiveSchemas.js answers "what config is valid?"
 * This catalog answers "when should an agent use it?"
 */

export const PRIMITIVE_CATALOG = {
  PageHeader: {
    tier: 'default',
    use: 'Use once at the top of a durable page for title, short subtitle, and primary actions.',
    avoid: 'Do not repeat page scope with nearby eyebrow labels, duplicate titles, or extra heading sections.',
  },
  ResourceTable: {
    tier: 'default',
    use: 'Use for collection/index pages that need search, filters, sorting, status, updated time, and row actions.',
    avoid: 'Do not wrap ResourceTable in decorative cards or add fake analytics around it.',
  },
  Form: {
    tier: 'default',
    use: 'Use for real create/edit/settings input surfaces with clear submit and optional cancel actions.',
    avoid: 'Do not generate forms without a submit path or with placeholder-only fields.',
  },
  Empty: {
    tier: 'default',
    use: 'Use for simple empty states with one concise message and at most one action.',
    avoid: 'Do not repeat empty states across every section of the same page.',
  },
  InlineEmptyState: {
    tier: 'default',
    use: 'Use inside a panel/table area when one bounded region has no data.',
    avoid: 'Do not use as filler copy when the section itself should be omitted.',
  },
  LoadingState: {
    tier: 'default',
    use: 'Use for full-page or full-region loading while real data is being fetched.',
    avoid: 'Do not use decorative skeletons when a plain loading state is enough.',
  },
  ErrorState: {
    tier: 'default',
    use: 'Use when a page or region cannot load actionable data.',
    avoid: 'Do not expose internal runtime names or stack traces in user-facing errors.',
  },

  SummaryStrip: {
    tier: 'support',
    use: 'Use for 2-4 genuinely useful portfolio metrics above a collection.',
    avoid: 'Do not create generic KPI strips or more than four stats.',
  },
  Panel: {
    tier: 'support',
    use: 'Use for one purposeful supporting section on a detail page.',
    avoid: 'Do not nest panels or turn every fact into a panel.',
  },
  SurfaceCard: {
    tier: 'support',
    use: 'Use for one prominent grouped surface when a panel needs stronger hierarchy.',
    avoid: 'Do not use multiple SurfaceCards to create a dashboard wall.',
  },
  StatusPill: {
    tier: 'support',
    use: 'Use for one compact state label per object.',
    avoid: 'Do not show multiple badges for the same status concept.',
  },
  Button: {
    tier: 'support',
    use: 'Use for single actions outside PageHeader, ResourceTable, or Form.',
    avoid: 'Do not scatter duplicate primary CTAs across the page.',
  },
  Alert: {
    tier: 'support',
    use: 'Use for important inline contextual messages.',
    avoid: 'Do not use alerts as explanatory decoration.',
  },
  AlertBanner: {
    tier: 'support',
    use: 'Use for high-priority page-level alerts that change what the user should do.',
    avoid: 'Do not use banners for routine status or marketing copy.',
  },
  Metric: {
    tier: 'support',
    use: 'Use inside a purposeful summary surface when one metric needs detail text.',
    avoid: 'Prefer SummaryStrip for page-level metrics; do not build KPI grids by default.',
  },
  SegmentedBar: {
    tier: 'support',
    use: 'Use for compact proportional breakdowns with real segment values.',
    avoid: 'Do not fake charts or show decorative bars without useful data.',
  },

  DataTable: {
    tier: 'specialized',
    use: 'Use only when ResourceTable is not appropriate for dense operational data.',
    avoid: 'Do not use as the default collection page primitive; prefer ResourceTable.',
  },
  Grid: {
    tier: 'specialized',
    use: 'Use only to arrange a small number of child primitives when layout is essential.',
    avoid: 'Do not use Grid to manufacture dashboard sections.',
  },
  Modal: {
    tier: 'specialized',
    use: 'Use for interruptive confirmation or compact create/edit flows.',
    avoid: 'Do not hide primary page tasks in modals by default.',
  },
  Timeline: {
    tier: 'specialized',
    use: 'Use for real chronological events such as deployments or approvals.',
    avoid: 'Do not generate decorative activity feeds.',
  },
  CodeBlock: {
    tier: 'specialized',
    use: 'Use for real code, config, logs, or command output.',
    avoid: 'Do not show pseudo-code placeholders.',
  },
  ProgressTracker: {
    tier: 'specialized',
    use: 'Use when the user is moving through a known ordered process.',
    avoid: 'Do not use for generic lifecycle decoration.',
  },
  ActionButton: {
    tier: 'specialized',
    use: 'Use for a compact cluster of related actions when PageHeader actions are not enough.',
    avoid: 'Do not create multiple competing primary actions.',
  },
  FileList: {
    tier: 'specialized',
    use: 'Use when the page has real files or generated artifacts to show.',
    avoid: 'Do not create placeholder file lists.',
  },
  Skeleton: {
    tier: 'specialized',
    use: 'Use only for short in-place loading placeholders.',
    avoid: 'Prefer LoadingState for full-page loading.',
  },

};

export const DEFAULT_PAGE_RECIPE = [
  'PageHeader',
  'ResourceTable or Form',
  'Empty or InlineEmptyState',
  'SummaryStrip only when metrics are explicitly useful',
  'Panel only for one purposeful supporting section',
];

export default PRIMITIVE_CATALOG;
