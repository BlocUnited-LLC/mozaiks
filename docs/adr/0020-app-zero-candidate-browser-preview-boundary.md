# ADR 0020: App Zero Candidate Browser Preview Boundary

Date: 2026-10-09

Status: proposed

## Decision

App Zero may offer its owner an interactive preview of a private repository
candidate **before** opening a draft pull request, but only through a trusted,
owner-authenticated HTTP and WebSocket gateway on a browser origin separate
from the product. The gateway serves the exact sealed candidate and disposable
runtime admitted by the existing OSS `ArtifactPreviewSessionManager`. It does
not expose a Docker port, E2B hostname, provider traffic token, or sandbox
command interface to the browser.

The current offline sealed boot remains the default until the product caller,
gateway, preview identity, and evidence binding described below exist and pass
local acceptance. This decision elaborates [ADR 0010](0010-agent-and-app-sandbox-execution-boundary.md);
AG2 still owns coding execution, while Mozaiks owns application preview and
acceptance.

## Reason and current boundary

`create_sealed_candidate` currently verifies a bounded archive, binds its
SHA-256 and a pinned local Docker image ID to the durable preview record, stages
read-only source, boots the framework platform host without network or
published ports, and returns health without a URL. It does not prove browser
behavior or the App Zero product host. The broader Studio artifact preview
routes can publish URLs for generated apps; their owner check and mutable sync
contract do not authorize exposing private repository candidates. App Zero's
`app.host:app` entrypoint, brokered OIDC contract, and product dependencies
also differ from a generic platform preview. A new delivery boundary is needed
before an owner can safely review a live candidate.

## Ownership and admission

1. **App Zero is the trusted caller.** Its server authenticates the owner,
   verifies the owned build and candidate descriptor, reconstructs the exact
   canonical archive from a pinned source revision plus reviewed changes, and
   submits it to the OSS manager. Agents and candidate code cannot call the
   sealed creation path. The candidate descriptor alone is not proof that
   archive bytes are current.
2. **OSS retains preview authority.** `ArtifactPreviewSessionManager` and its
   durable store own admission limits, immutable owner/app/artifact/build and
   provider identity, startup, status, provider routing after reconnect, TTL,
   and confirmed teardown. The browser path extends that authority; it does
   not create a second session manager or reuse mutable file sync for sealed
   candidates. App Zero owns its product approval policy and gateway, not an
   alternative sandbox ledger.
3. **The source is immutable.** The admitted archive contains only canonical
   `app/`, `workflows/`, and root `requirements.txt` members. Its exact digest
   must equal the candidate descriptor and the digest stored in the preview
   reservation. Source stays out of browser tokens, URLs, logs, and preview
   records. Candidate replacement requires a new archive identity and preview;
   a stale preview cannot be relabeled as the replacement.
4. **The runtime is attested.** A trusted build receipt binds the candidate
   archive digest, pinned source revision, installed OSS commit and dependency
   lock, product host entrypoint, and immutable runtime reference. For Docker
   that reference is an inspected `sha256:` image ID; for E2B it is a specific
   template build identity, not a mutable template name. The trusted caller
   verifies that the installed dependencies satisfy the candidate's
   `requirements.txt` and provenance before claiming exact behavior. A valid
   image ID or template name alone is insufficient. No candidate-controlled
   install or image build occurs during preview startup. The trusted build must
   compile UI changes from the same candidate archive and bind the resulting
   frontend asset digest in the receipt. Serving a baseline SPA with a changed
   backend does not prove the candidate the owner will review.

The existing sealed identity fields are Docker-shaped (`sealed_archive_sha256`
and `sealed_image_id`, with `provider=docker` enforced). Provider support must
first evolve that one persisted contract into a typed, provider-specific
immutable runtime reference with a migration or explicit replacement strategy
for existing records. The manager must route reconnect and cleanup by the
stored provider. Neither a selected environment variable nor a product-only
record can substitute for that durable identity.

## Browser delivery and identity

- App Zero issues a short-lived, single-use launch ticket bound to owner,
  candidate, archive digest, build, and preview session. A deliberate owner
  action submits it in a top-level, cross-origin form `POST` to the gateway.
  The gateway requires the exact product `Origin`, redeems the ticket once
  server-side before loading any candidate content, checks current owner and
  preview state, then redirects to a clean preview URL with a host-only,
  `Secure`, `HttpOnly` browser session with revocation. The ticket never appears
  in a URL, fragment, referrer, browser storage, or candidate response. The
  launch response is not cached. Provider credentials and product bearer tokens
  are never placed in browser storage, query strings, `postMessage` payloads,
  or candidate requests. A new tab on the dedicated preview origin is the
  initial UX; embedding needs a later explicit frame and cookie policy decision.
