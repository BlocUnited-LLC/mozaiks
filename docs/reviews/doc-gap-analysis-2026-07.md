# Documentation Gap Analysis — July 2026

This document catalogs gaps between implemented code and published
documentation as of the `codex/fix-provenance-task-contracts` branch.
It is an internal tracking file and is excluded from the public docs site.

---

## Summary

The mozaiks OSS repo has solid architectural documentation for the Refinement
Engine, App Intelligence Plane (high level), persistence model, and factory
build workflows. The primary gaps are in the control plane subsystems that
support App Intelligence indexing, source validation, and artifact lifecycle
management.

---

## Status Key

| Status | Meaning |
|---|---|
| **RESOLVED** | Gap filled by a new or updated doc in this analysis pass |
| **PARTIAL** | Partially covered; needs expansion |
| **OPEN** | Not yet documented |

---

## 1. Undocumented Code — High Impact

### Framework Detection
- **Code:** `mozaiksai/core/app_context/framework_detection.py`
- **Status:** **RESOLVED** — `docs/architecture/foundations/framework-detection.md` created
- **What was missing:** Detection algorithm, evidence model, supported frameworks,
  primary framework selection order, validation command emission, monorepo detection

### Source Import Contracts
- **Code:** `mozaiksai/control_plane/source_import.py`
- **Status:** **RESOLVED** — `docs/architecture/builder/source-import-contracts.md` created
- **What was missing:** Two import kinds (`local_workspace` / `git_repository`),
  request/result schemas, URL validation rules, path containment model,
  `MOZAIKS_SOURCE_IMPORTS_PATH` env var, public payload redaction,
  scan policy merging

### Source Validation Framework
- **Code:** `mozaiksai/control_plane/app_validation.py`
- **Status:** **RESOLVED** — `docs/architecture/builder/source-validation-framework.md` created
- **What was missing:** Command planning algorithm, executable allowlist
  (security boundary), workspace isolation model, overlay file mechanics,
  fallback checks, aggregate status rules, `confirm_execution` opt-in model

---

## 2. Partially Documented — Medium Impact

### App Intelligence Index Jobs
- **Code:** `mozaiksai/control_plane/app_intelligence_jobs.py`
- **Status:** **PARTIAL** — mentioned in `app-intelligence-plane.md` (§ Source
  Import And Indexing Jobs) but job phase state machine and public payload
  redaction rules are not explicitly described
- **What's missing:**
  - Full phase list: `clone → scan → source_index → symbol_parse →
    graph_build → intelligence_synthesis → ready`
  - How `workspace_root` is set from import result
  - How `framework_detection` is stored on the job and read back by the
    validation runner
- **Recommendation:** Add a "Job State Machine" section to `app-intelligence-plane.md`

### Artifact Invalidation and Staleness
- **Code:** `mozaiksai/control_plane/invalidation.py`
- **Status:** **PARTIAL** — BFS propagation and the two-tier routing model are
  well documented in `docs/architecture/builder/artifact-staleness-and-routing.md`
- **What's missing:**
  - How invalidation is triggered by a source change vs. a dependency change
    vs. a policy change (as opposed to explicit change-request-driven
    invalidation)
  - App Intelligence context staleness specifically (separate from artifact
    family staleness)
- **Recommendation:** Add a "Source Change Invalidation" section to the staleness doc

### Studio Host Architecture
- **Code:** `mozaiksai/hosts/studio.py`
- **Status:** **PARTIAL** — mentioned in CLAUDE.md and ARCHITECTURE.md at
  high level; `docs/architecture/builder/studio-product-model.md` covers
  the product/UX model but not the host composition
- **What's missing:**
  - Host composition model (how Studio layers on Platform)
  - Background task lifecycle for indexing and refinement jobs
  - Endpoint surface summary (source import, index, validation, artifact
    review and promotion)
- **Recommendation:** Create `docs/architecture/builder/studio-host-architecture.md`

### App Context Refresh
- **Code:** `mozaiksai/control_plane/app_context_refresh.py`,
  `mozaiksai/control_plane/app_context_refresh_execution.py`
- **Status:** **OPEN**
- **What's missing:** How the control plane determines that a source root needs
  re-indexing, what triggers a refresh plan, and how refresh jobs relate to
  App Intelligence index jobs
