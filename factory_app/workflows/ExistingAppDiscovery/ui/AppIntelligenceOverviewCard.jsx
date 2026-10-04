/**
 * AppIntelligenceOverviewCard — explore the app's prompt-safe catalog.
 * Source findings are evidence for discovery, not a working-app preview.
 */

import { useState } from 'react';
import { Boxes, Code2, Database, GitBranch, Info, Layers3 } from 'lucide-react';
import { Button } from '@mozaiks/chat-ui/ui';

const EMPTY_ARRAY = Object.freeze([]);
const EMPTY_OBJECT = Object.freeze({});

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
  return String(value || '').replace(/[_:.-]+/g, ' ').replace(/\b\w/g, (match) => match.toUpperCase());
}

function surfaceLabel(item) {
  const name = item.name || item.label || item.module_id || item.service_id || item.workflow_id || item.capability_id;
  if (name) return /\s/.test(name) ? name : labelize(name);
  const parts = String(item.path || item.root || '').split(/[\\/]/).filter(Boolean);
  const filename = parts.pop()?.replace(/\.[^.]+$/, '');
  const parent = parts.reverse().find((part) => !['app', 'backend', 'frontend', 'src', 'services', 'modules'].includes(part));
  return [parent, filename].filter(Boolean).map(labelize).join(' · ') || 'Indexed surface';
}

function EvidenceDetails({ value, label = 'Source details' }) {
  return (
    <details className="min-w-0 text-xs text-muted-foreground">
      <summary className="cursor-pointer py-2 font-medium hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">{label}</summary>
      <pre className="mt-1 max-h-72 overflow-auto whitespace-pre-wrap break-all rounded-lg bg-muted/45 p-3 font-mono text-[11px] leading-5">{JSON.stringify(value, null, 2)}</pre>
    </details>
  );
}

function SurfaceList({ title, items, emptyLabel = 'No matching source files were included in this scan.' }) {
  const [expanded, setExpanded] = useState(false);
  const all = asArray(items);
  const visible = expanded ? all : all.slice(0, 6);
  return (
    <section className="min-w-0 space-y-3">
      <h3 className="text-sm font-semibold text-foreground">{title}</h3>
      {visible.length ? (
        <div className="grid gap-3 sm:grid-cols-2">
          {visible.map((item, index) => (
            <article key={`${surfaceLabel(item)}-${index}`} className="min-w-0 rounded-xl border border-border/60 bg-background/35 p-4">
              <h4 className="break-words text-sm font-semibold text-foreground">{surfaceLabel(item)}</h4>
              {(item.description || item.kind || item.role) && <p className="mt-1 text-xs leading-5 text-muted-foreground">{item.description || labelize(item.kind || item.role)}</p>}
              <EvidenceDetails value={item} />
            </article>
          ))}
        </div>
      ) : <p className="rounded-xl bg-muted/30 p-4 text-sm leading-6 text-muted-foreground">{emptyLabel}</p>}
      {all.length > 6 && <Button variant="ghost" size="sm" onClick={() => setExpanded(!expanded)} aria-expanded={expanded}>{expanded ? 'Show fewer' : `Show all ${formatCount(all.length)}`}</Button>}
    </section>
  );
}

function ReviewNotes({ warnings, risks }) {
  return (
    <section className="space-y-4">
      <div>
        <h3 className="text-base font-semibold text-foreground">What needs a closer look</h3>
        <p className="mt-1 text-sm leading-6 text-muted-foreground">These findings describe the indexed source. Runtime behavior still needs validation.</p>
      </div>
      {!warnings.length && !risks.length && <p className="rounded-xl bg-muted/30 p-4 text-sm text-muted-foreground">No scan warnings were reported. This is not a security or readiness assessment.</p>}
      {risks.map((risk, index) => (
        <div key={risk.risk_id || index} className="rounded-xl border border-border/60 p-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h4 className="text-sm font-semibold text-foreground">{labelize(risk.risk_id || 'Source finding')}</h4>
          </div>
          {risk.description && <p className="mt-2 text-sm leading-6 text-muted-foreground">{risk.description}</p>}
        </div>
      ))}
      {warnings.length > 0 && (
        <div className="rounded-xl bg-warning/10 p-4">
          <h4 className="text-sm font-semibold text-foreground">Scan warnings</h4>
          <ul className="mt-2 list-disc space-y-2 break-words pl-4 text-xs leading-5 text-muted-foreground">{warnings.map((warning) => <li key={warning}>{warning}</li>)}</ul>
        </div>
      )}
    </section>
  );
}