- Each preview gets a unique origin that is never reassigned to another
  candidate or owner; this isolates browser storage and service workers across
  sessions. Hosted deployment must use a domain boundary that cannot receive
  product domain cookies; a distinct port or sibling subdomain with shared
  parent-domain cookies is insufficient. Locally, use a distinct loopback
  hostname with host-only cookies and a deliberately configured origin. Neither
  the product nor preview origin may grant broad cross-origin credential access.
- The gateway authorizes every HTTP request and WebSocket upgrade against the
  owner, session, artifact/build/digest, and unexpired admission. It checks
  `Host` and WebSocket `Origin`, normalizes and constrains paths, forwards only
  to the session's registered private endpoint, and rejects arbitrary targets
  and redirects to provider endpoints. It strips product credentials, forwarded
  identity headers, and hop-by-hop headers; it bounds request size and rate.
  Responses suppress caching and referrer leakage and enforce a preview-scoped
  content security policy. Authorization failure or revocation closes existing
  WebSockets as well as new requests.
- The candidate runs with a separate, disposable preview OIDC issuer/client,
  audience, JWKS, and limited synthetic owner persona. Its configured
  `authRequired` and brokered OIDC flow remain enabled. Preview identity cannot
  access product accounts, production data, source-control tokens, host APIs,
  or payment/provider secrets. The app receives only scoped preview values and
  its private database. The trusted preview issuer must be reachable by the
  browser and by backend JWKS validation through narrowly routed gateway or
  private-network endpoints; this does not grant general network egress. The
  frontend's API, WebSocket, callback, and CORS settings resolve to the preview
  origin. The existing shell auth projection reads the host's runtime
  `VITE_OIDC_*` values, so the trusted launcher can bind a unique callback
  origin without rebuilding the frontend for each candidate. The disposable
  issuer must register that exact callback; a wildcard callback is not a
  substitute. Disabling auth or setting `VITE_MOCK_MODE` is not an acceptable
  shortcut. The candidate may compromise this disposable persona; the persona
  must have no authority outside its preview.
- The trusted supervisor selects a fixed, attested App Zero preview product-host
  entrypoint and compatible frontend. It cannot accept a candidate-provided
  arbitrary command. The current `mozaiksai.hosts.platform:app` boot remains
  valid for generic app checks but is not evidence that App Zero works. A
  health response alone is not an interactive acceptance result.

### Product-host preview profile is required

The deployed `app.host:app` composition calls App Zero's production security
guard during import. That guard requires operator metrics and telemetry secrets
and durable generated-bundle storage in deployed mode. Supplying those secrets
or the product's storage identity to untrusted candidate code would cross the
preview boundary. `E2B_API_KEY` is separate from this startup guard; when E2B
is selected, only the trusted preview controller may hold its provider key.

Before browser preview can activate, App Zero must provide a dedicated,
attested preview-safe product-host composition and configuration profile. It
must load the candidate's App Zero bundle and the product routes needed for the
review journey, use disposable OIDC and an isolated Mongo database, and run
without operator provider credentials or access to production data. Product
integrations that cannot run on disposable preview state must fail closed and
be identified as unverified in the review evidence. The profile must not
disable authentication or weaken the deployed `app.host:app` startup guard.
Until this composition and its isolation are proven, the existing offline boot
cannot be promoted to an owner-visible App Zero preview.

## Provider routes

**Local Docker first.** A browser-capable sealed session retains read-only
source, a non-root app process, private writable state, no host mounts or Docker
socket, and a pinned image. It replaces the offline session's `--network none`
only with an isolated internal network reachable by the trusted gateway. It
publishes no app port on the host and has no route to the product network or
internet. The gateway reaches only the registered container and port. The
offline boot path remains available unchanged.

**E2B later.** E2B sealed allocation may be added through `SandboxPort` only
after the provider adapter can guarantee a private or traffic-token-protected
ingress and exact template build identity. The gateway alone holds the E2B
traffic token and attaches it on server-side upstream requests, including
WebSocket upgrades. It must never forward that token to candidate code or
return a provider URL to the browser. The E2B SDK exposes the traffic token on
the live sandbox object; a controller restart cannot recover it by reading the
session ledger, and reconnect may resume a paused sandbox. Browser activation
therefore needs a trusted, revocable secret-backed token lifecycle, or must
close access and kill the session by ID when that token is lost. If the
provider cannot enforce this boundary, E2B browser preview stays disabled.
Provider cost requires a separate
operator decision; this ADR does not authorize paid allocation.

## Review, publication, and lifecycle

