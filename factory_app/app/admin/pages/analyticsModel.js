/**
 * analyticsModel — pure view-model helpers for the Studio analytics surfaces.
 *
 * No React, no JSX, no I/O: this module turns the deterministic payloads of
 * /api/studio/analytics/* into render-ready structures. Metric semantics
 * (units, polarity, labels) come from the backend registry payload — this
 * file never re-derives metric math, it only formats and arranges it.
 */

// ── Periods ──────────────────────────────────────────────────────────────────

export const ANALYTICS_PERIODS = [
  { value: '7d', label: '7D' },
  { value: '30d', label: '30D' },
  { value: '90d', label: '90D' },
];

export const DEFAULT_ANALYTICS_PERIOD = '30d';

// ── Headline metric sets (progressive disclosure: 4 per domain) ─────────────

export const WORLD_REVENUE_HEADLINES = ['mrr', 'mrr_growth', 'net_new_mrr', 'nrr'];
export const WORLD_USERS_HEADLINES = ['active_users', 'paying_users', 'user_growth', 'paid_conversion'];

export const APP_REVENUE_HEADLINES = WORLD_REVENUE_HEADLINES;
export const APP_USERS_HEADLINES = WORLD_USERS_HEADLINES;

export const REVENUE_PAGE_HEADLINES = ['mrr', 'arr', 'mrr_growth', 'nrr', 'arppu'];
export const USERS_PAGE_HEADLINES = ['active_users', 'paying_users', 'new_users', 'paid_conversion', 'retention'];

// ── Trend metric switchers ───────────────────────────────────────────────────

export const REVENUE_TREND_METRICS = [
  { value: 'mrr', label: 'MRR' },
  { value: 'net_new_mrr', label: 'Net New MRR' },
  { value: 'arr', label: 'ARR' },
];

export const USERS_TREND_METRICS = [
  { value: 'active_users', label: 'Active' },
  { value: 'paying_users', label: 'Paying' },
  { value: 'new_users', label: 'New' },
];

// ── Formatting ───────────────────────────────────────────────────────────────

export function formatCurrency(value, fallback = 'Pending') {
  if (value == null || Number.isNaN(Number(value))) return fallback;
  const num = Number(value);
  const abs = Math.abs(num);
  if (abs >= 1000) {
    return new Intl.NumberFormat(undefined, {
      style: 'currency',
      currency: 'USD',
      notation: 'compact',
      maximumFractionDigits: 1,
    }).format(num);
  }
  const fractionDigits = abs > 0 && abs < 0.01 ? 4 : 2;
  return new Intl.NumberFormat(undefined, {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: fractionDigits,
    maximumFractionDigits: fractionDigits,
  }).format(num);
}

export function formatCount(value, fallback = 'Pending') {
  if (value == null || Number.isNaN(Number(value))) return fallback;
  return new Intl.NumberFormat(undefined, {
    notation: Math.abs(Number(value)) >= 1000 ? 'compact' : 'standard',
    maximumFractionDigits: 1,
  }).format(Number(value));
}

export function formatPercent(value, fallback = 'Pending') {
  if (value == null || Number.isNaN(Number(value))) return fallback;
  const num = Number(value);
  return `${num.toFixed(Math.abs(num) % 1 === 0 ? 0 : 1)}%`;
}

export function formatMetricValue(definition, value, fallback = 'Pending') {
  if (!definition) return formatCount(value, fallback);
  if (definition.unit === 'currency_usd') return formatCurrency(value, fallback);
  if (definition.unit === 'percent') return formatPercent(value, fallback);
  return formatCount(value, fallback);
}

// ── Deltas ───────────────────────────────────────────────────────────────────

function deltaTone(definition, rawDelta) {
  if (rawDelta == null || rawDelta === 0) return 'neutral';
  const polarity = definition?.polarity || 'higher_is_better';
  if (polarity === 'neutral') return 'neutral';
  const improving = polarity === 'higher_is_better' ? rawDelta > 0 : rawDelta < 0;
  return improving ? 'positive' : 'negative';
}

/**
 * A polarity-aware delta view for a metric envelope
 * ({value, previous, delta, delta_pct}).
 *
 * Percent-unit metrics show point changes ("+2.4 pts"); everything else
 * shows relative change ("+8.1%") when defined, falling back to absolute.
 */
