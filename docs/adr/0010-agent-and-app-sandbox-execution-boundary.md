# ADR 0010: Agent And Generated-App Sandbox Execution Boundary

Date: 2026-09-14

Status: Accepted

## Context

Mozaiks uses sandboxes in two different phases:

1. AG2 agents may need to inspect, write, and run code while completing an
   assignment.
2. The Factory must start a generated application and verify its backend,
   frontend, persistence, and browser behavior before promotion.

Both phases can use Docker or a hosted sandbox provider, but they have
different owners, lifecycles, security policies, and success criteria. Treating
them as one mechanism would allow an agent execution result to be mistaken for
proof that the generated application works.

## Decision

AG2 owns agent-side code execution. Mozaiks owns generated-app validation and
preview.

| Phase | Canonical owner | Contract | Validates | Typical providers |
| --- | --- | --- | --- | --- |
| Agent implementation | AG2 | `SandboxShellTool` / `SandboxCodeTool` over a `CodeEnvironment` | Commands or code requested by the agent | Local, Docker, Daytona, Tenki, or another AG2 backend |
| App validation and preview | Mozaiks OSS | `SandboxPort` and `ArtifactPreviewSessionManager` | A bound app artifact starting as a complete app, including health and browser checks | Local Docker or E2B |

The Factory lifecycle is:

```text
AG2 generation/refinement
  -> deterministic artifact validation
  -> SandboxPort app boot
  -> backend/frontend readiness
  -> functional and Playwright acceptance
  -> persist validation evidence
  -> promote only accepted artifact
```

Agent-side execution is never sufficient for the final acceptance decision.
App preview is never exposed as an unbounded agent shell. Each preview session
must be bound to the authenticated owner, host, target app, artifact version,
and build registry; it must have a deadline and confirmed teardown.

## Configuration policy

- Use `sandbox_shell: true` in a workflow agent declaration only when that
  agent needs AG2 shell access for its own bounded assignment.
- Select generated-app validation with the existing
  `app_validation_strategy` contract: `e2b`, `docker`, `local`, or `skip`.
- Configure provider details through environment and provider adapters. Do not
  generate provider SDK code, credentials, or Jinja-rendered runtime policy
  into an app bundle.
- The existing `infra/docker/Dockerfile.preview` is the canonical local
  preview environment definition. A hosted E2B template must provide the
  equivalent runtime, frontend dependencies, private MongoDB, and preview
  supervisor.
- `skip` may be used for deterministic tests, but it is not a validation pass
  and cannot by itself authorize promotion.

## Ownership and evidence

AG2 owns command execution, tool policy, and agent task lifecycle. Mozaiks
owns artifact identity, deterministic validation, preview lifecycle, browser
acceptance, promotion, and persisted evidence. A build record may name an E2B
or Docker strategy only when that provider actually ran; provider session IDs,
status, and trimmed output are evidence fields, not semantic authority.

Hosted product code may choose E2B as its provider and apply quotas or billing,
but it must consume the OSS `SandboxPort` boundary. It must not fork the
Factory or move generated-app validation into AG2 agent tools.

## Rejected alternatives

- **One universal sandbox abstraction:** rejected because agent commands and
  full-app acceptance have incompatible ownership and lifecycle semantics.
- **Jinja as the E2B implementation:** rejected because Jinja can render
  configuration but cannot provide the executable image and dependencies an
  E2B template requires.
- **Use AG2 code execution as app acceptance:** rejected because AG2 code
  execution does not establish the app's canonical identity, long-lived
  servers, preview URL, database composition, or promotion evidence.
- **E2B-only OSS behavior:** rejected because local Docker is the correct free
  path for OSS users and hosted E2B is optional product infrastructure.

## Compatibility watchpoint

If AG2 later provides long-lived `CodeEnvironment` sessions with exposed ports,
health checks, artifact identity, and per-session budgets, revisit the
implementation. The boundary remains: AG2 may own the generic execution
primitive, while Mozaiks retains app identity, acceptance, promotion, and
hosted policy unless those contracts are explicitly moved upstream.

## Verification

The decision is exercised by the existing AG2 sandbox contract tests,
`SandboxPort` adapter tests, preview-session tests, generated-app acceptance,
and live Docker/E2B checks. The live checks must remain separate: agent
generation success does not substitute for generated-app acceptance.

## Addendum: closed repository candidate boot (2026-10-09)

The first repository candidate preview step uses the existing
`ArtifactPreviewSessionManager` admission, owner/build identity, TTL, and
confirmed teardown. Its internal `create_sealed_candidate` method requires a
canonical app archive, its exact SHA-256 digest, and a trusted local Docker
image ID or exact E2B template build reference. The admission record binds the
archive digest and provider runtime reference immutably. Existing mutable
artifact preview records keep their current identity shape.
The trusted caller must also attest that the selected image or template build source and installed
framework/dependencies match the candidate's `requirements.txt` and provenance
before describing the result as an exact candidate preview. A syntactically
valid runtime reference alone does not establish that correspondence.

Before provider allocation, the manager bounds and verifies the archive, rejects
paths outside `app/`, `workflows/`, and root `requirements.txt`, and matches `app/app.json` `appId`
to the owned target. The Docker adapter runs the pinned local image without
network, published ports, forwarded environment, runtime dependency install,
or container logs. It stages source once as root-owned read-only files and
limits writable temporary state with tmpfs. The app runs as a non-root UID.
Only a successful internal backend/frontend health check is returned; no raw
preview URL or public route is created. The existing sync and start operations
reject sealed sessions. Cleanup uses the same durable preview ledger.

For E2B, the selected adapter stages the bounded source through a root-only
upload and committed verifier, then locks the workspace against the runtime UID
before boot. It confirms provider isolation and returns no URL. Stage failure
kills by provider ID; a restarted worker cannot reconnect a sealed instance
because E2B connect may resume it. This adds an offline framework boot path,
not a browser route or hosted product acceptance. Live E2B provider proof and
an owner-authorized token-holding gateway remain separate rollout gates.

This is a closed boot check, not a browser preview or functional acceptance.
It currently boots the framework platform host. Authenticated apps fail closed
without a preview-scoped OIDC issuer, and a hosted product's own host entrypoint
is not exercised by this check.
A later decision must specify owner-authenticated proxy routing, separate
browser origin, authenticated app identity, the hosted product entrypoint, and
evidence binding before a candidate can be shown or approved in Studio. AG2
continues to own coding turns; this addendum changes only the app-side sandbox.
