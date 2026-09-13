import assert from 'node:assert/strict';
import test from 'node:test';

import {
  PORTFOLIO_DEFAULT_COLUMNS,
  PORTFOLIO_OPTIONAL_COLUMNS,
  buildBridgeModel,
  buildDeltaView,
  buildFunnelSteps,
  buildHeadlineItems,
  buildInsightItems,
  buildTrendPoints,
  formatMetricValue,
  formatPercent,
  resolvePortfolioColumns,
  sortPortfolioRows,
} from '../../factory_app/app/admin/pages/analyticsModel.js';

const CURRENCY_DEF = { unit: 'currency_usd', polarity: 'higher_is_better', label: 'MRR', short_label: 'MRR' };
const PERCENT_DEF = { unit: 'percent', polarity: 'higher_is_better', label: 'NRR' };
const CHURN_DEF = { unit: 'percent', polarity: 'lower_is_better', label: 'Churn' };

test('formatMetricValue routes by unit and keeps pending fallback', () => {
  assert.equal(formatMetricValue(CURRENCY_DEF, null), 'Pending');
  assert.equal(formatPercent(103.4), '103.4%');
  assert.equal(formatPercent(100), '100%');
  assert.match(formatMetricValue(CURRENCY_DEF, 12.5), /12\.50/);
  assert.match(formatMetricValue({ unit: 'count' }, 950), /950/);
});

test('buildDeltaView is polarity aware', () => {
  const rising = buildDeltaView(CURRENCY_DEF, { value: 110, previous: 100, delta: 10, delta_pct: 10 });
  assert.equal(rising.tone, 'positive');
  assert.equal(rising.rising, true);
  assert.equal(rising.label, '10.0%');

  const falling = buildDeltaView(CURRENCY_DEF, { value: 90, previous: 100, delta: -10, delta_pct: -10 });
  assert.equal(falling.tone, 'negative');
  assert.equal(falling.rising, false);

  // Churn falling is an improvement: tone positive while direction is down.
  const churnDrop = buildDeltaView(CHURN_DEF, { value: 3, previous: 5, delta: -2, delta_pct: -40 });
  assert.equal(churnDrop.tone, 'positive');
  assert.equal(churnDrop.rising, false);
  assert.equal(churnDrop.label, '2.0 pts'); // percent units show point changes

  assert.equal(buildDeltaView(CURRENCY_DEF, { value: 100, previous: null }), null);
});

test('buildHeadlineItems marks missing data pending instead of faking zeros', () => {
  const registry = { mrr: CURRENCY_DEF, nrr: PERCENT_DEF };
  const values = {
    mrr: { value: 1000, previous: 900, delta: 100, delta_pct: 11.1, available: true },
    nrr: { value: null, previous: null, delta: null, delta_pct: null, available: false },
  };
  const items = buildHeadlineItems(registry, values, ['mrr', 'nrr'], {});
  assert.equal(items.length, 2);
  assert.equal(items[0].pending, false);
  assert.equal(items[1].pending, true);
  assert.equal(items[1].detail, 'No data yet');
});

test('buildTrendPoints converts backend series and caps points', () => {
  const series = Array.from({ length: 30 }, (_, index) => ({
    period_start: `2026-08-${String(index + 1).padStart(2, '0')}`,
    value: index,
  }));
  const points = buildTrendPoints(series, CURRENCY_DEF, 21);
  assert.equal(points.length, 21);
  assert.equal(points[0].value, 9);
  assert.ok(points[0].extras.length >= 2);
});

test('buildBridgeModel returns null when movement unavailable and composes drivers otherwise', () => {
  assert.equal(buildBridgeModel({ available: false }, {}), null);
  const registry = {
    new_mrr: { label: 'New MRR', unit: 'currency_usd' },
    expansion_mrr: { label: 'Expansion MRR', unit: 'currency_usd' },
    contraction_mrr: { label: 'Contraction MRR', unit: 'currency_usd' },
    churned_mrr: { label: 'Churned MRR', unit: 'currency_usd' },
  };
  const bridge = buildBridgeModel(
    {
      available: true,
      starting_mrr: 1000,
      ending_mrr: 1350,
      unexplained: null,
      new_mrr: 500,
      expansion_mrr: 200,
      contraction_mrr: 100,
      churned_mrr: 250,
    },
    registry,
  );
  assert.equal(bridge.steps.length, 4);
  assert.deepEqual(
    bridge.steps.map((step) => step.direction),
    ['add', 'add', 'subtract', 'subtract'],
  );
  assert.equal(bridge.unexplained, null);
});

test('buildFunnelSteps labels stage-to-stage conversion from the second stage', () => {
  const funnel = {
    configured: true,
    steps: [
      { step_id: 'signed_up', label: 'Signed up', count: 200, conversion_rate: null },
      { step_id: 'paid', label: 'Paid', count: 50, conversion_rate: 25 },
    ],
  };
  const steps = buildFunnelSteps(funnel);
  assert.equal(steps[0].conversionLabel, null);
  assert.equal(steps[1].conversionLabel, '25% of previous');
  assert.deepEqual(buildFunnelSteps({ configured: false }), []);
});

test('resolvePortfolioColumns keeps canonical order and rejects unknown ids', () => {
  const columns = resolvePortfolioColumns(['nrr', 'arr', 'not_a_metric']);
  assert.deepEqual(columns.slice(0, PORTFOLIO_DEFAULT_COLUMNS.length), PORTFOLIO_DEFAULT_COLUMNS);
  assert.deepEqual(
    columns.slice(PORTFOLIO_DEFAULT_COLUMNS.length),
    PORTFOLIO_OPTIONAL_COLUMNS.filter((id) => ['arr', 'nrr'].includes(id)),
  );
});

test('sortPortfolioRows puts largest MRR first and missing data last', () => {
  const rows = [
    { name: 'NoData', metrics: {} },
    { name: 'Small', metrics: { mrr: { value: 10 } } },
    { name: 'Big', metrics: { mrr: { value: 1000 } } },
  ];
  assert.deepEqual(
    sortPortfolioRows(rows).map((row) => row.name),
    ['Big', 'Small', 'NoData'],
  );
});

test('buildInsightItems wires selection through to the insight payload', () => {
  let selected = null;
  const items = buildInsightItems(
    [{ insight_id: 'mrr_decline', app_id: 'a', severity: 'attention', headline: 'X declined', metric_id: 'mrr' }],
    { onSelect: (insight) => { selected = insight; } },
  );
  assert.equal(items[0].severity, 'attention');
  items[0].onSelect();
  assert.equal(selected.app_id, 'a');
});