export function buildDeltaView(definition, envelope, comparisonLabel = null) {
  if (!envelope || envelope.value == null || envelope.previous == null) return null;
  const isPercentUnit = definition?.unit === 'percent';
  const rawDelta = isPercentUnit ? envelope.delta : (envelope.delta_pct ?? envelope.delta);
  if (rawDelta == null) return null;
  const magnitude = Math.abs(Number(rawDelta));
  const label = isPercentUnit
    ? `${magnitude.toFixed(1)} pts`
    : envelope.delta_pct != null
      ? `${magnitude.toFixed(1)}%`
      : formatMetricValue(definition, magnitude, '');
  const direction = Number(rawDelta) > 0 ? 'up' : Number(rawDelta) < 0 ? 'down' : 'flat';
  return {
    label,
    tone: deltaTone(definition, Number(rawDelta)),
    rising: direction === 'flat' ? null : direction === 'up',
    srLabel: `${direction === 'up' ? 'up' : direction === 'down' ? 'down' : 'unchanged'} ${label}${comparisonLabel ? ` ${comparisonLabel}` : ''}`,
  };
}

// Growth-style metrics (derived, no previous window) compare against zero.
function growthDelta(definition, envelope) {
  if (!envelope || envelope.value == null) return null;
  const value = Number(envelope.value);
  return {
    label: `${Math.abs(value).toFixed(1)}%`,
    tone: deltaTone(definition, value),
    rising: value === 0 ? null : value > 0,
    srLabel: `${value >= 0 ? 'up' : 'down'} ${Math.abs(value).toFixed(1)}%`,
  };
}

// ── Headline strips ──────────────────────────────────────────────────────────

/**
 * MetricSummaryStrip items for a set of metric ids.
 *
 * @param registry  backend registry payload (metric_id -> definition)
 * @param values    backend values map (metric_id -> envelope)
 * @param metricIds ordered ids to display
 * @param options   { comparisonLabel, onSelect(metricId) }
 */
export function buildHeadlineItems(registry, values, metricIds, options = {}) {
  const { comparisonLabel = null, onSelect = null } = options;
  return (metricIds || []).map((metricId) => {
    const definition = registry?.[metricId] || null;
    const envelope = values?.[metricId] || null;
    const available = Boolean(envelope?.available);
    const isGrowth = definition?.source === 'derived' && /growth/.test(metricId);
    const delta = isGrowth
      ? null
      : buildDeltaView(definition, envelope, comparisonLabel);
    const value = isGrowth
      ? formatPercent(envelope?.value, 'Pending')
      : formatMetricValue(definition, envelope?.value, 'Pending');
    const growthView = isGrowth ? growthDelta(definition, envelope) : null;
    return {
      id: metricId,
      label: definition?.short_label || definition?.label || metricId,
      value,
      delta: growthView || delta,
      detail: available ? null : 'No data yet',
      pending: !available,
      onSelect: onSelect ? () => onSelect(metricId) : undefined,
    };
  });
}

// ── Trend series ─────────────────────────────────────────────────────────────

const DAY_LABEL = new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric' });
const DAY_DETAIL = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium' });

function parseDay(periodStart) {
  const date = new Date(`${periodStart}T00:00:00Z`);
  return Number.isNaN(date.getTime()) ? null : date;
}

/** UsageTrendPanel points from a backend [{period_start, value}] series. */
export function buildTrendPoints(series, definition, maxPoints = 21) {
  const rows = (Array.isArray(series) ? series : [])
    .filter((row) => row && row.period_start)
    .slice(-maxPoints);
  return rows.map((row) => {
    const date = parseDay(row.period_start);
    return {
      id: row.period_start,
      label: date ? DAY_LABEL.format(date) : row.period_start,
      detail: date ? DAY_DETAIL.format(date) : row.period_start,
      value: Number(row.value || 0),
      extras: [
        { label: 'Date', value: date ? DAY_DETAIL.format(date) : row.period_start },
        {
          label: definition?.short_label || definition?.label || 'Value',
          value: formatMetricValue(definition, row.value, '—'),
        },
      ],
    };
  });
}