export default function AppIntelligenceOverviewCard({ payload = {} }) {
  const catalog = asObject(payload.app_intelligence_catalog || payload.catalog);
  const coverage = asObject(catalog.coverage);
  const architecture = asObject(catalog.architecture);
  const health = asObject(payload.app_intelligence_health);
  const scanHealth = asObject(coverage.scan_health);
  const risks = asArray(catalog.risk_hints);
  const warnings = [...new Set([...asArray(payload.warnings), ...asArray(catalog.warnings), ...asArray(health.warnings), ...asArray(scanHealth.warnings)])];
  const scanLimited = warnings.some((warning) => /file_limit|scan.*limit|budget.*exhaust/i.test(String(warning)))
    || risks.some((risk) => risk.risk_id === 'source_scan_file_limit_reached')
    || scanHealth.limit_reached === true || asObject(health.coverage).limit_reached === true;
  const partial = scanLimited || payload.status === 'partial' || health.status === 'blocked' || !catalog.present;
  const displayName = payload.app_name || payload.repo_name || payload.github_repo || 'Your app';
  const summary = payload.analysis_summary || payload.discovery_brief || payload.summary;
  const features = asArray(payload.features).length ? payload.features : catalog.capabilities;
  const languages = Object.keys(asObject(coverage.language_counts));
  const frameworks = asArray(architecture.frameworks);
  const stack = asArray(payload.stack).length ? payload.stack : [...frameworks.map((item) => item.label), ...languages.map(labelize)];
  const [activeSection, setActiveSection] = useState('capabilities');
  const sections = [
    { id: 'capabilities', label: 'Capabilities', icon: Boxes },
    { id: 'architecture', label: 'Code structure', icon: Code2 },
    { id: 'connections', label: 'Connections & data', icon: Database },
    { id: 'review', label: 'Review notes', icon: Info },
  ];

  return (
    <div className="overflow-hidden rounded-2xl border border-border bg-card">
      <header className="border-b border-border/60 bg-gradient-to-br from-primary/10 via-card to-card p-5 sm:p-6">
        <div className="flex items-start gap-4">
          <div className="flex h-12 w-12 shrink-0 items-center justify-center rounded-xl border border-primary/20 bg-primary/10 text-primary"><Layers3 aria-hidden="true" size={23} /></div>
          <div className="min-w-0 flex-1">
            <p className="text-xs font-medium uppercase tracking-widest text-muted-foreground">App discovery</p>
            <h2 className="mt-1 break-words text-xl font-semibold tracking-tight text-foreground sm:text-2xl">{displayName}</h2>
            {payload.github_repo && payload.github_repo !== displayName && <p className="mt-1 flex items-start gap-1.5 break-all text-xs text-muted-foreground"><GitBranch aria-hidden="true" size={14} className="mt-0.5 shrink-0" />{payload.github_repo}</p>}
          </div>
        </div>
        <p className="mt-5 whitespace-pre-line text-sm leading-7 text-foreground/85">{summary || 'Explore the capabilities and code structure found in this repository. Product analysis has not been added to this overview yet.'}</p>
        {stack.length > 0 && (
          <div className="mt-4 flex flex-wrap gap-2" aria-label="Detected technologies">
            {[...new Set(stack)].filter(Boolean).map((label) => <span key={label} className="rounded-md border border-border/50 bg-background/40 px-2 py-1 text-xs text-muted-foreground">{label}</span>)}
          </div>
        )}
      </header>
      <div className="space-y-5 p-5 sm:p-6">
        {partial && (
          <div className="rounded-xl border border-warning/25 bg-warning/10 p-4" role="status">
            <p className="text-sm font-medium text-foreground">Partial analysis</p>
            <p className="mt-1 text-xs leading-5 text-muted-foreground">{scanLimited ? 'The scan reached its limit. Some parts of this app may be missing from this view.' : 'This overview has incomplete source coverage. Missing entries do not mean the app lacks those features.'}</p>
          </div>
        )}
        <nav className="grid grid-cols-2 gap-2" aria-label="Explore app discovery">
          {sections.map(({ id, label, icon: Icon }) => <Button key={id} variant={activeSection === id ? 'secondary' : 'ghost'} onClick={() => setActiveSection(id)} aria-pressed={activeSection === id} className="h-auto min-h-11 justify-start whitespace-normal px-3 py-3 text-left text-xs"><Icon aria-hidden="true" size={16} className="shrink-0" />{label}</Button>)}
        </nav>
        <div className="min-w-0 border-t border-border/60 pt-5">
          {activeSection === 'capabilities' && (
            <div className="space-y-4">
              <p className="text-sm leading-6 text-muted-foreground">Capabilities found in the source. Runtime behavior still needs validation.</p>
              <SurfaceList title="What’s in this app" items={features} emptyLabel="No capability boundaries were included in this scan. Explore Code structure or Review notes for the available evidence." />
            </div>
          )}
          {activeSection === 'architecture' && (
            <div className="space-y-6">
              <SurfaceList title="Modules" items={architecture.module_roots} />
              <SurfaceList title="Services" items={architecture.service_roots} />
              <SurfaceList title="Routes, pages & components" items={architecture.ui_surfaces} emptyLabel="No route, page or component files were included in this scan." />
              <SurfaceList title="Workflows" items={architecture.workflow_roots} />
            </div>
          )}
          {activeSection === 'connections' && <div className="space-y-6"><SurfaceList title="Integrations" items={catalog.integration_surfaces} /><SurfaceList title="Data" items={catalog.data_surfaces} /></div>}
          {activeSection === 'review' && <ReviewNotes warnings={warnings} risks={risks} />}
        </div>
        <div className="border-t border-border/60 pt-2">
          <EvidenceDetails label="Index & agent context details" value={{
            snapshot_id: catalog.snapshot_id || payload.app_intelligence_snapshot_id,
            current_app_context_version_id: payload.current_app_context_version_id,
            artifact_version_ids: payload.artifact_version_ids,
            catalog,
            progress: payload.app_intelligence_progress,
            source_summary: payload.app_intelligence_summary,
            health: payload.app_intelligence_health,
            agent_context_policy: catalog.agent_context_policy,
          }} />
        </div>
      </div>
    </div>
  );
}
