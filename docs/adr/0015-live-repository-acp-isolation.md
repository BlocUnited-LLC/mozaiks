# ADR 0015: Live Repository ACP Isolation

Date: 2026-10-07

Status: proposed

## Decision

Keep the existing offline repository ACP executor as the proof of file and
operation boundaries. Add live model execution only through a separate,
explicitly enabled worker image and Docker transport. The trusted host worker
retains approval, source snapshot, path grants, validation, and publication
authority; AG2 and its Codex or Claude ACP adapter perform one bounded coding
turn against selected files inside a disposable container.

`execute_repository_docker_turn` remains the single OSS host-side execution
port. A future typed, trusted-worker-only live profile selects the new internal
transport; no user request, plan, or agent output may select that profile.
The caller still supplies `CodingWorkerRequest`, approved execution context,
snapshot, and host path callbacks, and receives `RepositoryDockerTurn`. Offline
execution remains the default. The live profile must reject an absent or
mismatched approved context, snapshot, path validator, or create-path validator
when creation is granted. It must verify source and operation grants before
supplying source or a synthetic gateway token to the container. The offline port's
current option to run without an approved context is not a live-mode grant.
App Zero must not launch a parallel Codex or Claude subprocess path.

One manually triggered, operator-only local acceptance turn may run after the
fixed image, gateway-held credential route, and network preflight pass their
gates. It may create a draft protected PR after the normal validation gates, but
cannot itself authorize merge, artifact promotion, or release. Unattended or
customer-facing live execution remains disabled until that real-model turn has
been reviewed. Multi-tenant live execution remains disabled until a separate
credential and isolation decision is accepted. This ADR does not enable live
mode or change the offline executor.

## Reason

The [repository editing loop](../architecture/workflows/e2e-app-editing-loop.md)
already has the right authority split. `execute_repository_docker_turn` currently
enforces `--network none`, rejects known model credentials in the image, and
requires an `offline` label. Its proof image runs a synthetic ACP agent. The
separate adapter image proves real Codex and Claude ACP initialization without
authentication, but has no coding-turn entrypoint. These are valuable offline
proofs; none establishes a live model-backed private-repository edit.

The agent can request a terminal even when AG2's `allow_terminal=False` does
not advertise one. Treat both the ACP adapter and the code it may execute as
untrusted. A prompt, shell policy, or post-turn diff filter cannot isolate it
from host files, credentials, or networks available to its process.

## Authority and Threat Boundary

| Principal | Receives | Must not receive | Authority |
| --- | --- | --- | --- |
| App host | Authenticated request and approved plan | Docker socket or model credential in an agent-visible response | Tenant and workspace authorization |
| Trusted job worker | Approved handoff, pinned source snapshot, local Docker socket, provider configuration, source-control publisher | Agent-authored approval or validation claims | Recheck identity and source; start and remove the container; validate and publish a protected PR |
| ACP container | One bounded request, selected editable files, selected read-only inspection files, exact create/delete grants, and a synthetic per-job gateway token | Upstream model key, host filesystem or profile mounts, personal Codex/Claude login, GitHub token, Docker socket, complete repository, host database credentials | Propose file bytes only |
| Model gateway | One upstream model key over private stdin, approved adapter and model, and the matching per-job token | Repository write credentials, source files except those included in approved model requests, or a published host port | Authenticate the worker and forward only bounded model API requests to the fixed provider host |

The agent may read every file and environment variable in its container and
may attempt arbitrary commands. The host therefore supplies only approved
source, mounts no host directory, uses a nonroot user, read-only root, bounded
tmpfs, dropped capabilities, no new privileges, process/CPU/memory/time limits,
no Docker logs, and removes the container on every outcome. A separate trusted
worker holds the Docker socket; the web host and agent container do not.

The agent may send selected source to the configured model provider as part of
the approved task. The host must apply its source and secret-content policy
before the turn and its candidate-content policy before storing or publishing
output. The model response, ACP events, and agent-authored summary are never
acceptance evidence. The host exports observed workspace bytes, stages them
against the pinned snapshot, finalizes the canonical patch candidate, and
binds validation and publication to its digest. A protected PR and human
review remain required.

