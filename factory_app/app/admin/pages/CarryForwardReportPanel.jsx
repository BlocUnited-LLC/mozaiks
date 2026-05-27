import { useState } from 'react'

import { Panel, StatusPill } from '../../ui/components/StudioShared.jsx'
import { _isSensitivePath, _sanitizePaths } from './_carry_forward_redact.js'

function _sanitizeKeys(obj) {
  if (!obj || typeof obj !== 'object') return {}
  return Object.fromEntries(
    Object.keys(obj).map((k) => [_isSensitivePath(k) ? '[redacted]' : k, obj[k]]),
  )
}

// ---------------------------------------------------------------------------
// Sub-components
// ---------------------------------------------------------------------------

function ModuleTagList({ ids, emptyText = 'None' }) {
  if (!Array.isArray(ids) || ids.length === 0) {
    return <span className="text-[12px] text-muted-foreground">{emptyText}</span>
  }
  return (
    <div className="flex flex-wrap gap-1.5">
      {ids.map((id) => (
        <StatusPill key={id} tone="default">{id}</StatusPill>
      ))}
    </div>
  )
}

function ExpandablePathList({ paths, label }) {
  const [expanded, setExpanded] = useState(false)
  const safe = _sanitizePaths(paths)
  if (!safe.length) return null
  const shown = expanded ? safe : safe.slice(0, 3)
  const hasMore = safe.length > 3

  return (
    <div>
      <button
        type="button"
        className="text-[12px] font-medium text-primary/80 hover:text-primary"
        onClick={() => setExpanded((e) => !e)}
        aria-expanded={expanded}
      >
        {label} ({safe.length}){' '}
        <span aria-hidden="true">{expanded ? '▲' : '▼'}</span>
      </button>
      {expanded && (
        <ul className="mt-2 space-y-1" role="list">
          {shown.map((p, i) => (
            <li
              key={`${p}-${i}`}
              className="rounded-lg border border-border/40 bg-muted/18 px-2.5 py-1 font-mono text-[11px] text-muted-foreground"
            >
              {p}
            </li>
          ))}
          {hasMore && !expanded && (
            <li className="text-[11px] text-muted-foreground">
              +{safe.length - 3} more…
            </li>
          )}
        </ul>
      )}
    </div>
  )
}

