import React from 'react';

const COMPLETE_STATUSES = new Set(['complete', 'completed', 'ready', 'success', 'succeeded', 'done']);
const FAILED_STATUSES = new Set(['failed', 'error', 'unavailable']);

const asObject = (value) => (
  value && typeof value === 'object' && !Array.isArray(value) ? value : {}
);

const titleCase = (value) => (
  String(value || 'status')
    .replace(/[_-]+/g, ' ')
    .replace(/\b\w/g, (match) => match.toUpperCase())
);

const clampPercent = (value) => {
  const number = Number(value);
  if (!Number.isFinite(number)) return null;
  return Math.max(0, Math.min(100, Math.round(number)));
};

export default function SystemStatusCard({ payload = {} }) {
  const progress = asObject(payload.progress);
  const status = String(
    payload.status
    || progress.status
    || 'working'
  ).trim().toLowerCase();
  const stage = String(payload.progress_stage || progress.stage || status).trim();
  const percent = clampPercent(payload.progress_percent ?? progress.percent);
  const agent = String(payload.agent || payload.agent_name || 'System').trim();
  const message = String(
    payload.message
    || progress.message
    || `${agent} is working.`
  ).trim();

  const tone = COMPLETE_STATUSES.has(status)
    ? 'success'
    : FAILED_STATUSES.has(status)
      ? 'destructive'
      : 'primary';
  const badgeClass = {
    success: 'border-success/35 bg-success/10 text-success',
    destructive: 'border-destructive/35 bg-destructive/10 text-destructive',
    primary: 'border-primary/35 bg-primary/10 text-primary',
  }[tone];
  const barClass = {
    success: 'bg-success',
    destructive: 'bg-destructive',
    primary: 'bg-primary',
  }[tone];

  return (
    <div
      className="w-full max-w-[34rem] rounded-lg border border-border bg-card/85 px-4 py-3 text-card-foreground shadow-sm"
      role="status"
      aria-live="polite"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs font-semibold uppercase text-muted-foreground">{agent}</span>
            <span className={`rounded-full border px-2 py-0.5 text-[11px] font-medium ${badgeClass}`}>
              {titleCase(status)}
            </span>
          </div>
          <p className="mt-1 text-sm font-semibold text-foreground">{message}</p>
          <p className="mt-0.5 text-xs leading-5 text-muted-foreground">{titleCase(stage)}</p>
        </div>
        {percent !== null && (
          <span className="shrink-0 rounded-md border border-border/60 bg-background/70 px-2.5 py-1 text-xs font-semibold text-foreground">
            {percent}%
          </span>
        )}
      </div>
      {percent !== null && (
        <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-muted">
          <div className={`h-full rounded-full transition-all duration-700 ${barClass}`} style={{ width: `${percent}%` }} />
        </div>
      )}
    </div>
  );
}