- **Recommendation:** Add a section to `app-intelligence-plane.md` (§ Continuous
  Refinement) or create a dedicated doc

### Revision Context and Scoped Execution
- **Code:** `mozaiksai/control_plane/revision_context.py`,
  `mozaiksai/control_plane/scoped_execution.py`
- **Status:** **OPEN**
- **What's missing:** What revision context includes (prior artifacts, stale
  families, impact analysis), how scoped execution bounds the files sent to
  the coding refinement checkpoint
- **Recommendation:** Add a "Revision Context" section to
  `docs/architecture/workflows/refinement-engine.md`

### Staged Coding Worker
- **Code:** `mozaiksai/control_plane/staged_coding_worker.py`
- **Status:** **OPEN**
- **What's missing:** How staged patches are generated and applied, how the
  worker bridges between a coding refinement agent's output and the
  `overlay_files` passed to the validation runner
- **Recommendation:** Add a subsection to
  `docs/architecture/workflows/refinement-harness-architecture.md`

---

## 3. Minor Gaps

### Persistence Namespace Classes
- **Code:** `mozaiksai/core/data/persistence/namespaces.py`
- **Status:** **PARTIAL** — collections named in
  `docs/architecture/foundations/events-and-data/persistence-and-artifact-storage.md`
  but the three namespace groupings (`RuntimeCollections`, `BuilderCollections`,
  `PlatformCollections`) are not documented
- **Recommendation:** Add an "Namespace Organization" section to the persistence doc

### Scan Policy
- **Code:** `mozaiksai/core/app_context/scan_policy.py`
- **Status:** **PARTIAL** — exclusion prefixes mentioned in context of source
  import but default exclusions and customization are not documented
- **Recommendation:** Add a dedicated section in `framework-detection.md` or
  create `docs/architecture/foundations/scan-policy.md`

### Factory Control Plane Module — Intentional Stub
- **Code:** `factory_app/app/modules/factory_control_plane/`
- **Status:** **PARTIAL** — CLAUDE.md says "Studio identity stub only — no backend,
  no logic" but no doc explains this design decision
- **Recommendation:** Add a note in `refinement-harness-architecture.md` that
  this module exists only for Studio identity and registration, and that the
  actual harness engine lives in `mozaiksai/control_plane/`

### mkdocs Navigation — New Docs Not Wired
- **Status:** **OPEN** — the three new docs created in this pass are not yet
  in `mkdocs.yml`
- **Files to add:**
  - `architecture/foundations/framework-detection.md`
  - `architecture/builder/source-import-contracts.md`
  - `architecture/builder/source-validation-framework.md`

---

## 4. Docs With No Corresponding Stale Code Found

The following concerns were checked and found to be accurately documented:

| Doc | Verdict |
|---|---|
| `artifact-staleness-and-routing.md` | Accurate — BFS propagation, two-tier routing, session sync all match code |
| `refinement-engine.md` | Accurate — checkpoint model, harness pack, policy config all match code |
| `refinement-harness-architecture.md` | Accurate — harness.yaml, tool routing, checkpoints match code |
| `app-intelligence-plane.md` | Mostly accurate — implementation map is up to date |
| `persistence-and-artifact-storage.md` | Accurate — collection names and lifecycle status match code |

---

## 5. Recommended Next Documentation Work (Priority Order)

1. **Add job phase state machine to `app-intelligence-plane.md`**
   — Addresses the most common question: "what is the indexer doing?"

2. **Create `docs/architecture/builder/studio-host-architecture.md`**
   — Covers Studio host composition, background task lifecycle, and endpoint
   surface for contributors adding new Studio capabilities

3. **Add "Revision Context" to `refinement-engine.md`**
   — Covers `revision_context.py` and `scoped_execution.py` so contributors
   understand what context a coding checkpoint receives

4. **Wire new docs into `mkdocs.yml`**
   — Adds framework detection, source import, and source validation to the
   public nav under Foundations and Builder/Generation sections

5. **Add scan policy section to `framework-detection.md` or new dedicated doc**
   — Documents default exclusions and the `MOZAIKS_SCAN_EXCLUDED_PREFIXES`
   customization path