function CountTile({ value, label, highlight = false }) {
  return (
    <div className="rounded-2xl border border-border/42 bg-card/30 px-4 py-3 text-center">
      <div
        className={`text-2xl font-semibold ${highlight && value > 0 ? 'text-warning' : 'text-foreground'}`}
      >
        {value}
      </div>
      <div className="mt-1 text-[11px] font-medium text-muted-foreground">{label}</div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Public export
// ---------------------------------------------------------------------------

/**
 * CarryForwardReportPanel
 *
 * Renders an operator-visible audit summary of the Phase 7A carry-forward
 * preservation step. Displays which declarative module contract files were
 * preserved from the prior app bundle, which were overwritten by generated
 * output, and which modules were dropped or regenerated.
 *
 * Props:
 *   report — the carry_forward_report object from
 *             artifact.commit_metadata.metadata.carry_forward_report
 *
 * Returns null when report is absent or not an object — callers do not need
 * to guard before rendering this component.
 *
 * Note: backend Python source is never carried forward in Phase 7A. This
 * component is an audit trail only and does not affect build behavior.
 */
export default function CarryForwardReportPanel({ report }) {
  if (!report || typeof report !== 'object') return null

  const {
    previous_app_bundle_ref,
    workspace_available,
    workspace_source,
    preserved_paths = [],
    conflicts = {},
    skipped_paths = {},
    reused_modules = [],
    adapted_modules = [],
    regenerated_modules = [],
    dropped_modules = [],
    warnings = [],
  } = report

  const conflictKeys = Object.keys(_sanitizeKeys(conflicts))
  const skippedKeys = Object.keys(_sanitizeKeys(skipped_paths))
  const safePaths = _sanitizePaths(preserved_paths)

  return (
    <Panel
      title="Carry-forward preservation"
      subtitle="Declarative module contracts preserved from the prior app bundle. Backend code was not copied."
    >
      <div className="space-y-4">

        {/* Workspace unavailable warning */}
        {!workspace_available && (
          <div className="rounded-2xl border border-warning/28 bg-warning/8 px-4 py-3 text-sm text-warning">
            Prior workspace was not available. No files were preserved. Build
            proceeded from generated output only.
          </div>
        )}

        {/* Diagnostic warnings */}
        {warnings.length > 0 && (
          <div className="space-y-1.5">
            {warnings.map((w, i) => (
              <div
                key={i}
                className="rounded-2xl border border-warning/28 bg-warning/8 px-4 py-2 text-[12px] text-warning"
              >
                {w}
              </div>
            ))}
          </div>
        )}

        {/* Source artifact reference */}
        {previous_app_bundle_ref && (
          <div className="rounded-2xl border border-border/42 bg-card/30 px-4 py-3">
            <div className="text-[12px] font-medium text-muted-foreground/82">
              Source artifact version
            </div>
            <div className="mt-1 font-mono text-sm text-foreground">
              {previous_app_bundle_ref}
            </div>
            {workspace_source && (
              <div className="mt-1 text-[11px] text-muted-foreground">
                Loaded from: {workspace_source}
              </div>
            )}
          </div>
        )}

        {/* Module outcomes grid */}
        {workspace_available && (
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="rounded-2xl border border-border/42 bg-card/30 px-4 py-3">
              <div className="mb-2 text-[12px] font-medium text-muted-foreground/82">
                Reused modules
              </div>
              <ModuleTagList ids={reused_modules} emptyText="None" />
            </div>
            <div className="rounded-2xl border border-border/42 bg-card/30 px-4 py-3">
              <div className="mb-2 text-[12px] font-medium text-muted-foreground/82">
                Dropped prior modules
              </div>
              <ModuleTagList ids={dropped_modules} emptyText="None" />
            </div>
            {adapted_modules.length > 0 && (
              <div className="rounded-2xl border border-border/42 bg-card/30 px-4 py-3">
                <div className="mb-2 text-[12px] font-medium text-muted-foreground/82">
                  Adapted modules
                </div>
                <div className="mb-1">
                  <ModuleTagList ids={adapted_modules} />
                </div>
                <div className="text-[11px] text-muted-foreground">
                  Adapt/regenerate decisions did not copy files.
                </div>
              </div>
            )}
            {regenerated_modules.length > 0 && (
              <div className="rounded-2xl border border-border/42 bg-card/30 px-4 py-3">
                <div className="mb-2 text-[12px] font-medium text-muted-foreground/82">
                  Regenerated modules
                </div>
                <div className="mb-1">
                  <ModuleTagList ids={regenerated_modules} />
                </div>
                <div className="text-[11px] text-muted-foreground">
                  Adapt/regenerate decisions did not copy files.
                </div>
              </div>
            )}
          </div>
        )}

        {/* Counts summary */}
        {workspace_available && (
          <div className="grid gap-3 sm:grid-cols-3">
            <CountTile
              value={safePaths.length}
              label="Preserved declarative contracts"
            />
            <CountTile
              value={conflictKeys.length}
              label="Generated output overwrote preserved candidate"
              highlight
            />
            <CountTile
              value={skippedKeys.length}
              label="Skipped paths"
            />
          </div>
        )}

        {/* Expandable path detail */}
        {safePaths.length > 0 && (
          <ExpandablePathList paths={safePaths} label="Preserved paths" />
        )}
        {conflictKeys.length > 0 && (
          <div className="space-y-1.5">
            <ExpandablePathList paths={conflictKeys} label="Conflict paths" />
            <div className="text-[11px] text-muted-foreground">
              Generated output overwrote preserved candidates at these paths.
            </div>
          </div>
        )}

        {/* Permanent notice — backend not copied */}
        <div className="rounded-2xl border border-border/36 bg-background/28 px-4 py-3 text-[12px] text-muted-foreground">
          Only module.yaml and contracts/*.yaml files are eligible for
          preservation. Backend code was not copied. runtime_extensions.yaml,
          custom React, and database intent files are never preserved.
        </div>

      </div>
    </Panel>
  )
}
