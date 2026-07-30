/**
 * AppIntelligenceInlineBrief - compact App Intelligence result in the chat feed.
 *
 * This is the durable transcript companion to AppIntelligenceOverviewCard. It
 * shows the prompt-safe context summary without exposing raw source contents.
 */

import {
  AlertTriangle,
  CheckCircle2,
  FileCode2,
  GitBranch,
  Network,
} from 'lucide-react';

const EMPTY_ARRAY = Object.freeze([]);
const EMPTY_OBJECT = Object.freeze({});

const STATUS_STYLES = {
  ready: 'border-success/25 bg-success/10 text-success',
  partial: 'border-warning/30 bg-warning/10 text-warning',
  none: 'border-border bg-muted/45 text-muted-foreground',
};

function asArray(value) {
  return Array.isArray(value) ? value : EMPTY_ARRAY;
}

function asObject(value) {
  return value && typeof value === 'object' && !Array.isArray(value) ? value : EMPTY_OBJECT;
}

function formatCount(value) {
  const number = Number(value || 0);
  return Number.isFinite(number) ? number.toLocaleString() : '0';
}

function labelize(value) {
  return String(value || '')
    .replace(/[_-]+/g, ' ')
    .replace(/\b\w/g, (match) => match.toUpperCase());
}

function splitTechStack(value) {
  if (Array.isArray(value)) return value.filter(Boolean).slice(0, 6);
  return String(value || '')
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean)
    .slice(0, 6);
}

function firstLabel(item) {
  if (typeof item === 'string') return item;
  if (!item || typeof item !== 'object') return '';
  return (
    item.label ||
    item.capability_id ||
    item.module_id ||
    item.service_id ||
    item.workflow_id ||
    item.path ||
    item.root ||
    ''
  );
}

function CountItem({ icon: Icon, label, value }) {
  return (
    <div className="min-w-0 border-l border-border/60 pl-3 first:border-l-0 first:pl-0">
      <div className="flex items-center gap-1.5 text-muted-foreground">
        <Icon className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
        <span className="truncate text-[11px] font-medium">{label}</span>
      </div>
      <p className="mt-1 text-base font-semibold tabular-nums text-foreground">{formatCount(value)}</p>
    </div>
  );
}

function ChipRow({ label, items, emptyLabel }) {
  const visible = asArray(items).map(firstLabel).filter(Boolean).slice(0, 5);
  if (!visible.length && !emptyLabel) return null;
  return (
    <div className="min-w-0">
      <p className="text-[11px] font-medium text-muted-foreground">{label}</p>
      {visible.length > 0 ? (
        <div className="mt-1.5 flex flex-wrap gap-1.5">
          {visible.map((item) => (
            <span
              key={item}
              className="max-w-full truncate rounded-md border border-border/55 bg-background/70 px-2 py-1 text-[11px] text-foreground"
            >
              {labelize(item)}
            </span>
          ))}
        </div>
      ) : (
        <p className="mt-1.5 text-xs text-muted-foreground">{emptyLabel}</p>
      )}
    </div>
  );
}

export default function AppIntelligenceInlineBrief({ payload = {} }) {
  const coverage = asObject(payload.coverage);
  const architecture = asObject(payload.architecture);
  const status = String(payload.status || 'partial');
  const statusStyle = STATUS_STYLES[status] || STATUS_STYLES.partial;
  const appName = payload.app_name || payload.repo_name || payload.github_repo || 'Indexed app';
  const techStack = splitTechStack(payload.tech_stack || payload.frameworks);
  const warnings = asArray(payload.warnings);
  const risks = asArray(payload.risk_hints);
  const capabilities = asArray(payload.capabilities);
  const sourceRefs = asArray(architecture.source_refs);
  const graphEdges = coverage.edge_count || coverage.relationship_count || 0;
  const healthWarningCount = warnings.length + risks.length;

  return (
    <div className="w-full overflow-hidden rounded-lg border border-border bg-card text-card-foreground shadow-sm">
      <div className="border-b border-border bg-muted/20 px-4 py-3">
        <div className="flex min-w-0 items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <span className="inline-flex items-center gap-1.5 text-xs font-semibold uppercase text-muted-foreground">
                <Network className="h-3.5 w-3.5" aria-hidden="true" />
                App Intelligence
              </span>
              <span className={`rounded-full border px-2 py-0.5 text-[11px] font-medium ${statusStyle}`}>
                {labelize(status)}
              </span>
            </div>
            <p className="mt-1 truncate text-sm font-semibold text-foreground">{appName}</p>
            {payload.github_repo && (
              <p className="mt-0.5 truncate font-mono text-[11px] text-muted-foreground">{payload.github_repo}</p>
            )}
          </div>
          <div className="shrink-0 pt-0.5">
            {status === 'ready' ? (
              <CheckCircle2 className="h-5 w-5 text-success" aria-label="Ready" />
            ) : (
              <AlertTriangle className="h-5 w-5 text-warning" aria-label="Partial" />
            )}
          </div>
        </div>
      </div>

      <div className="space-y-3 px-4 py-3">
        <div className="grid grid-cols-4 gap-3">
          <CountItem icon={FileCode2} label="Files" value={coverage.file_count || payload.total_files_scanned} />
          <CountItem icon={GitBranch} label="Symbols" value={coverage.symbol_count} />
          <CountItem icon={Network} label="Links" value={graphEdges} />
          <CountItem icon={AlertTriangle} label="Flags" value={healthWarningCount} />
        </div>

        {payload.summary && (
          <p className="text-xs leading-5 text-foreground/85">{payload.summary}</p>
        )}

        <div className="grid gap-3 sm:grid-cols-2">
          <ChipRow label="Tech stack" items={techStack} emptyLabel="No framework signals detected yet." />
          <ChipRow label="Likely code surfaces" items={sourceRefs} emptyLabel="No source surfaces indexed yet." />
        </div>

        {capabilities.length > 0 && (
          <ChipRow label="Capability signals" items={capabilities} />
        )}

        {(payload.integration_count > 0 || payload.data_surface_count > 0) && (
          <div className="flex flex-wrap gap-2 text-[11px] text-muted-foreground">
            <span>Integrations: {formatCount(payload.integration_count)}</span>
            <span>Data surfaces: {formatCount(payload.data_surface_count)}</span>
          </div>
        )}

        {warnings.length > 0 && (
          <div className="rounded-md border border-warning/25 bg-warning/10 px-3 py-2">
            <p className="text-[11px] font-medium text-warning">Indexing warnings</p>
            <p className="mt-1 line-clamp-2 text-[11px] leading-5 text-foreground/80">
              {warnings.slice(0, 3).join(' | ')}
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