App Zero may show a review action only after the gateway has served the owner
the running candidate and the product records functional browser evidence tied
to the same owner, archive digest, immutable runtime build, installed OSS pin,
and preview session. The review record includes the candidate descriptor and
diff identity. Accepting that exact candidate authorizes a draft PR; rejection,
supersession, failed verification, or expiry invalidates the approval. The
publication step rechecks the archive digest and pinned source revision before
using a scoped source-control credential. GitHub required checks then evaluate
the draft PR; only the human owner merges. Preview evidence does not claim CI
success, and a draft PR is not required merely to view the candidate.

The owner can stop the preview. TTL, owner revocation, rejection,
supersession, and terminal publication also close gateway access and request
provider teardown. Capacity is released only after the provider confirms
termination or absence; cleanup uncertainty keeps the reservation and denies
new access. Gateway authorization reads the durable ledger and does not assume
process-local state or sticky sessions. Limits remain bounded per owner and
host, with product rate and cost policy layered on the same session identity.

## Alternatives considered

- Expose a raw Docker port or E2B URL: rejected because private source and
  preview cookies would bypass owner authorization and revocation.
- Proxy on the product's browser origin or disable frame protections: rejected
  because untrusted candidate scripts could inherit product origin authority.
- Reuse production OIDC or allow mock login: rejected because the candidate
  could capture a privileged token or conceal broken authentication.
- Start `app.host:app` with arbitrary candidate commands or install candidate
  dependencies at runtime: rejected because the archive would choose its own
  execution and network boundary after admission.
- Pass deployed-host operator secrets to the candidate or relax the deployed
  startup guard: rejected because either would erase the product trust boundary.
- Make AG2's coding sandbox or a new App Zero preview ledger the preview
  authority: rejected by ADR 0010 and the existing OSS lifecycle contract.
- Require a draft PR before browser review: rejected because the owner should
  inspect the candidate before authorizing source publication. Required CI
  still runs on the PR before merge.

## Consequences and reversibility

This decision adds a product-facing security boundary and a typed OSS preview
runtime identity. It allows a responsive pre-PR review without weakening the
offline check or GitHub merge gates. It also requires a gateway, disposable
OIDC issuer and Mongo database, a preview-safe App Zero host profile, fixed
product runtime image, browser tests, and a deliberate provider migration
before activation. Reversibility is **medium risk**: the
preview route can be disabled, but persisted runtime identities and product
review evidence need coordinated migration if their shape changes.

## Affected invariants and OSS boundary

The decision preserves the [OSS software design](../architecture/MOZAIKS_OSS_SOFTWARE_DESIGN.md)
rule that generated apps have deterministic contracts and explicit auth,
[ADR 0010](0010-agent-and-app-sandbox-execution-boundary.md)'s AG2/app-sandbox
ownership split, and the shared host and interface boundaries in
`docs/agent-engineering-contract.md`.
The OSS side is **keep OSS: foundational** for session identity, admission,
provider routing, and teardown. The App Zero owner policy, source-control
credential, product OIDC persona, gateway deployment, and approval UI remain
product-owned. This ADR **defers** [issue #411](https://github.com/BlocUnited-LLC/mozaiks/issues/411):
browser delivery does not redefine generated-app semantics or introduce a typed
decision ledger as a second authority. The verified canonical archive remains
the candidate input.

## Validation before activation

1. Prove the exact archive, dependency lock, OSS pin, fixed entrypoint, and
   runtime image/template build match before admission and again at approval.
   Render a marker from changed candidate UI through the browser and verify its
   asset digest belongs to that same archive and runtime receipt.
   Mismatches, stale descriptors, mutable tags, and missing receipts fail closed.
2. Prove the preview-safe product host starts without operator provider secrets
   while the deployed `app.host:app` guard still rejects missing production
   prerequisites. Verify disposable OIDC and Mongo isolation, deny production
   and provider access from candidate code, and record any product integration
   that the preview cannot exercise. Missing profile or isolation evidence
   blocks the browser URL and owner approval.
3. Run a real local Docker App Zero browser journey through the separate-origin
   gateway and disposable OIDC: login, HTTP actions, WebSocket traffic, private
   database state, owner review, rejection, stop, and TTL cleanup. Assert that
   no host port, provider URL, source-control token, production token, or
   cross-origin cookie is exposed.
4. Exercise unauthorized owners, ticket replay, origin spoofing, malicious
   redirects and paths, cross-session browser storage and service workers, lost
   gateway state, concurrent capacity, revocation, browser refresh, and
   interrupted provider teardown. Confirm access closes while uncertain cleanup
   retains its reservation.
5. Use fake E2B adapter tests for ingress-token handling and durable provider
   routing, including controller restart with a lost traffic token. Any live
   E2B build or session needs operator cost approval and the same
   browser/security evidence before that provider is enabled.

This ADR is a proposal only. It creates no route, image, provider allocation,
or activation by itself.