## Live Transport and Credential Route

1. Build a fixed, locally available image with pinned AG2, ACP protocol,
   Codex/Claude adapters, and a one-shot Mozaiks worker entrypoint. Do not
   install packages at turn time. Resolve the local image ID before Docker
   creation and inspect the created container before starting it. The image
   must contain no credentials or app workspace.
2. Bind the agent only to a per-turn Docker `--internal` bridge whose only
   other peer is a one-job HTTP model gateway. Give the gateway a separate
   egress bridge, no published host port, no host mount, and a fixed upstream
   (`api.openai.com:443` for Codex or `api.anthropic.com:443` for Claude). The
   gateway authenticates the synthetic token, enforces the approved model,
   accepts only the required API paths, verifies upstream TLS, and does not
   follow redirects. The trusted worker verifies both images, network peers,
   and container isolation before source enters the agent. The current gateway
   code restricts its own outbound HTTP calls; its Docker egress bridge is not
   an IP-level allowlist. A compromised gateway could use that bridge for other
   destinations. Network-level egress restriction or a separately accepted
   residual-risk decision is required before unattended or multi-tenant use.
3. Use a dedicated, revocable model credential for this worker, with restricted
   API permissions and a verified hard spend limit where the provider supports
   one. An alert-only budget is not a spend cap. Deliver the upstream key from
   the trusted secret backend only to the separate gateway over private stdin.
   The agent receives a random, one-job token that works only at that gateway.
   The upstream key must never enter the agent image, environment, command
   line, workspace, proposal, or result JSON. The gateway also must not put it
   in its image, Docker `Config.Env`, command line, or logs. Docker `inspect`
   exposes `Config.Env`. The agent can use its synthetic token to send selected
   source to the permitted model and spend within the gateway's request, byte,
   time, and output-token budgets. Those transport budgets do not cap input
   tokens or total provider spend. A hard provider-side limit and key rotation
   or revocation after the operator acceptance turn remain required.
4. Set `HOME`, `CODEX_HOME`, and `CLAUDE_CONFIG_DIR` to fresh container tmpfs
   paths. Do not mount or copy the operator's `~/.codex/auth.json` or
   `~/.claude` directory. Codex uses a fresh user-level provider configuration
   pointing its Responses API at the gateway and reads the synthetic token
   from `MOZAIKS_JOB_TOKEN`. Claude Code uses the gateway as
   `ANTHROPIC_BASE_URL` and the synthetic token as `ANTHROPIC_AUTH_TOKEN`.
   Neither adapter receives a cached personal login or the upstream API key.
5. Keep the bounded request and response protocol from the offline executor.
   The container receives only the exact selected files and operation grants.
   The host rejects unapproved paths, changed inspection files, malformed or
   oversized output, stale source, and any failure to remove the container.
   No Git or GitHub credential reaches the agent. The trusted publisher runs
   only after validation and candidate-digest checks.

