import React from "react";

const STAGES = [
  {
    id: "resolving_sources",
    label: "Connect to codebase",
    description: "Checking repository access, branch, and workspace inputs.",
  },
  {
    id: "collecting_evidence",
    label: "Read app signals",
    description: "Looking at manifests, repo metadata, and existing context.",
  },
  {
    id: "selecting_source_files",
    label: "Choose safe files",
    description: "Selecting source files while skipping secrets and oversized paths.",
  },
  {
    id: "fetching_source_files",
    label: "Load source files",
    description: "Reading the selected code needed for App Intelligence.",
  },
  {
    id: "extracting_symbols",
    label: "Parse code structure",
    description: "Extracting routes, modules, functions, imports, and symbols.",
  },
  {
    id: "building_snapshot",
    label: "Build relationship graph",
    description: "Connecting files, symbols, modules, services, and surfaces.",
  },
  {
    id: "ready",
    label: "Prepare agent brief",
    description: "Packaging the compact context the discovery agent will use.",
  },
];

const STAGE_INDEX = STAGES.reduce((acc, stage, index) => {
  acc[stage.id] = index;
  return acc;
}, {
  queued: 0,
  building_graph: 5,
  partial: 6,
  repo_access_required: 2,
  unavailable: 2,
  failed: 2,
});

const TERMINAL_READY = new Set(["complete", "completed", "ready", "success", "succeeded", "done"]);
const TERMINAL_FAILED = new Set(["failed", "error", "unavailable"]);

function asObject(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : {};
}

function asArray(value) {
  return Array.isArray(value) ? value : [];
}

function clampPercent(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return 0;
  return Math.max(0, Math.min(100, Math.round(number)));
}

function titleCase(value) {
  return String(value || "")
    .replace(/[_-]+/g, " ")
    .replace(/\b\w/g, (match) => match.toUpperCase());
}

function warningCode(value) {
  return String(value || "").trim().split(":")[0];
}

function dedupeWarnings(values) {
  const seen = new Set();
  const out = [];

  for (const value of asArray(values)) {
    const text = String(value || "").trim();
    if (!text) continue;

    const key = warningCode(text) || text;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(text);
  }

  return out;
}

function warningNote(value) {
  const text = String(value || "").trim();
  const [code, rawCount] = text.split(":");
  const count = Number(rawCount);
  const displayCount = Number.isFinite(count) ? count.toLocaleString() : "";

  switch (code) {
    case "context_graph_file_limit_reached":
      return `Indexed the safest ${displayCount || "configured"} files first.`;
    case "context_graph_large_files_skipped":
      return `Skipped ${displayCount || "some"} oversized file${count === 1 ? "" : "s"}.`;
    case "context_graph_sensitive_files_skipped":
      return `Protected ${displayCount || "some"} sensitive path${count === 1 ? "" : "s"}.`;
    case "context_graph_char_limit_reached":
      return "Kept the source bundle within the prompt-safe size limit.";
    default:
      return labelizeWarning(code || text);
  }
}

function labelizeWarning(value) {
  return titleCase(String(value || "indexing_note").replace(/^context_graph_/, ""));
}

function resolveStageIndex(stage, percent) {
  const normalizedStage = String(stage || "").trim().toLowerCase();
  if (Number.isInteger(STAGE_INDEX[normalizedStage])) {
    return STAGE_INDEX[normalizedStage];
  }
  if (percent >= 100) return STAGES.length - 1;
  return Math.max(0, Math.min(STAGES.length - 1, Math.floor((percent / 100) * STAGES.length)));
}

function stageState({ index, currentIndex, status, stage }) {
  const normalizedStatus = String(status || "").trim().toLowerCase();
  const normalizedStage = String(stage || "").trim().toLowerCase();

  if (normalizedStage === "ready" || TERMINAL_READY.has(normalizedStatus)) {
    return "done";
  }
  if (normalizedStage === "partial") {
    return index < STAGES.length - 1 ? "done" : "attention";
  }
  if (TERMINAL_FAILED.has(normalizedStatus) || ["failed", "unavailable", "repo_access_required"].includes(normalizedStage)) {
    if (index < currentIndex) return "done";
    if (index === currentIndex) return "error";
    return "pending";
  }
  if (index < currentIndex) return "done";
  if (index === currentIndex) return "active";
  return "pending";
}

