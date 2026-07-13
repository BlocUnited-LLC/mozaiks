export const USAGE_TREND_METRICS = [
  { value: 'cost', label: 'Spend' },
  { value: 'tokens', label: 'Tokens' },
  { value: 'runs', label: 'Chats' },
];

function readNumber(...values) {
  for (const value of values) {
    const number = Number(value);
    if (Number.isFinite(number)) return number;
  }
  return 0;
}

export function getUsageRows(usage, fallbackRows = []) {
  if (Array.isArray(usage?.by_run) && usage.by_run.length > 0) return usage.by_run;
  if (Array.isArray(usage?.events) && usage.events.length > 0) return usage.events;
  return Array.isArray(fallbackRows) ? fallbackRows : [];
}

export function readUsageInputTokens(row) {
  return readNumber(row?.prompt_tokens, row?.input_tokens, row?.inputTokens);
}

export function readUsageOutputTokens(row) {
  return readNumber(row?.completion_tokens, row?.output_tokens, row?.outputTokens);
}

export function readUsageTotalTokens(row) {
  const explicitTotal = readNumber(row?.total_tokens, row?.tokens_used, row?.totalTokens);
  if (explicitTotal > 0) return explicitTotal;
  return readUsageInputTokens(row) + readUsageOutputTokens(row);
}

export function readUsageCost(row) {
  return readNumber(row?.estimated_cost_usd, row?.cost, row?.llm_cost_usd, row?.total_cost);
}

export function readUsageRunCount(row) {
  const count = readNumber(row?.chats, row?.chat_count, row?.runs, row?.count, row?.workflow_runs, row?.run_count);
  return count > 0 ? count : 1;
}

function readUsageDate(row) {
  const value = row?.started_at || row?.event_ts || row?.created_at || row?.updated_at || row?.timestamp;
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

function formatDayLabel(date) {
  return new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric' }).format(date);
}

function formatDayDetail(date) {
  return new Intl.DateTimeFormat(undefined, { dateStyle: 'medium' }).format(date);
}

function dayKey(date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

export function buildUsageTrendSeries(rows, metric, maxPoints = 21) {
  const buckets = new Map();
  const sourceRows = Array.isArray(rows) ? rows : [];

  sourceRows.forEach((row, index) => {
    const date = readUsageDate(row);
    const key = date ? dayKey(date) : 'undated';
    const sortValue = date ? date.getTime() : index;
    const value = metric === 'cost'
      ? readUsageCost(row)
      : metric === 'runs'
        ? readUsageRunCount(row)
        : readUsageTotalTokens(row);

    if (!buckets.has(key)) {
      buckets.set(key, {
        id: key,
        label: date ? formatDayLabel(date) : 'Usage',
        detail: date ? formatDayDetail(date) : 'Undated usage',
        value: 0,
        cost: 0,
        tokens: 0,
        runs: 0,
        sortValue,
      });
    }

    const bucket = buckets.get(key);
    bucket.value += value;
    bucket.cost += readUsageCost(row);
    bucket.tokens += readUsageTotalTokens(row);
    bucket.runs += readUsageRunCount(row);
    bucket.sortValue = Math.min(bucket.sortValue, sortValue);
  });

  return Array.from(buckets.values())
    .sort((left, right) => left.sortValue - right.sortValue)
    .slice(-maxPoints);
}
