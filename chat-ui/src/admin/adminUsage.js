/**
 * Pure helpers for the admin portal's framework panels.
 *
 * Token and cost numbers come only from `GET /api/admin/usage` (the runtime
 * usage ledger). `GET /api/admin/stats`, `/runs`, and `/sessions` describe
 * conversation lifecycle and carry no token fields.
 */

// The usage endpoint totals the most recent model calls, up to this many.
export const USAGE_EVENT_LIMIT = 1000;
export const USAGE_ENDPOINT = `/api/admin/usage?limit=${USAGE_EVENT_LIMIT}`;

const PLACEHOLDER = '—';

function finiteNumber(value) {
  if (value === null || value === undefined || value === '') return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

export function formatCount(value) {
  const number = finiteNumber(value);
  return number === null ? PLACEHOLDER : number.toLocaleString();
}

export function formatCost(value) {
  const number = finiteNumber(value);
  if (number === null) return PLACEHOLDER;
  // A real cost too small for four decimals must not read as zero.
  if (number > 0 && number < 0.00005) return '<$0.0001';
  return `$${number.toFixed(number !== 0 && Math.abs(number) < 1 ? 4 : 2)}`;
}

export function formatDuration(seconds) {
  const number = finiteNumber(seconds);
  if (number === null) return PLACEHOLDER;
  if (number < 59.95) return `${number.toFixed(1)}s`;
  // Round once, then split, so 119.7 s reads "2m 0s" and never "1m 60s".
  const total = Math.round(number);
  const minutes = Math.floor(total / 60);
  if (minutes < 60) return `${minutes}m ${total % 60}s`;
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}

export function formatTimestamp(value) {
  if (!value) return PLACEHOLDER;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? PLACEHOLDER : date.toLocaleString();
}

/**
 * Session status as stored by the runtime: 0 in progress, 1 completed, 2 failed.
 * Anything else is still in progress as far as the runtime knows.
 */
export function sessionStatus(status) {
  if (status === 1) return { label: 'complete', variant: 'success' };
  if (status === 2) return { label: 'failed', variant: 'error' };
  return { label: 'in progress', variant: 'warning' };
}

// `cost_source` of a model call (or of a whole response) when no price is known.
const UNPRICED = 'not_configured';

/**
 * Whether the usage response carries a real cost. `cost_source` is
 * `not_configured` when no model in it has a price, so its cost of 0 is not a
 * measurement and must not be shown as "$0.00".
 */
export function isUsagePriced(usage) {
  return Boolean(usage) && typeof usage.cost_source === 'string' && usage.cost_source !== UNPRICED;
}

function usageEvents(usage) {
  return usage && Array.isArray(usage.events) ? usage.events : null;
}

/** How many of the totalled model calls have no price. */
function unpricedCallCount(usage) {
  const reported = finiteNumber(usage?.pricing_health?.unpriced_event_count);
  if (reported !== null) return reported;
  const events = usageEvents(usage) ?? [];
  return events.filter((event) => event && event.cost_source === UNPRICED).length;
}

/**
 * Totals from a `/api/admin/usage` response, with the wording needed to show
 * them honestly: which calls they cover, and whether the cost is priced.
 */
export function summarizeUsage(usage) {
  const totals = usage && typeof usage === 'object' ? usage.totals : null;
  if (!totals || typeof totals !== 'object') return null;

  const modelCalls = finiteNumber(totals.llm_calls) ?? 0;
  const promptTokens = finiteNumber(totals.prompt_tokens) ?? 0;
  const completionTokens = finiteNumber(totals.completion_tokens) ?? 0;
  const totalTokens = finiteNumber(totals.total_tokens) ?? promptTokens + completionTokens;
  const priced = isUsagePriced(usage);

  // The endpoint totals at most USAGE_EVENT_LIMIT calls. At the limit the
  // numbers are a window, not the app's lifetime total, and every card that
  // shows one says so.
  const capped = modelCalls >= USAGE_EVENT_LIMIT;
  const windowNote = capped
    ? `Latest ${USAGE_EVENT_LIMIT.toLocaleString()} model calls`
    : 'All recorded model calls';

  let costNote = 'Estimated from model prices';
  if (!priced) costNote = modelCalls > 0 ? 'No price is set for the models used' : 'No model calls yet';
  else if (unpricedCallCount(usage) > 0) costNote = 'Some models have no price set';
  if (capped) costNote = `${windowNote} only. ${costNote}`;

  return {
    modelCalls,
    promptTokens,
    completionTokens,
    totalTokens,
    cost: priced ? finiteNumber(totals.estimated_cost_usd) ?? 0 : null,
    costNote,
    capped,
    windowNote,
  };
}

/**
 * Per-run usage rows keyed by chat id, for joining onto the runs list.
 *
 * Each row gains `priced`: whether at least one of that run's model calls has
 * a price. A run that used only unpriced models has a cost of 0 that is not a
 * measurement, so its cost must not be shown as "$0.00". The per-call
 * `cost_source` comes from `events`, which cover the same calls as `by_run`.
 */
export function usageByChatId(usage) {
  const rows = usage && Array.isArray(usage.by_run) ? usage.by_run : [];
  const events = usageEvents(usage);
  const pricedChats = new Set();
  (events ?? []).forEach((event) => {
    if (event && typeof event.chat_id === 'string' && typeof event.cost_source === 'string' && event.cost_source !== UNPRICED) {
      pricedChats.add(event.chat_id);
    }
  });
  // Without per-call detail, fall back to what the whole response says.
  const responsePriced = isUsagePriced(usage);
  const byChat = new Map();
  rows.forEach((row) => {
    if (!row || typeof row.chat_id !== 'string' || !row.chat_id) return;
    byChat.set(row.chat_id, { ...row, priced: events ? pricedChats.has(row.chat_id) : responsePriced });
  });
  return byChat;
}