function StageMarker({ state }) {
  if (state === "active") {
    return (
      <span className="flex h-5 w-5 items-center justify-center rounded-md border border-primary/45 bg-primary/10">
        <span className="h-2.5 w-2.5 animate-spin rounded-full border-2 border-primary/30 border-t-primary" />
      </span>
    );
  }

  const styles = {
    done: "border-success/35 bg-success/10 text-success",
    attention: "border-warning/40 bg-warning/10 text-warning",
    error: "border-destructive/40 bg-destructive/10 text-destructive",
    pending: "border-border/70 bg-background/70 text-muted-foreground",
  };
  const labels = {
    done: "Done",
    attention: "Review",
    error: "Blocked",
    pending: "",
  };

  return (
    <span
      className={`flex h-5 w-5 items-center justify-center rounded-md border text-[10px] font-semibold ${styles[state] || styles.pending}`}
      aria-label={labels[state] || "Pending"}
    >
      {state === "done" ? "✓" : state === "attention" ? "!" : state === "error" ? "!" : ""}
    </span>
  );
}

export default function AppIntelligenceProgressCard({ metadata: metadataProp = {}, payload: payloadProp = {}, message = "" }) {
  // Support normal inline UI surface payloads and restored transcript metadata.
  const metadata = Object.keys(metadataProp).length > 0 ? metadataProp : payloadProp;
  const progress = asObject(metadata.app_intelligence_progress || metadata.progress || {});
  const progressDetails = asObject(metadata.progress_details || progress.details);
  const stage = String(metadata.progress_stage || progress.stage || "").trim().toLowerCase();
  const status = String(metadata.progress_status || progress.status || "").trim().toLowerCase();
  const percent = clampPercent(metadata.progress_percent ?? progress.percent);
  const currentIndex = resolveStageIndex(stage, percent);
  const warnings = dedupeWarnings([
    ...asArray(metadata.progress_warnings),
    ...asArray(progress.warnings),
  ]);

  const isReady = stage === "ready" || TERMINAL_READY.has(status);
  const isPartial = stage === "partial" || status === "partial";
  const isBlocked = stage === "repo_access_required" || TERMINAL_FAILED.has(status);
  const badgeClass = isBlocked
    ? "border-destructive/35 bg-destructive/10 text-destructive"
    : isPartial
      ? "border-warning/35 bg-warning/10 text-warning"
      : isReady
        ? "border-success/35 bg-success/10 text-success"
        : "border-primary/35 bg-primary/10 text-primary";
  const badgeLabel = isBlocked ? "Needs attention" : isPartial ? "Partial" : isReady ? "Ready" : "Working";
  const statusMessage = String(message || progress.message || "Building app context.").trim();
  const repositorySource = progressDetails.source || "";
  const downloaded = progressDetails.downloaded_file_count;
  const total = progressDetails.fetch_candidate_count;

  if (isReady) {
    const warningCount = warnings.length;

    return (
      <div
        className="w-full max-w-[34rem] rounded-lg border border-success/30 bg-card/85 px-4 py-3 text-card-foreground shadow-sm"
        role="status"
        aria-live="polite"
      >
        <div className="flex items-start gap-3">
          <span className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-md border border-success/35 bg-success/10 text-xs font-semibold text-success">
            ✓
          </span>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-xs font-semibold uppercase text-muted-foreground">App Intelligence</span>
              <span className="rounded-full border border-success/35 bg-success/10 px-2 py-0.5 text-[11px] font-medium text-success">
                Completed
              </span>
            </div>
            <p className="mt-1 text-sm font-semibold text-foreground">Codebase context is ready</p>
            <p className="mt-0.5 text-xs leading-5 text-muted-foreground">
              Mozaiks finished reading the app. The discovery agent can use this context now.
            </p>
            {warningCount > 0 && (
              <p className="mt-1 text-[11px] leading-4 text-warning">
                Safe indexing notes are available in the overview.
              </p>
            )}
          </div>
          <span className="shrink-0 rounded-md border border-border/60 bg-background/70 px-2.5 py-1 text-xs font-semibold text-foreground">
            100%
          </span>
        </div>
      </div>
    );
  }

  return (
    <div
      className="w-full max-w-[44rem] overflow-hidden rounded-lg border border-border bg-card text-card-foreground shadow-sm"
      role="status"
      aria-live="polite"
    >
      <div className="border-b border-border bg-muted/20 px-4 py-3">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-xs font-semibold uppercase text-muted-foreground">App Intelligence</span>
              <span className={`rounded-full border px-2 py-0.5 text-[11px] font-medium ${badgeClass}`}>
                {badgeLabel}
              </span>
            </div>
            <p className="mt-1 text-sm font-semibold text-foreground">Understanding your codebase</p>
            <p className="mt-0.5 text-xs leading-5 text-muted-foreground">
              {isReady
                ? "The discovery agent now has its source-backed context."
                : isBlocked
                  ? "Mozaiks needs repository access before the discovery agent can continue."
                  : "Mozaiks is preparing source-backed context before the agent responds."}
            </p>
          </div>
          <div className="shrink-0 rounded-md border border-border/60 bg-background/70 px-3 py-1.5 text-right">
            <p className="text-base font-semibold tabular-nums text-foreground">{percent}%</p>
            <p className="text-[11px] text-muted-foreground">{titleCase(stage || status || "working")}</p>
          </div>
        </div>
      </div>

      <div className="space-y-3 px-3 py-2.5 sm:px-4">
        <div className="h-2 overflow-hidden rounded-full bg-muted">
          <div
            className={`h-full rounded-full transition-all duration-700 ${isBlocked ? "bg-destructive" : isPartial ? "bg-warning" : "bg-primary"}`}
            style={{ width: `${percent}%` }}
          />
        </div>

        <div className="rounded-md border border-border/55 bg-background/60 px-3 py-2">
          <p className="text-xs font-medium text-foreground">{statusMessage}</p>
          <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
            {repositorySource && <span>Source: {titleCase(repositorySource)}</span>}
            {Number.isFinite(Number(downloaded)) && Number.isFinite(Number(total)) && Number(total) > 0 && (
              <span>
                Files loaded: {Number(downloaded).toLocaleString()} / {Number(total).toLocaleString()}
              </span>
            )}
            {!isReady && !isBlocked && <span>The agent has not started editing files.</span>}
          </div>
        </div>

        <div className="grid gap-1.5 sm:grid-cols-2">
          {STAGES.map((item, index) => {
            const state = stageState({ index, currentIndex, status, stage });
            return (
              <div
                key={item.id}
                className={`flex items-start gap-2 rounded-md border px-2.5 py-2 transition-colors ${
                  state === "active"
                    ? "border-primary/35 bg-primary/10"
                    : state === "done"
                      ? "border-success/20 bg-success/5"
                      : state === "error"
                        ? "border-destructive/35 bg-destructive/10"
                        : state === "attention"
                          ? "border-warning/35 bg-warning/10"
                          : "border-border/45 bg-background/45"
                }`}
              >
                <StageMarker state={state} />
                <div className="min-w-0 flex-1">
                  <p className={`text-xs font-semibold ${state === "pending" ? "text-muted-foreground" : "text-foreground"}`}>
                    {item.label}
                  </p>
                  <p className="mt-0.5 text-[11px] leading-4 text-muted-foreground">{item.description}</p>
                </div>
              </div>
            );
          })}
        </div>

        {warnings.length > 0 && (
          <div className="rounded-md border border-warning/30 bg-warning/10 px-3 py-2">
            <p className="text-[11px] font-medium text-warning">Indexing notes</p>
            <p className="mt-1 line-clamp-2 text-[11px] leading-5 text-foreground/80">
              {warnings.slice(0, 3).map(warningNote).join(" ")}
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