// ── MRR bridge ───────────────────────────────────────────────────────────────

const BRIDGE_STEPS = [
  { metricId: 'new_mrr', direction: 'add' },
  { metricId: 'expansion_mrr', direction: 'add' },
  { metricId: 'contraction_mrr', direction: 'subtract' },
  { metricId: 'churned_mrr', direction: 'subtract' },
];

/** MetricBridge props from the backend movement payload; null when unavailable. */
export function buildBridgeModel(movement, registry, options = {}) {
  if (!movement || !movement.available) return null;
  const { onSelect = null } = options;
  const steps = BRIDGE_STEPS.map(({ metricId, direction }) => {
    const definition = registry?.[metricId] || null;
    const value = movement[metricId];
    return {
      id: metricId,
      label: definition?.label || metricId,
      value: Number(value || 0),
      formatted: formatCurrency(Math.abs(Number(value || 0)), '$0.00'),
      direction,
      onSelect: onSelect ? () => onSelect(metricId) : undefined,
    };
  });
  return {
    start: { label: 'Starting MRR', formatted: formatCurrency(movement.starting_mrr, '—') },
    end: { label: 'Ending MRR', formatted: formatCurrency(movement.ending_mrr, '—') },
    steps,
    unexplained:
      movement.unexplained != null
        ? { formatted: formatCurrency(movement.unexplained, '—') }
        : null,
  };
}

// ── Funnel ───────────────────────────────────────────────────────────────────

/** StageFunnel steps from the backend funnel payload; [] when not renderable. */
export function buildFunnelSteps(funnel, options = {}) {
  if (!funnel || !funnel.configured || !Array.isArray(funnel.steps)) return [];
  const { onSelect = null } = options;
  return funnel.steps.map((step, index) => ({
    id: step.step_id,
    label: step.label,
    count: Number(step.count || 0),
    formattedCount: formatCount(step.count, '0'),
    conversionLabel:
      index > 0 && step.conversion_rate != null
        ? `${Number(step.conversion_rate).toFixed(0)}% of previous`
        : null,
    onSelect: onSelect ? () => onSelect(step) : undefined,
  }));
}

// ── Portfolio table columns ──────────────────────────────────────────────────

export const PORTFOLIO_DEFAULT_COLUMNS = ['mrr', 'mrr_growth', 'paying_users', 'active_users', 'paid_conversion'];
export const PORTFOLIO_OPTIONAL_COLUMNS = [
  'arr',
  'net_new_mrr',
  'nrr',
  'churn_rate',
  'arppu',
  'retention',
  'new_users',
];

/** Resolve the visible metric columns, keeping a stable canonical order. */
export function resolvePortfolioColumns(selectedOptional = []) {
  const optional = PORTFOLIO_OPTIONAL_COLUMNS.filter((id) => selectedOptional.includes(id));
  return [...PORTFOLIO_DEFAULT_COLUMNS, ...optional];
}

/** Sort app rows for the portfolio table: largest MRR first, then actives. */
export function sortPortfolioRows(rows) {
  return [...(Array.isArray(rows) ? rows : [])].sort((left, right) => {
    const leftMrr = left?.metrics?.mrr?.value;
    const rightMrr = right?.metrics?.mrr?.value;
    if ((rightMrr ?? -1) !== (leftMrr ?? -1)) return (rightMrr ?? -1) - (leftMrr ?? -1);
    const leftActive = left?.metrics?.active_users?.value;
    const rightActive = right?.metrics?.active_users?.value;
    if ((rightActive ?? -1) !== (leftActive ?? -1)) return (rightActive ?? -1) - (leftActive ?? -1);
    return String(left?.name || '').localeCompare(String(right?.name || ''));
  });
}

// ── Insights ─────────────────────────────────────────────────────────────────

/** InsightList items from backend insights; wires navigation per insight. */
export function buildInsightItems(insights, options = {}) {
  const { onSelect = null } = options;
  return (Array.isArray(insights) ? insights : []).map((insight) => ({
    id: insight.insight_id ? `${insight.insight_id}:${insight.app_id}` : insight.headline,
    severity: insight.severity,
    headline: insight.headline,
    detail: insight.detail,
    onSelect: onSelect ? () => onSelect(insight) : undefined,
  }));
}
