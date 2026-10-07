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

No live mode may start until the fixed image, model credential route, restricted
model egress, and an opt-in real-model acceptance turn have been verified. This
ADR does not enable live mode or change the offline executor.

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
| ACP container | One bounded request, selected editable files, selected read-only inspection files, exact create/delete grants, and a model credential route | Host filesystem or profile mounts, personal Codex/Claude login, GitHub token, Docker socket, complete repository, host database credentials | Propose file bytes only |
| Model egress proxy | One selected provider endpoint and a per-turn network identity | Repository write credentials or a general network route | Deny all other outbound destinations |

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
2. Bind the agent only to a dedicated Docker `--internal` network with a
   trusted egress proxy. The proxy allows the configured model host and port
   (`api.openai.com:443` for Codex API-key execution or
   `api.anthropic.com:443` for Claude) and denies other names, IP literals,
   private addresses, redirects to other hosts, and direct outbound TCP.
   Ordinary Docker bridge access is insufficient. The trusted worker verifies
   the network and proxy identity at each launch. If the proxy is absent or
   policy cannot be verified, the turn fails before source enters the container.
3. Use a dedicated, revocable model credential for this worker, with restricted
   API permissions and a bounded spend limit where the provider supports
   them. Deliver it from the trusted secret backend to the one-shot worker over
   its private stdin protocol, then pass it only to the selected ACP adapter's
   child process. Never put it in an image, Docker `Config.Env`, command line,
   repository, log, proposal, or result JSON. Docker `inspect` exposes
   `Config.Env`; the agent can still read its own child environment, so this
   initial route requires the network and spending limits above. A future
   proxy-auth route may keep the credential outside the container, but must
   first prove the chosen ACP adapter can authenticate through that proxy.
4. Set `HOME`, `CODEX_HOME`, and `CLAUDE_CONFIG_DIR` to fresh container tmpfs
   paths. Do not mount or copy the operator's `~/.codex/auth.json` or
   `~/.claude` directory. The [official Codex authentication guidance](https://learn.chatgpt.com/docs/auth)
   says `auth.json` contains access tokens and recommends API-key login for
   programmatic CLI work. The local ChatGPT login grants personal account
   authority, not a scoped job identity. The [Codex ACP adapter](https://github.com/agentclientprotocol/codex-acp#authentication)
   accepts `CODEX_API_KEY` or `OPENAI_API_KEY`; AG2's `CodexConfig` supports
   passing the selected key explicitly. Claude's adapter uses the corresponding
   `ANTHROPIC_API_KEY` path.
5. Keep the bounded request and response protocol from the offline executor.
   The container receives only the exact selected files and operation grants.
   The host rejects unapproved paths, changed inspection files, malformed or
   oversized output, stale source, and any failure to remove the container.
   No Git or GitHub credential reaches the agent. The trusted publisher runs
   only after validation and candidate-digest checks.

The initial credential route is for local operator acceptance with a dedicated
key. Multi-tenant operation should use a proven per-tenant credential or
brokered proxy-auth design before activation. ChatGPT plan access through a
user-authorized OAuth integration is a separate product decision with its own
eligibility, consent, and token lifecycle; a cached personal login is not that
integration. [OpenAI's sandbox security guidance](https://developers.openai.com/api/docs/guides/agents-api/environments/security)
likewise recommends isolating workloads, restricting egress, and keeping
long-lived application credentials outside agent environments.

## Acceptance Gates

All gates below apply to the fixed image and transport that will actually be
used; passing the existing fake-agent proof or initialize handshake alone is
insufficient.

1. **Offline container assertions:** before start, verify image ID, exact
   internal network/proxy, nonroot user, filesystem and resource limits, no
   bind/volume mounts, no socket, no host credentials in Docker configuration,
   fresh tmpfs homes, and no general egress. After a malicious terminal probe,
   assert that direct internet, host gateway, metadata/private-network targets,
   DNS bypass, and non-allowlisted proxy destinations are unreachable.
2. **Credential containment:** use a canary credential to prove it does not
   appear in image layers, `docker inspect`, process arguments, Docker logs,
   stderr/stdout, serialized proposals, archives, or PR text. Verify that an
   injected host-only sentinel and personal auth files are absent. Reject
   missing credentials and proxy misconfiguration before the turn.
3. **Contract and failure tests:** exercise update, exact create, exact delete,
   out-of-scope and read-only mutations, source drift, malformed output,
   timeout, process crash, cleanup failure, and duplicate durable attempts.
   Host validation, candidate digest, and source-control gates must fail
   closed; advisory token usage cannot become wallet authority.
4. **Opt-in live acceptance:** with an operator-provided dedicated model key,
   run one real Codex or Claude ACP turn on a deliberately small private
   repository fixture. Record the pinned OSS and app commits, image ID,
   configured adapter/model, approved paths, sanitized turn status, candidate
   digest, actual validation result, protected PR number, CI result, and human
   review. Do not publish or tag based on an offline proof. Never run this gate
   in ordinary CI or expose the key to a pull request from untrusted code.

## Alternatives Considered

- Mount the operator's Codex or Claude login into the container: rejected
  because the agent can read the entire credential cache and act as that user.
- Run the ACP subprocess on the App host: rejected because AG2 ACP may inherit
  host profile paths and can still receive terminal requests.
- Give the agent ordinary Docker bridge access and an API key: rejected because
  it permits arbitrary network exfiltration and access to local services.
- Reuse the offline fake-agent proof or adapter initialize test as live
  acceptance: rejected because neither sends a coding request to a model.
- Introduce a second coding-agent orchestration system: rejected because AG2
  already owns ACP execution and Mozaiks already owns the refinement lifecycle.

## Consequences

The repository bridge remains provider-neutral at its approved-file and
candidate boundary. The live transport adds a small, explicit provider-specific
deployment surface: pinned adapter image, model credential route, and network
proxy. A trusted local worker must manage container and proxy lifecycle. The
operator supplies model access; this decision provisions no paid cloud service.

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

## OSS Boundary

Keep the generic transport, sandbox assertions, and repository-patch contract
in OSS. The hosted product owns tenant/job authorization, provider credentials,
proxy deployment, repository access, validation policy, and PR publication.

## Validation

This proposal requires link/source review and a docs build before merge. It
claims no live acceptance. Implementation must pass every acceptance gate
above and should be enabled only after a separate security and architecture
review of the exact image, proxy, and credential path.