The initial credential route is for local operator acceptance with a dedicated
gateway-held key. Multi-tenant operation must remain disabled until a separately
reviewed credential source, gateway isolation, and provider quota policy prove
their tenant boundary.
ChatGPT plan access through a user-authorized OAuth integration is a separate
product decision with its own eligibility, consent, and token lifecycle; a
cached personal login is not that integration.
[OpenAI's sandbox security guidance](https://developers.openai.com/api/docs/guides/agents-api/environments/security)
likewise recommends isolating workloads, restricting egress, and keeping
long-lived application credentials outside agent environments.

## Acceptance Gates

All gates below apply to the fixed image and transport that will actually be
used; passing the existing fake-agent proof or initialize handshake alone is
insufficient.

1. **Offline container assertions:** before start, verify both image IDs, exact
   per-turn internal network with only the gateway as the worker's peer,
   gateway-only separate egress, nonroot users, filesystem and resource limits,
   no bind/volume mounts or socket, no host credentials in Docker configuration,
   fresh tmpfs homes, and no published port. After a malicious terminal probe,
   assert that the worker cannot directly reach the internet, host gateway,
   metadata/private-network targets, or the gateway's egress bridge. Assert
   that the HTTP gateway rejects non-allowlisted paths, model names, methods,
   tokens, and redirects. A network-layer egress gate for a compromised
   gateway remains a separate activation requirement.
2. **Credential handling:** use a canary upstream credential to verify the
   worker's image, `docker inspect`, environment, process arguments, output,
   and archive do not contain it. Verify the gateway receives it only over
   private stdin and does not emit it through logs or HTTP errors. The worker
   receives only a short-lived synthetic token; attempts to copy that token
   into source or a proposal must fail the host's output/content gate. Verify
   that injected host-only sentinels and personal auth files are absent.
   Reject missing credentials, model mismatch, and gateway misconfiguration
   before the turn. This does not prevent an agent from spending through its
   valid token or sending approved source to the model endpoint.
3. **Contract and failure tests:** exercise update, exact create, exact delete,
   out-of-scope and read-only mutations, source drift, malformed output,
   timeout, process crash, cleanup failure, and duplicate durable attempts.
   Host validation, candidate digest, and source-control gates must fail
   closed; advisory token usage cannot become wallet authority.
4. **Opt-in live acceptance:** after gates 1-3, use the operator-only test path
   and an operator-provided dedicated model key to run one real Codex or Claude
   ACP turn on a deliberately small private repository fixture. Record the
   pinned OSS and app commits, image ID,
   configured adapter/model, approved paths, sanitized turn status, candidate
   digest, actual validation result, protected PR number, CI result, and human
   review. Do not publish or tag based on an offline proof. Never run this gate
   in ordinary CI or expose the key to a pull request from untrusted code.

## Alternatives Considered

- Mount the operator's Codex or Claude login into the container: rejected
  because the agent can read the entire credential cache and act as that user.
- Run the ACP subprocess on the App host: rejected because AG2 ACP may inherit
  host profile paths and can still receive terminal requests.
- Give the agent ordinary Docker bridge access or an upstream API key: rejected
  because it permits direct network exfiltration, local-service access, and
  disclosure of the credential by the agent.
- Reuse the offline fake-agent proof or adapter initialize test as live
  acceptance: rejected because neither sends a coding request to a model.
- Introduce a second coding-agent orchestration system: rejected because AG2
  already owns ACP execution and Mozaiks already owns the refinement lifecycle.

## Consequences

The repository bridge remains provider-neutral at its approved-file and
candidate boundary. The live transport adds a small, explicit provider-specific
deployment surface: pinned adapter and gateway images, a gateway-held model
credential route, and two per-turn networks. A trusted local worker must manage
container and gateway lifecycle. The operator supplies model access; this
decision provisions no paid cloud service.

## Reversibility

Medium risk: the container transport and provider can change behind the
existing coding-provider and repository-patch contracts. Credential custody,
network policy, and audit evidence need review before such a change.

## Affected Invariants

Preserves provider neutrality, names-only generated secrets, deterministic
validation and promotion, bounded agent authority, and the OSS/operator
separation in [Architectural Invariants](https://github.com/BlocUnited-LLC/mozaiks/blob/main/ARCHITECTURAL_INVARIANTS.md).
It extends [ADR 0010](0010-agent-and-app-sandbox-execution-boundary.md): AG2
still owns agent execution; Mozaiks retains artifact acceptance.
It defers the semantic-model and typed-decision-ledger direction in
[issue #411](https://github.com/BlocUnited-LLC/mozaiks/issues/411). This
decision changes execution containment only; it does not change semantic
authority, `AppBuildPlan`, artifact contracts, or refinement routing.

## OSS Boundary

Keep the generic transport, model gateway, sandbox assertions, and
repository-patch contract in OSS. The hosted product owns tenant/job
authorization, credential provisioning, trusted worker deployment, repository
access, validation policy, and PR publication.

## Validation

This decision claims no live acceptance. Implementation must pass every
acceptance gate above and may be enabled only after a separate security and
architecture review of the exact images, gateway, and credential path.
