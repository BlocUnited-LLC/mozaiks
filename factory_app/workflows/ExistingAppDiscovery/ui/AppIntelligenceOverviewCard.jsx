/**
 * AppIntelligenceOverviewCard — compact repo context visible to agents.
 *
 * Emitted after deterministic App Intelligence indexing completes. The card
 * summarizes the prompt-safe catalog; exact source remains behind retrieval
 * tools and is not rendered here.
 */

import { useMemo, useState } from 'react';

const EMPTY_ARRAY = Object.freeze([]);
const EMPTY_OBJECT = Object.freeze({});

const STATUS_COLORS = {
  ready: 'bg-success/15 text-success border-success/25',
  partial: 'bg-warning/15 text-warning border-warning/25',
  none: 'bg-muted text-muted-foreground border-border',
};

const RISK_COLORS = {
  high: 'border-destructive/35 bg-destructive/10 text-destructive',
  medium: 'border-warning/35 bg-warning/10 text-warning',
  low: 'border-border bg-muted/45 text-muted-foreground',
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

function topCountItems(counts, limit = 5) {
  return Object.entries(asObject(counts))
    .sort((left, right) => Number(right[1] || 0) - Number(left[1] || 0))
    .slice(0, limit)
    .map(([label, value]) => ({ label, value }));
}

function surfaceLabel(item) {
  return (
    item.label ||
    item.module_id ||
    item.service_id ||
    item.workflow_id ||
    item.path ||
    item.root ||
    item.capability_id ||
    'Surface'
  );
}

function surfaceDetail(item) {
  if (item.path) return item.path;
  if (Array.isArray(item.paths) && item.paths.length > 0) return item.paths[0];
  if (item.role) return `${item.role}${item.language ? ` / ${item.language}` : ''}`;
  if (item.kind) return item.kind;
  return '';
}

function Metric({ label, value, detail }) {
  return (
    <div className="min-h-[5.25rem] rounded-lg border border-border/55 bg-background/70 px-3 py-2">
      <p className="text-xl font-semibold tabular-nums text-foreground">{formatCount(value)}</p>
      <p className="mt-0.5 text-xs font-medium text-muted-foreground">{label}</p>
      {detail && <p className="mt-1 truncate text-[11px] text-muted-foreground/75">{detail}</p>}
    </div>
  );
}

function CountStrip({ title, items }) {
  if (!items.length) return null;
  return (
    <div className="space-y-1.5">
      <p className="text-xs font-medium text-muted-foreground">{title}</p>
      <div className="flex flex-wrap gap-1.5">
        {items.map((item) => (
          <span
            key={item.label}
            className="rounded-md border border-border/55 bg-background/75 px-2 py-1 text-xs text-foreground"
          >
            {labelize(item.label)} <span className="text-muted-foreground">{formatCount(item.value)}</span>
          </span>
        ))}
      </div>
    </div>
  );
}

function SurfaceList({ title, items, emptyLabel = 'No surfaces indexed.' }) {
  const visible = asArray(items).slice(0, 6);
  return (
    <div className="min-w-0 space-y-2">
      <div className="flex items-center justify-between gap-2">
        <p className="text-xs font-medium text-muted-foreground">{title}</p>
        {asArray(items).length > visible.length && (
          <span className="text-[11px] text-muted-foreground">+{asArray(items).length - visible.length}</span>
        )}
      </div>
      {visible.length > 0 ? (
        <div className="grid gap-1.5">
          {visible.map((item, index) => (
            <div
              key={`${surfaceLabel(item)}-${index}`}
              className="min-w-0 rounded-lg border border-border/50 bg-background/60 px-3 py-2"
            >
              <p className="truncate text-xs font-medium text-foreground">{surfaceLabel(item)}</p>
              {surfaceDetail(item) && (
                <p className="mt-0.5 truncate font-mono text-[11px] text-muted-foreground">
                  {surfaceDetail(item)}
                </p>
              )}
            </div>
          ))}
        </div>
      ) : (
        <p className="rounded-lg border border-border/45 bg-muted/30 px-3 py-2 text-xs text-muted-foreground">
          {emptyLabel}
        </p>
      )}
    </div>
  );
}

function RiskList({ risks }) {
  const visible = asArray(risks).slice(0, 5);
  if (!visible.length) {
    return (
      <p className="rounded-lg border border-success/25 bg-success/10 px-3 py-2 text-xs text-success">
        No App Intelligence risk hints were raised by the deterministic scan.
      </p>
    );
  }
  return (
    <div className="grid gap-1.5">
      {visible.map((risk, index) => {
        const severity = String(risk.severity || 'low');
        return (
          <div
            key={`${risk.risk_id || index}`}
            className={`rounded-lg border px-3 py-2 ${RISK_COLORS[severity] || RISK_COLORS.low}`}
          >
            <div className="flex items-center justify-between gap-3">
              <p className="truncate text-xs font-medium">{labelize(risk.risk_id || 'risk')}</p>
              <span className="shrink-0 text-[11px]">{labelize(severity)}</span>
            </div>
            {risk.description && (
              <p className="mt-1 text-[11px] leading-5 text-foreground/80">{risk.description}</p>
            )}
          </div>
        );
      })}
    </div>
  );
}

function AgentPolicy({ policy }) {
  const resolved = asObject(policy);
  const surfaces = asArray(resolved.surfaces).slice(0, 4);
  const authority = asObject(resolved.authority);
  return (
    <div className="space-y-3">
      <div className="rounded-lg border border-border/50 bg-background/65 px-3 py-2">
        <p className="text-xs font-medium text-foreground">{labelize(resolved.policy || 'retrieve_not_dump')}</p>
        <p className="mt-1 text-[11px] leading-5 text-muted-foreground">
          Summary from App Intelligence, relationships from Context Graph, exact code through source retrieval.
        </p>
      </div>
      <div className="grid gap-2 md:grid-cols-3">
        {Object.entries(authority).map(([key, value]) => (
          <div key={key} className="rounded-lg border border-border/45 bg-muted/30 px-3 py-2">
            <p className="text-[11px] font-medium text-muted-foreground">{labelize(key)}</p>
            <p className="mt-0.5 truncate text-xs text-foreground">{String(value || '')}</p>
          </div>
        ))}
      </div>
      <SurfaceList title="Workflow context surfaces" items={surfaces} emptyLabel="No policy surfaces declared." />
    </div>
  );
}

export default function AppIntelligenceOverviewCard({ payload = {} }) {
  const catalog = asObject(payload.app_intelligence_catalog || payload.catalog);
  const coverage = asObject(catalog.coverage);
  const architecture = asObject(catalog.architecture);
  const policy = asObject(catalog.agent_context_policy);
  const status = payload.status || (catalog.present ? 'ready' : 'partial');
  const statusColor = STATUS_COLORS[status] || STATUS_COLORS.partial;
  const displayName = payload.app_name || payload.repo_name || payload.github_repo || catalog.app_id || 'Indexed app';
  const warnings = asArray(payload.warnings).length ? asArray(payload.warnings) : asArray(catalog.warnings);
  const [activeTab, setActiveTab] = useState('architecture');

  const counts = useMemo(() => ({
    languages: topCountItems(coverage.language_counts),
    roles: topCountItems(coverage.role_counts),
  }), [coverage.language_counts, coverage.role_counts]);

  const tabs = [
    {
      id: 'architecture',
      label: 'Architecture',
      count:
        asArray(architecture.module_roots).length +
        asArray(architecture.service_roots).length +
        asArray(architecture.ui_surfaces).length,
    },
    { id: 'capabilities', label: 'Capabilities', count: asArray(catalog.capabilities).length },
    {
      id: 'surfaces',
      label: 'Integrations & Data',
      count: asArray(catalog.integration_surfaces).length + asArray(catalog.data_surfaces).length,
    },
    { id: 'policy', label: 'Agent Context', count: asArray(policy.surfaces).length },
  ];

  return (
    <div className="overflow-hidden rounded-lg border border-border bg-card">
      <div className="border-b border-border bg-muted/25 px-4 py-3">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-xs font-medium uppercase text-muted-foreground">App Intelligence</span>
              <span className={`rounded-full border px-2 py-0.5 text-xs font-medium ${statusColor}`}>
                {labelize(status)}
              </span>
            </div>
            <p className="mt-1 truncate text-sm font-semibold text-foreground">{displayName}</p>
            {payload.github_repo && (
              <p className="mt-0.5 truncate font-mono text-xs text-muted-foreground">{payload.github_repo}</p>
            )}
          </div>
          <div className="shrink-0 rounded-lg border border-border/55 bg-background/70 px-3 py-2 text-xs text-muted-foreground">
            {catalog.snapshot_id || payload.app_intelligence_snapshot_id || 'snapshot pending'}
          </div>
        </div>
      </div>

      <div className="space-y-4 p-4">
        <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
          <Metric label="Files" value={coverage.file_count || payload.total_files_scanned} />
          <Metric label="Symbols" value={coverage.symbol_count} />
          <Metric label="Graph Nodes" value={coverage.node_count} />
          <Metric label="Graph Edges" value={coverage.edge_count} />
        </div>

        <div className="grid gap-3 md:grid-cols-2">
          <CountStrip title="Languages" items={counts.languages} />
          <CountStrip title="File roles" items={counts.roles} />
        </div>

        <div className="flex gap-1 overflow-x-auto rounded-lg border border-border/45 bg-background/55 p-1">
          {tabs.map((tab) => (
            <button
              key={tab.id}
              type="button"
              onClick={() => setActiveTab(tab.id)}
              className={[
                'shrink-0 rounded-md px-3 py-1.5 text-xs font-medium transition-colors',
                activeTab === tab.id
                  ? 'bg-primary text-primary-foreground'
                  : 'text-muted-foreground hover:bg-muted hover:text-foreground',
              ].join(' ')}
            >
              {tab.label}
              <span className="ml-1 opacity-75">{formatCount(tab.count)}</span>
            </button>
          ))}
        </div>

        {activeTab === 'architecture' && (
          <div className="grid gap-3 md:grid-cols-2">
            <SurfaceList title="Module roots" items={architecture.module_roots} />
            <SurfaceList title="Service roots" items={architecture.service_roots} />
            <SurfaceList title="UI surfaces" items={architecture.ui_surfaces} />
            <SurfaceList title="Workflow roots" items={architecture.workflow_roots} />
          </div>
        )}

        {activeTab === 'capabilities' && (
          <SurfaceList title="Capability boundaries" items={catalog.capabilities} emptyLabel="No capability boundaries indexed yet." />
        )}

        {activeTab === 'surfaces' && (
          <div className="grid gap-3 md:grid-cols-2">
            <SurfaceList title="Integration surfaces" items={catalog.integration_surfaces} />
            <SurfaceList title="Data surfaces" items={catalog.data_surfaces} />
          </div>
        )}

        {activeTab === 'policy' && <AgentPolicy policy={policy} />}

        <RiskList risks={catalog.risk_hints} />

        {warnings.length > 0 && (
          <div className="rounded-lg border border-warning/25 bg-warning/10 px-3 py-2">
            <p className="text-xs font-medium text-warning">Indexing warnings</p>
            <p className="mt-1 line-clamp-2 text-[11px] leading-5 text-foreground/80">
              {warnings.slice(0, 4).join(' | ')}
            </p>
          </div>
        )}
      </div>

      <div className="border-t border-border bg-muted/15 px-4 py-2">
        <p className="text-xs text-muted-foreground">
          Agents receive this compact context first and retrieve exact files only when a task needs code evidence.
        </p>
      </div>
    </div>
  );
}
