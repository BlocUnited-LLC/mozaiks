import { useState } from 'react'

import { StatusPill } from '../../ui/components/StudioShared.jsx'
import { _isSensitivePath, _sanitizePaths } from './_carry_forward_redact.js'

// ---------------------------------------------------------------------------
// CarryForwardReportSummary
//
// Compact inline carry-forward audit summary for use in build/revision
// history entries. Shows module lists and key counts without the full
// Panel wrapper used by CarryForwardReportPanel on the overview page.
//
// Props:
//   report — the carry_forward_report object from
//             artifact.commit_metadata.metadata.carry_forward_report
//
// Returns null when report is absent or not an object — callers do not need
// to guard before rendering this component.
// ---------------------------------------------------------------------------

export default function CarryForwardReportSummary({ report }) {
  const [expanded, setExpanded] = useState(false)

  if (!report || typeof report !== 'object') return null

  const {
    workspace_available,
    preserved_paths = [],
    conflicts = {},
    skipped_paths = {},
    reused_modules = [],
    adapted_modules = [],
    regenerated_modules = [],
    dropped_modules = [],
    warnings = [],
  } = report

  const safePaths = _sanitizePaths(preserved_paths)
  const conflictCount = Object.keys(conflicts || {}).length
  const warningCount = (warnings || []).length

  return (
    <div className="mt-2 rounded-xl border border-border/36 bg-background/28 px-3 py-2 text-[11px]">
      <button
        type="button"
        className="flex w-full items-center justify-between gap-2 text-left"
        onClick={() => setExpanded((e) => !e)}
        aria-expanded={expanded}
      >
        <span className="flex flex-wrap items-center gap-2">
          <span className="font-medium text-muted-foreground">Carry-forward</span>

          {!workspace_available && (
            <StatusPill tone="warning">workspace unavailable</StatusPill>
          )}

          {reused_modules.length > 0 && (
            <span className="text-muted-foreground">
              {reused_modules.length} reused
            </span>
          )}
          {dropped_modules.length > 0 && (
            <span className="text-muted-foreground">
              {dropped_modules.length} dropped
            </span>
          )}
          {safePaths.length > 0 && (
            <span className="text-muted-foreground">
              {safePaths.length} paths preserved
            </span>
          )}
          {conflictCount > 0 && (
            <StatusPill tone="warning">{conflictCount} conflict{conflictCount !== 1 ? 's' : ''}</StatusPill>
          )}
          {warningCount > 0 && (
            <StatusPill tone="warning">{warningCount} warning{warningCount !== 1 ? 's' : ''}</StatusPill>
          )}
        </span>
        <span className="shrink-0 text-muted-foreground/60" aria-hidden="true">
          {expanded ? '▲' : '▼'}
        </span>
      </button>

      {expanded && (
        <div className="mt-2 space-y-2 border-t border-border/28 pt-2">

          {!workspace_available && (
            <div className="rounded-lg border border-warning/28 bg-warning/8 px-2.5 py-1.5 text-[11px] text-warning">
              Prior workspace was not available. No files were preserved.
            </div>
          )}

          {/* Module lists */}
          <div className="flex flex-wrap gap-x-4 gap-y-1.5">
            {reused_modules.length > 0 && (
              <div>
                <span className="mr-1 text-muted-foreground/60">Reused:</span>
                {reused_modules.map((id) => (
                  <span key={id} className="mr-1">
                    <StatusPill tone="success">{id}</StatusPill>
                  </span>
                ))}
              </div>
            )}
            {dropped_modules.length > 0 && (
              <div>
                <span className="mr-1 text-muted-foreground/60">Dropped:</span>
                {dropped_modules.map((id) => (
                  <span key={id} className="mr-1">
                    <StatusPill tone="default">{id}</StatusPill>
                  </span>
                ))}
              </div>
            )}
            {adapted_modules.length > 0 && (
              <div>
                <span className="mr-1 text-muted-foreground/60">Adapted:</span>
                {adapted_modules.map((id) => (
                  <span key={id} className="mr-1">
                    <StatusPill tone="primary">{id}</StatusPill>
                  </span>
                ))}
              </div>
            )}
            {regenerated_modules.length > 0 && (
              <div>
                <span className="mr-1 text-muted-foreground/60">Regenerated:</span>
                {regenerated_modules.map((id) => (
                  <span key={id} className="mr-1">
                    <StatusPill tone="default">{id}</StatusPill>
                  </span>
                ))}
              </div>
            )}
          </div>

          {/* Counts */}
          <div className="flex flex-wrap gap-3 text-muted-foreground">
            <span>{safePaths.length} paths preserved</span>
            {conflictCount > 0 && (
              <span className="text-warning">
                {conflictCount} conflict{conflictCount !== 1 ? 's' : ''} — generated output wins
              </span>
            )}
            {Object.keys(skipped_paths || {}).length > 0 && (
              <span>{Object.keys(skipped_paths).length} paths skipped</span>
            )}
          </div>

          {/* Permanent safety notice */}
          <div className="text-muted-foreground/60">
            Only module.yaml and contracts/*.yaml preserved. Backend code was not copied.
          </div>

          {/* Diagnostic warnings */}
          {warningCount > 0 && (
            <div className="space-y-1">
              {warnings.map((w, i) => (
                <div key={i} className="rounded-lg border border-warning/28 bg-warning/8 px-2.5 py-1 text-[11px] text-warning">
                  {w}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
