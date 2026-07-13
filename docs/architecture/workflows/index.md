# Workflows

Workflow docs define declarative AI runs, routing, orchestration loops,
structured outputs, session routing, and refinement control.

Read these docs when changing AG2 workflow authoring, AppGenerator/AgentGenerator
handoffs, workflow routing, or session/refinement behavior.

| Doc | Scope |
| --- | --- |
| [Workflow Architecture](workflow-architecture.md) | Workflow root ownership and execution model |
| [Workflow Authoring Contracts](workflow-authoring-contracts.md) | Canonical workflow YAML contract |
| [Workflow Routing Transitions](workflow-routing-transitions.md) | Global workflow routing and transition UI |
| [Orchestration Control Loops](orchestration-control-loops.md) | Workflow-local, builder-session, and refinement loops |
| [AG2 Ownership Boundary](ag2-ownership-boundary.md) | Mozaiks/AG2 ownership rules and compatibility watchpoints |
| [AG2 Update Watchpoints](ag2-update-watchpoints.md) | Living AG2 upgrade checks and intentional divergence log |
| [AG2 Execution Alignment Plan](ag2-execution-alignment-plan.md) | Audit and replacement plan for shrinking Mozaiks-owned agentic runtime code |
| [Declarative AG2 Mapping](declarative-ag2-mapping.md) | YAML-to-AG2 mapping rules |
| [AG2 Network Patternbook](ag2-network-patternbook.md) | Canonical AgentGenerator pattern catalog for AG2 1.0 beta Network workflow shapes |
| [Build Context Packs](build-context-packs.md) | Targeted build-time catalogs, contracts, templates, and prompt projections |
| [Domain-Agnostic Build Factory](domain-agnostic-build-factory.md) | Dev-pack architecture for shared harnesses and domain-specific workflow contracts |
| [Structured Output Extraction](structured-output-extraction-contract.md) | Strict structured-output extraction contract |
| [Refinement Control Plane](refinement-control-plane.md) | Refinement routing, classification, and scoped repair |
| [Session Router](session-router.md) | Session routing and resume contract |
| [Control-Plane Harness Architecture](control-plane-harness-architecture.md) | Builder harness ownership and contracts |
| [Proposal-Only Workflow Pattern](proposal-only-workflow-pattern.md) | HITL planning/review workflow archetype, blocked/deferred phases, output invariants |
