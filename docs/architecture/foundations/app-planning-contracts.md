# App Planning Contracts

**Last updated:** 2026-03-12  
**Status:** Current architecture reference  
**Audience:** Generator authors, workflow authors, runtime maintainers

---

## Purpose

This document defines the typed planning contracts that decompose raw user
intent into structured app concerns before file generation begins.

These contracts are the missing bridge between:

- user intent
- app planning
- compiled `platform/` bundle files

Without this layer, app generation collapses into freeform code generation and
workflow overuse.

---

## Runtime Contract Location

The canonical runtime models live in:

- `mozaiksai/core/orchestration/planning_contracts.py`

Core entrypoint:

- `build_decomposition_package(payload)`

This validates a complete planning payload and enforces cross-reference
integrity.

---

## Contract Set

The planning contract currently includes:

- `AppSpec`
- `Capability`
- `EntitySpec`
- `ViewSpec`
- `ActionSpec`
- `ModuleSpec`
- `WorkflowSpec`
- `PolicySpec`
- `BundlePlan`
- `DecompositionPackage`

---

## Why This Exists

A broad request like:

- `build me a marketplace`

does not directly answer:

- what entities exist
- what pages should be modules
- what actions are deterministic
- what belongs in workflows
- what policies should gate access

The planning contracts force these decisions before generation.

---

## Mode Rule

Each capability must declare one execution mode:

- `workflow`
- `action`
- `module`

And each mode has a required reference:

- `workflow` -> `workflow_refs` must be non-empty
- `action` -> `action_refs` must be non-empty
- `module` -> `module_refs` must be non-empty

This prevents vague capability plans.

---

## Cross-Reference Rule

`DecompositionPackage` validates that references in capabilities exist in the
declared specs.

Examples:

- `capability.action_refs` must refer to declared `ActionSpec.name`
- `capability.module_refs` must refer to declared `ModuleSpec.name`
- `capability.workflow_refs` must refer to declared `WorkflowSpec.name`

This catches broken planning outputs before file generation starts.

---

## Example Skeleton

```json
{
  "app_spec": {
    "name": "CampusMarket",
    "summary": "Student marketplace"
  },
  "capabilities": [
    {
      "capability_id": "browse_listings",
      "label": "Browse listings",
      "mode": "module",
      "module_refs": ["marketplace_home"],
      "view_refs": ["listings_list"],
      "entity_refs": ["Listing"]
    }
  ],
  "entities": [
    { "name": "Listing", "purpose": "Sell items", "key_fields": [] }
  ],
  "views": [
    { "name": "listings_list", "view_type": "list", "entity": "Listing" }
  ],
  "actions": [],
  "modules": [
    {
      "name": "marketplace_home",
      "purpose": "Main page",
      "route": "/marketplace",
      "primary_views": ["listings_list"]
    }
  ],
  "workflows": [],
  "policies": [],
  "bundle_plan": {
    "config_files": ["platform/config/ai.json"],
    "module_paths": ["platform/modules/marketplace_home/module.json"],
    "workflow_paths": [],
    "data_model_paths": ["platform/entities/listing.json"]
  }
}
```

---

## Relationship To Other Docs

- [App Creation Guide](app-creation-guide.md) explains the decomposition
  pipeline.
- [App Bundle Declaratives](app-bundle-declaratives.md) explains the target file
  families.
- [Builder Execution Model](builder-execution-model.md) explains how planning
  output becomes task execution.
- [Prompt Pack: Decompose App Intent](../../instruction-prompts/app-planning/decompose-app-intent.md)
  tells AI coding agents how to emit this structure.
